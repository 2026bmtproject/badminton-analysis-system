"""Inter-stage data contracts and the pipeline dependency graph.

This module is the *single source of truth* for two things that the nine
analysis stages must agree on **before** they are individually built:

1. **Match layout.** A match lives under ``matches/{match}/`` which we call
   the *match path* (``match_path``). Inside it::

       matches/{match}/
       ├── input/                 # raw material (the broadcast video)
       │   └── match.mp4
       ├── cache/                 # shared derived media, rebuildable, safe to delete
       │   └── match_480p.mp4     # e.g. a downscaled video several stages reuse
       └── stages/                # one folder per stage, generated output
           └── {stage_name}/
               ├── status.json    # StageState (see modules.base)
               └── <output_file>  # the stage's data contract artifact

   ``input/`` is the untouched source, ``stages/{name}/`` holds contract
   artifacts (the JSON schemas below), and ``cache/`` holds derived media that
   is expensive to build but cheap to reproduce (a downscaled video, an
   extracted audio track, ...). Keyed by parameters so any stage can request
   and reuse one — see ``modules.common.downscale.downscaled_video``.

2. **Data contracts.** Each stage reads the artifacts of its dependencies and
   writes exactly one primary artifact (a JSON file) whose record shape is
   pinned by a dataclass below. Downstream stages are written against these
   dataclasses, so nailing them down now keeps the later wiring cheap.

Only ``match_segmentation`` is implemented today; the other eight schemas are
DRAFTs — refine the fields as each stage is built, but keep the file name,
dependency list, and envelope stable so the runner and consumers keep working.

Envelope convention (every artifact JSON is an object, never a bare array)::

    {"<record_key>": [ {<record fields>}, ... ], ...optional metadata... }

so producers can add top-level metadata (fps, model version, ...) without
breaking consumers. ``StageSpec.record_key`` names that list, and the generic
reader/writer in ``modules.artifacts`` uses it to serialize any stage's
artifact from its ``record_type`` dataclass — no per-stage I/O module needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Match layout
# --------------------------------------------------------------------------- #

INPUT_DIRNAME = "input"
STAGES_DIRNAME = "stages"
CACHE_DIRNAME = "cache"

# Container formats we accept as the raw match video, in priority order.
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi")


def input_path(match_path: str | Path) -> Path:
    """``matches/{match}/input`` — where the raw video lives."""
    return Path(match_path) / INPUT_DIRNAME


def stage_path(match_path: str | Path, stage_name: str) -> Path:
    """``matches/{match}/stages/{stage_name}`` — a stage's output folder."""
    return Path(match_path) / STAGES_DIRNAME / stage_name


def cache_path(match_path: str | Path) -> Path:
    """``matches/{match}/cache`` — shared, rebuildable derived media."""
    return Path(match_path) / CACHE_DIRNAME


def resolve_input_video(match_path: str | Path) -> Path:
    """Return the raw match video under ``input/``.

    Picks the first file (sorted) whose suffix is in :data:`VIDEO_EXTENSIONS`.
    Raises ``FileNotFoundError`` if the ``input/`` folder or a video is missing.
    """
    folder = input_path(match_path)
    if not folder.is_dir():
        raise FileNotFoundError(f"input folder not found: {folder}")
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix.lower() in VIDEO_EXTENSIONS:
            return entry
    raise FileNotFoundError(
        f"no input video ({', '.join(VIDEO_EXTENSIONS)}) under {folder}"
    )


# --------------------------------------------------------------------------- #
# Per-stage artifact record schemas (the "data contracts")
# --------------------------------------------------------------------------- #


@dataclass
class Segment:
    """One video segment (the slice for one candidate rally). Artifact:
    ``segments.json`` (key ``segments``).

    Envelope also carries top-level ``fps``. The frame->second derivation lives
    with its producer in ``modules.match_segmentation.segments``.
    """

    start_frame: int
    end_frame: int
    start_sec: float
    end_sec: float
    duration_sec: float


@dataclass
class RallyScore:
    """DRAFT — score_recognition. Artifact: ``scores.json`` (key ``rallies``).

    One record per segment, indexing back into ``segments.json`` by position.
    """

    segment_index: int
    score_a: int
    score_b: int
    server: str | None = None          # "a" | "b" | None if unknown
    game_index: int | None = None      # which game within the match


@dataclass
class CourtCalibration:
    """DRAFT — court_detection. Artifact: ``court.json`` (key ``courts``).

    Court corners in image space plus the homography to a top-down metric
    plane. Emitted per segment (camera may cut) or once globally when
    ``segment_index`` is None.
    """

    corners: list[list[float]]              # 4x [x, y], clockwise from top-left
    homography: list[list[float]]           # 3x3 image -> court-plane matrix
    segment_index: int | None = None


