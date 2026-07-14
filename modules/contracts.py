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

    Court corners in image space plus the homography relating them to a top-down
    metric plane. Emitted per segment (camera may cut) or once globally when
    ``segment_index`` is None.

    **Direction matters:** ``homography`` maps **court metres -> image pixels**
    (``modules.court_detection.detector`` fits it that way, and ``project_16``
    uses it in that direction to draw the key points). Consumers that need to ask
    "where on the court is this pixel?" — ``pose`` picking the two players — must
    invert it first; see ``modules.pose.select.court_from_image``.
    """

    corners: list[list[float]]              # 4x [x, y], clockwise from top-left
    homography: list[list[float]]           # 3x3 court-metres -> image matrix
    segment_index: int | None = None


#: The COCO-17 keypoints RTMPose emits, in index order. ``PoseFrame.keypoints``
#: is aligned to this list, so a consumer can look a joint up by name.
COCO_KEYPOINTS = (
    "nose", "L_eye", "R_eye", "L_ear", "R_ear",
    "L_shoulder", "R_shoulder", "L_elbow", "R_elbow",
    "L_wrist", "R_wrist", "L_hip", "R_hip",
    "L_knee", "R_knee", "L_ankle", "R_ankle",
)

#: How ``pose`` names the two players. These are *court positions*, not identities:
#: "top" is the player in the far half of the court (smaller image y), "bottom" the
#: near one. Which is derivable from geometry alone, whereas an identity ("player a")
#: is not — so downstream stages key off these and map them to identities only where
#: they actually have that information (e.g. ``score_recognition``).
POSE_PLAYERS = ("top", "bottom")


@dataclass
class PoseFrame:
    """DRAFT — pose (RTMPose). Artifact: ``pose.json`` (key ``frames``).

    One record per (frame, player), like ``ShuttlePoint``. ``frame`` is an
    **absolute** index into the raw match video, and only frames inside a rally
    segment are emitted — dead time between rallies has no skeletons.

    ``keypoints`` is 17 x ``[x, y, score]`` in original-video pixels, aligned to
    :data:`COCO_KEYPOINTS`. It and ``bbox`` are None exactly when that player could
    not be found in the frame, which is the same convention as
    ``ShuttlePoint.x``/``y``: the record still exists, so consumers can iterate
    frames without consulting ``segments.json``.
    """

    frame: int
    segment_index: int
    player: str                             # one of POSE_PLAYERS
    keypoints: list[list[float]] | None     # 17x [x, y, score]; None if not found
    bbox: list[float] | None = None         # [x1, y1, x2, y2]; None if not found


#: The two trajectory-extraction methods ``shuttle_tracking`` runs over the same
#: TrackNet heatmaps. Both are written to ``shuttle.json``; ``event_detection``
#: consumes both, so this is not an either/or choice.
SHUTTLE_METHODS = ("inpaint", "viterbi")


@dataclass
class ShuttlePoint:
    """DRAFT — shuttle_tracking (TrackNetV3). Artifact: ``shuttle.json``
    (key ``points``).

    One record per (method, frame). ``frame`` is an **absolute** frame index into
    the raw match video — the same coordinate system as ``Segment.start_frame``
    and ``PoseFrame.frame`` — so downstream stages can align without consulting
    ``segments.json``. Only frames inside a segment are emitted; dead time
    between rallies is never tracked.

    ``x``/``y`` are in original-video pixels and are None exactly when
    ``visible`` is False. ``confidence`` is the heatmap peak backing the point
    (0 for interpolated/inpainted positions, None when the method cannot say).
    """

    frame: int
    segment_index: int
    method: str                             # one of SHUTTLE_METHODS
    x: float | None                         # None when not visible
    y: float | None
    visible: bool
    confidence: float | None = None


@dataclass
class HitEvent:
    """DRAFT — event_detection. Artifact: ``events.json`` (key ``events``).

    One record per hit, in frame order. ``frame`` is an **absolute** frame index into the
    raw match video, like ``ShuttlePoint.frame`` and ``PoseFrame.frame``.

    That single field is the whole contract, deliberately. The stage does derive a side
    for every hit — it has to, alternation between the two players is one of the signals
    it detects with — but ``stroke_classification`` reads the hitter straight out of BST's
    own 25-class head (``Top_*`` / ``Bottom_*``), so a ``player`` here would be a second,
    weaker answer to a question already answered downstream. Likewise a ``segment_index``:
    the frame is absolute, so which rally it falls in is a lookup in ``segments.json``, not
    a fact this stage gets to assert. The per-hit evidence that *was* used (side, source
    rule, signal measurements) is debugging material and goes to the CSVs behind
    ``--debug-csv``, not into the contract.
    """

    frame: int


@dataclass
class StrokeLabel:
    """DRAFT — stroke_classification (BST). Artifact: ``strokes.json`` (key ``strokes``).

    One record per :class:`HitEvent`, in the same order: ``event_index`` is the hit's
    position in ``events.json``, so the two artifacts zip together without a join.

    ``player`` is the half of the contract ``HitEvent`` deliberately leaves out. BST's
    25-class head answers "which stroke" and "who hit it" in one forward pass (``Top_*`` /
    ``Bottom_*``), so this is where the hitter is recorded for the whole pipeline — nothing
    upstream asserts it. It names a court position, not an identity, and matches
    :data:`POSE_PLAYERS`.

    ``segment_index`` *is* carried here, unlike in ``HitEvent``, because this stage has to
    resolve it anyway — BST's windows run between consecutive hits *of the same rally* — so
    writing it down asserts nothing new and saves every consumer from redoing the lookup.

    ``stroke_type`` is one of ``modules.common.bst.classes.CLASSES_8``, the Chinese
    8-class merge users are shown (小球, 高遠球, 殺球, ...). When the model's own answer is
    ``未知球種`` that is what is written, with ``player`` None: the stage reports what BST
    said rather than picking the best of the 24 real strokes, because a confident-looking
    label invented for a hit the model could not read is worse than an honest blank.
    """

    event_index: int
    frame: int                              # absolute, same as the HitEvent's
    segment_index: int
    player: str | None                      # one of POSE_PLAYERS; None for 未知球種
    stroke_type: str                        # one of CLASSES_8, or "未知球種"
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
    """Contract for one stage: what it depends on and what it emits.

    ``dependencies`` are hard: the stage cannot run without them, and ``check_ready``
    refuses until they are all completed. ``optional_dependencies`` are stages whose
    output the stage *uses if it is there* and works without otherwise. They still
    constrain the running order — an optional input that a full pipeline run happens to
    produce afterwards would never be picked up, which is exactly the kind of silent
    no-op this field exists to prevent — but they never block a run.
    """

    name: str
    description: str
    dependencies: list[str]
    output_filename: str
    record_type: type                       # the dataclass documenting a record
    record_key: str                         # envelope key holding the record list
    optional_dependencies: list[str] = field(default_factory=list)


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
        "pose", "骨架標記 (RTMPose)",
        ["match_segmentation", "court_detection"], "pose.json", PoseFrame, "frames",
    ),
    "event_detection": StageSpec(
        "event_detection", "擊球偵測",
        ["shuttle_tracking", "pose"], "events.json", HitEvent, "events",
        # scores.json only powers one precision rule (drop the warm-up and time-out
        # footage sitting between rallies at an unchanged score). Making it a hard
        # dependency would put a Gemini API key between the user and hit detection,
        # which is far too much to charge for one rule.
        optional_dependencies=["score_recognition"],
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


def ordering_dependencies(spec: StageSpec) -> list[str]:
    """Everything that must *run* before ``spec`` — hard and optional alike.

    Readiness asks a different question (see ``BaseModule.check_ready``) and looks only at
    ``spec.dependencies``.
    """
    return [*spec.dependencies, *spec.optional_dependencies]


def pipeline_order() -> list[str]:
    """Topological order of the full :data:`PIPELINE`."""
    return topological_order({n: ordering_dependencies(s) for n, s in PIPELINE.items()})
