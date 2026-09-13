"""Pipeline-stage wrapper implementing the BaseModule interface.

Runs in two phases, the same split ``shuttle_tracking`` uses and for the same reason:

1. **Detections.** For every rally segment, YOLOX finds every person in every frame and
   RTMPose gives a skeleton to each one who could plausibly be a player, cached under
   ``cache/pose/``. This is the expensive, GPU-bound phase; it is resumable per segment
   and skipped entirely when the cache is already valid — in which case no model is
   ever loaded.
2. **Selection.** The court homography picks the two players out of those candidates
   and writes them to ``pose.json``.

Keeping the split means the selection margins (which are heuristics, and will want
tuning against real footage) can be re-run for free, while the pass that costs the GPU
half an hour happens once.

Phase 1 runs several rallies at once, one worker thread each with its own decoder.
Decoding and the two models' pre-processing are CPU work that a single-threaded loop
spends the GPU's time waiting on — invisible on a modest card, and the whole reason a
faster one does not finish sooner. See :func:`default_workers`.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from modules.artifacts import read_segments, write_artifact
from modules.base import BaseModule, StageResult
from modules.common import console
from modules.common.video import iter_segment_frames, video_size
from modules.contracts import (
    PIPELINE,
    POSE_PLAYERS,
    PoseFrame,
    artifact_path,
    resolve_input_video,
)
from modules.pose import detection_cache
from modules.pose.select import (
    BAND_ESCAPE_TOLERANCE,
    PlayerTracker,
    SelectConfig,
    band_escape,
    build_static_anchors,
    candidate_margins,
    candidate_mask,
    read_image_to_court,
)

OUTPUT_FILENAME = PIPELINE["pose"].output_filename

#: Where the returns fell off on every card measured so far — past this the CPU work is
#: already hidden and the threads are only queueing for the same GPU. ``--workers``
#: exists because a faster card moves that point.
MAX_AUTO_WORKERS = 4

#: Frames between progress reports, which are taken under a lock.
PROGRESS_STRIDE = 32

ProgressFn = Callable[[float], None]


def default_workers(device: str) -> int:
    """How many rallies to decode and infer at once, when the caller did not say.

    One on the CPU, always: there is nothing to overlap with, because onnxruntime
    already has every core and a decoder thread would only take one away from it.
    """
    if not device.startswith("cuda"):
        return 1
    return max(1, min(MAX_AUTO_WORKERS, (os.cpu_count() or 2) // 2))


@dataclass
class PoseConfig:
    """Knobs for the pose stage.

    ``pose_mode`` trades accuracy for speed (``lightweight`` / ``balanced`` /
    ``performance``). ``device`` of None means "GPU if it genuinely works, otherwise
    CPU with a warning"; pass ``"cuda"`` to turn a missing GPU into an error instead.
    ``person_min_area`` drops detections smaller than that fraction of the frame, which
    is a cheap way to throw the crowd away before they ever reach RTMPose.
    ``workers`` of None lets :func:`default_workers` decide from the device.
    """

    pose_mode: str = "balanced"
    device: str | None = None            # None -> auto
    backend: str = "onnxruntime"
    person_min_area: float = 0.0
    select: SelectConfig = field(default_factory=SelectConfig)
    refresh_cache: bool = False
    workers: int | None = None           # None -> auto


class PoseModule(BaseModule):
    """Skeleton stage (RTMPose, top-down).

    Consumes ``match_segmentation``'s ``segments.json``, ``court_detection``'s
    ``court.json`` and the raw match video; writes ``stages/pose/pose.json`` and a
    ``status.json``, and leaves the per-segment detections in ``cache/pose/``.
    """

    name = "pose"
    dependencies = PIPELINE["pose"].dependencies  # [match_segmentation, court_detection]

    def __init__(self, config: PoseConfig | None = None) -> None:
        self.config = config or PoseConfig()

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def cache(self, match_path: Path, video: Path, image_to_court=None):
        return detection_cache.open_cache(
            match_path,
            detection_cache.build_params(
                pose_mode=self.config.pose_mode,
                person_min_area=self.config.person_min_area,
                candidate_margins=candidate_margins(self.config.select),
                video=video,
            ),
            court=(
                None if image_to_court is None
                else detection_cache.court_note(image_to_court)
            ),
        )

    # ---------------------------------------------------------------- phase 1
    def build_detections(
        self,
        match_path: Path,
        video: Path,
        segments: list[dict],
        image_to_court: np.ndarray,
        on_progress: ProgressFn | None = None,
    ) -> None:
        """Fill the detection cache, skipping segments that are already cached."""
        cache = self.cache(match_path, video, image_to_court)
        adopted = cache.migrate_legacy(segments)
        if adopted:
            console.field(
                "pose",
                f"adopted {console.count(adopted, 'segment')} from the pre-manifest cache",
            )
        # A moved court is not a question of taste: either the new band reaches
        # people the old one never posed, or it does not. See _court_verdict.
        status = detection_cache.court_status(cache, image_to_court)
        stale_court = False
        if status == "moved":
            stale_court, message = _court_verdict(
                detection_cache.cached_court(cache),
                image_to_court,
                video_size(str(video)),
                self.config.select,
            )
            console.field("court", message)
        elif status == "unknown":
            console.warn(
                "the court has moved since these detections were cached, and this\n"
                "cache predates recording which court that was — so whether the\n"
                "band still covers the people who were posed cannot be measured.\n"
                "If the court was repaired rather than nudged, rebuild it with:\n"
                f"  uv run python -m modules.pose {match_path} --refresh-cache"
            )

        plan = cache.plan(segments, force=self.config.refresh_cache or stale_court)
        notes = detection_cache.earned_notes(cache, plan, status)
        pending = plan.missing
        if not pending:
            cache.commit(plan, notes=notes)
            if on_progress:
                on_progress(1.0)
            return

        # Imported here so a run served entirely from cache never loads onnxruntime.
        from modules.pose.estimator import TwoStagePoseEstimator

        estimator = TwoStagePoseEstimator(
            pose_mode=self.config.pose_mode,
            device=self.config.device,
            backend=self.config.backend,
            person_min_area=self.config.person_min_area,
        )
        workers = self.config.workers or default_workers(estimator.device)
        workers = max(1, min(int(workers), len(pending)))
        console.field("device", estimator.device)
        console.field("RTMPose", f"{self.config.pose_mode} + YOLOX person detector")
        console.field(
            "frames",
            f"{console.count(len(pending), 'segment')} to compute, {len(plan.hits)} cached",
        )
        console.field(
            "workers", f"{console.count(workers, 'rally', 'rallies')} decoded at once"
        )

        # Only people who could conceivably be players get a skeleton; the crowd is
        # discarded between the two models. See select.candidate_mask.
        def keep(bboxes: np.ndarray) -> np.ndarray:
            return candidate_mask(bboxes, image_to_court, self.config.select)

        progress = _FrameCounter(
            total=sum(e.end_frame - e.start_frame + 1 for e in pending),
            on_progress=on_progress,
        )

        def compute(entry) -> None:
            detections = []
            for _, frame in iter_segment_frames(
                str(video), entry.start_frame, entry.end_frame
            ):
                detections.append(estimator(frame, keep=keep))
                progress.advance()
            detection_cache.save_segment(entry.path, detections)

        if workers == 1:
            for entry in pending:
                compute(entry)
        else:
            _run_concurrently(compute, pending, workers)

        # Only once every rally landed: the manifest records a *complete* pass. A run
        # that dies partway still leaves atomically-written entries for the next `plan`.
        cache.commit(plan, notes=notes)
        if on_progress:
            on_progress(1.0)

    # ---------------------------------------------------------------- phase 2
    def build_frames(
        self,
        match_path: Path,
        video: Path,
        segments: list[dict],
        image_to_court: np.ndarray,
        on_progress: ProgressFn | None = None,
    ) -> list[PoseFrame]:
        """Select the two players in every cached frame."""
        records: list[PoseFrame] = []

        # Load every segment once, up front: the static-anchor pass needs the whole match
        # to tell a fixture (umpire, line judge — present in a large fraction of frames,
        # never moving) from a player, and the selection loop then reuses the same
        # detections. See select.build_static_anchors.
        per_segment: list[list[dict]] = []
        for entry in self.cache(match_path, video, image_to_court).plan(segments).entries:
            if not entry.cached:
                raise RuntimeError(f"pose cache is missing {entry.label}: {entry.path}")
            per_segment.append(detection_cache.load_segment(entry.path))

        anchors = build_static_anchors(
            (det for detections in per_segment for det in detections),
            self.config.select,
        )
        tracker = PlayerTracker(image_to_court, self.config.select, anchors=anchors)
        for index, segment in enumerate(segments):
            detections = per_segment[index]
            start_frame = int(segment["start_frame"])
            # Rallies are not continuous with each other: where a player stood at the end
            # of the last one says nothing about where they start the next.
            tracker.reset()
            for offset, det in enumerate(detections):
                chosen = dict(zip(POSE_PLAYERS, tracker.update(det)))
                for player, person in chosen.items():
                    records.append(
                        _to_record(
                            det, person,
                            frame=start_frame + offset,
                            segment_index=index,
                            player=player,
                        )
                    )
            if on_progress:
                on_progress((index + 1) / len(segments))
        return records

    # -------------------------------------------------------------------- run
    def _run(
        self,
        match_path: Path,
        *,
        on_progress: Optional[ProgressFn] = None,
        only_detect: bool = False,
    ) -> StageResult:
        """Extract both players' skeletons through every rally segment.

        ``only_detect`` stops after the cache is filled — useful for doing the expensive
        GPU pass once and then iterating on the selection margins.
        """
        output_json = self.get_output_path(match_path)
        video = resolve_input_video(match_path)
        segments, _ = read_segments(match_path)
        image_to_court = read_image_to_court(match_path)

        # The GPU pass dominates the runtime, so it owns most of the progress bar.
        self.build_detections(
            match_path, video, segments, image_to_court,
            on_progress=(lambda f: on_progress(0.95 * f)) if on_progress else None,
        )
        if only_detect:
            # The cache is warm but the stage produced no artifact, so it is not
            # done — leaving it COMPLETED would make the runner skip it forever.
            console.note(f"pose cache ready; --only-detect wrote no {OUTPUT_FILENAME}")
            return StageResult(detection_cache.pose_dir(match_path), pending=True)

        records = self.build_frames(
            match_path, video, segments, image_to_court,
            on_progress=(lambda f: on_progress(0.95 + 0.05 * f)) if on_progress else None,
        )
        found = sum(r.keypoints is not None for r in records)
        write_artifact(
            PIPELINE["pose"],
            records,
            output_json,
            extra={
                "pose_mode": self.config.pose_mode,
                "x_margin": self.config.select.x_margin,
                "y_margin": self.config.select.y_margin,
                "players_found": found,
                "players_expected": len(records),
            },
        )
        console.field("players", f"found in {found}/{len(records)} (frame, player) slots")
        return StageResult(output_json)


def _court_verdict(
    previous: np.ndarray,
    image_to_court: np.ndarray,
    frame_size: tuple[int, int],
    config: SelectConfig,
) -> tuple[bool, str]:
    """Whether a moved court invalidates the cached detections, and what to say.

    The cache is exactly the people who passed the old candidate band, so the only
    thing that can have gone wrong is the repaired court letting a player stand
    somewhere that band never reached. ``band_escape`` measures precisely that, and on
    a real broadcast court it reads a clean zero for corners moved up to ~40 px and
    tens of percent for a court fitted to the wrong part of the frame — so the
    tolerance separates grid noise from a hole, not one judgement call from another.

    Above it, people who should now be selected have no skeleton at all and no amount
    of re-running the selection will invent them; the GPU pass has to happen, and the
    stage does it rather than asking.
    """
    escape = band_escape(previous, image_to_court, frame_size, config)
    if escape > BAND_ESCAPE_TOLERANCE:
        return True, (
            f"moved — {escape:.0%} of where a player can now be was never posed, "
            "recomputing detections"
        )
    return False, "moved, but everywhere a player can be was already posed; cache kept"


class _FrameCounter:
    """Frames finished across every worker, reported as one fraction.

    The count *and* the call are under the lock: ``on_progress`` redraws a terminal
    line, which two threads may not do at once.
    """

    def __init__(self, total: int, on_progress: ProgressFn | None) -> None:
        self.total = max(int(total), 1)
        self.on_progress = on_progress
        self._done = 0
        self._lock = threading.Lock()

    def advance(self) -> None:
        if self.on_progress is None:
            return
        with self._lock:
            self._done += 1
            if self._done % PROGRESS_STRIDE == 0:
                self.on_progress(self._done / self.total)


def _run_concurrently(work: Callable[[object], None], items: list, workers: int) -> None:
    """Run ``work`` over ``items`` in a thread pool, raising the first failure.

    Failing fast because the plausible failures — no VRAM, an unopenable video — will
    hit every remaining rally too, and fifty copies bury the one worth reading.
    """
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pose") as pool:
        futures = [pool.submit(work, item) for item in items]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def _to_record(
    det: dict,
    person: int | None,
    *,
    frame: int,
    segment_index: int,
    player: str,
) -> PoseFrame:
    """One contract record. ``person`` is None when that player was not found."""
    if person is None:
        return PoseFrame(
            frame=frame, segment_index=segment_index, player=player,
            keypoints=None, bbox=None,
        )
    kps, scores, bbox = det["kps"][person], det["scores"][person], det["bboxes"][person]
    return PoseFrame(
        frame=frame,
        segment_index=segment_index,
        player=player,
        keypoints=[
            [round(float(x), 2), round(float(y), 2), round(float(s), 4)]
            for (x, y), s in zip(kps, scores)
        ],
        bbox=[round(float(v), 2) for v in bbox],
    )