@dataclass
class PoseFrame:
    """DRAFT — pose (mmpose). Artifact: ``pose.json`` (key ``frames``).

    Per-frame skeletons. Heavy; a stage may instead write ``pose.npz`` and use
    this dataclass only to document the logical shape.
    """

    frame: int
    player: str                             # "a" | "b"
    keypoints: list[list[float]]            # Nx [x, y, confidence]


@dataclass
class ShuttlePoint:
    """DRAFT — shuttle_tracking (TrackNetV3). Artifact: ``shuttle.json``
    (key ``points``)."""

    frame: int
    x: float | None                         # None when not visible
    y: float | None
    visible: bool


@dataclass
class HitEvent:
    """DRAFT — event_detection. Artifact: ``events.json`` (key ``events``)."""

    frame: int
    player: str                             # "a" | "b"
    segment_index: int


@dataclass
class StrokeLabel:
    """DRAFT — stroke_classification (BST). Artifact: ``strokes.json``
    (key ``strokes``). One per HitEvent, aligned by ``event_index``."""

    event_index: int
    stroke_type: str                        # e.g. "clear", "smash", "net"
    confidence: float


@dataclass
class HighlightScore:
    """DRAFT — audio_highlight (YAMNet). Artifact: ``highlights.json``
    (key ``highlights``). Excitement score per segment."""

    segment_index: int
    score: float


@dataclass
class CommentaryLine:
    """DRAFT — commentary. Artifact: ``commentary.json`` (key ``lines``).
    Final narration keyed to a segment/time."""

    segment_index: int
    start_sec: float
    text: str


# --------------------------------------------------------------------------- #
# Pipeline DAG — dependencies + output artifact per stage
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StageSpec:
    """Contract for one stage: what it depends on and what it emits."""

    name: str
    description: str
    dependencies: list[str]
    output_filename: str
    record_type: type                       # the dataclass documenting a record
    record_key: str                         # envelope key holding the record list


PIPELINE: dict[str, StageSpec] = {
    "match_segmentation": StageSpec(
        "match_segmentation", "回合切割 — split the match into rally segments",
        [], "segments.json", Segment, "segments",
    ),
    "score_recognition": StageSpec(
        "score_recognition", "比分辨識 (Gemini API)",
        ["match_segmentation"], "scores.json", RallyScore, "rallies",
    ),
    "court_detection": StageSpec(
        "court_detection", "球場邊界辨識",
        ["match_segmentation"], "court.json", CourtCalibration, "courts",
    ),
    "shuttle_tracking": StageSpec(
        "shuttle_tracking", "羽球軌跡 (TrackNetV3)",
        ["match_segmentation"], "shuttle.json", ShuttlePoint, "points",
    ),
    "audio_highlight": StageSpec(
        "audio_highlight", "精彩片段偵測 (YAMNet)",
        ["match_segmentation"], "highlights.json", HighlightScore, "highlights",
    ),
    "pose": StageSpec(
        "pose", "骨架標記 (mmpose)",
        ["match_segmentation", "court_detection"], "pose.json", PoseFrame, "frames",
    ),
    "event_detection": StageSpec(
        "event_detection", "擊球偵測",
        ["shuttle_tracking", "pose"], "events.json", HitEvent, "events",
    ),
    "stroke_classification": StageSpec(
        "stroke_classification", "球種辨識 (BST)",
        ["event_detection", "pose", "shuttle_tracking"], "strokes.json", StrokeLabel, "strokes",
    ),
    "commentary": StageSpec(
        "commentary", "賽評生成",
        ["stroke_classification", "score_recognition", "audio_highlight"],
        "commentary.json", CommentaryLine, "lines",
    ),
}


def topological_order(dependencies: dict[str, list[str]]) -> list[str]:
    """Kahn's algorithm: return names so every dep precedes its dependents.

    ``dependencies`` maps name -> list of names it must follow. Dependencies on
    names outside the map are ignored (lets you sort a subset). Raises
    ``ValueError`` if the graph has a cycle.
    """
    names = set(dependencies)
    indegree = {n: 0 for n in names}
    dependents: dict[str, list[str]] = {n: [] for n in names}
    for n, deps in dependencies.items():
        for d in deps:
            if d in names:
                indegree[n] += 1
                dependents[d].append(n)

    ready = sorted(n for n, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in dependents[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                ready.append(m)
                ready.sort()

    if len(order) != len(names):
        cyclic = sorted(names - set(order))
        raise ValueError(f"dependency cycle among stages: {cyclic}")
    return order


def pipeline_order() -> list[str]:
    """Topological order of the full :data:`PIPELINE`."""
    return topological_order({n: s.dependencies for n, s in PIPELINE.items()})
