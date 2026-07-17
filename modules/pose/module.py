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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from modules.artifacts import read_artifact, read_segments, write_artifact
from modules.base import BaseModule, StageResult
from modules.common.video import iter_segment_frames
from modules.contracts import (
    PIPELINE,
    POSE_PLAYERS,
    PoseFrame,
    resolve_input_video,
    stage_path,
)
from modules.pose import detection_cache
from modules.pose.select import (
    PlayerTracker,
    SelectConfig,
    candidate_margins,
    candidate_mask,
    court_from_image,
)

OUTPUT_FILENAME = PIPELINE["pose"].output_filename

ProgressFn = Callable[[float], None]


@dataclass
class PoseConfig:
    """Knobs for the pose stage.

    ``pose_mode`` trades accuracy for speed (``lightweight`` / ``balanced`` /
    ``performance``). ``device`` of None means "GPU if it genuinely works, otherwise
    CPU with a warning"; pass ``"cuda"`` to turn a missing GPU into an error instead.
    ``person_min_area`` drops detections smaller than that fraction of the frame, which
    is a cheap way to throw the crowd away before they ever reach RTMPose.
    """

    pose_mode: str = "balanced"
    device: str | None = None            # None -> auto
    backend: str = "onnxruntime"
    person_min_area: float = 0.0
    select: SelectConfig = field(default_factory=SelectConfig)
    refresh_cache: bool = False


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
        return stage_path(match_path, self.name) / OUTPUT_FILENAME

    def _read_court(self, match_path: Path) -> np.ndarray:
        """The image -> court-metres matrix, inverted from what court_detection stored."""
        dep = PIPELINE["court_detection"]
        envelope = read_artifact(dep, stage_path(match_path, dep.name) / dep.output_filename)
        courts = envelope[dep.record_key]
        if not courts:
            raise RuntimeError("no court in court_detection output")
        # court_detection emits one global court (segment_index None); if it ever starts
        # emitting one per segment, this is the line that has to grow a lookup.
        return court_from_image(courts[0]["homography"])

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
        meta = detection_cache.build_meta(
            pose_mode=self.config.pose_mode,
            person_min_area=self.config.person_min_area,
            candidate_margins=candidate_margins(self.config.select),
            video=video,
            segments=segments,
        )
        detection_cache.prepare(match_path, meta, force=self.config.refresh_cache)

        pending = [
            i for i in range(len(segments))
            if not detection_cache.segment_file(match_path, i).is_file()
        ]
        if not pending:
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
        print(f"  device:   {estimator.device}")
        print(f"  RTMPose:  {self.config.pose_mode} + YOLOX person detector")
        print(f"  frames:   {len(pending)} segment(s) to compute, "
              f"{len(segments) - len(pending)} cached")

        # Only people who could conceivably be players get a skeleton; the crowd is
        # discarded between the two models. See select.candidate_mask.
        def keep(bboxes: np.ndarray) -> np.ndarray:
            return candidate_mask(bboxes, image_to_court, self.config.select)

        total_frames = sum(
            int(segments[i]["end_frame"]) - int(segments[i]["start_frame"]) + 1
            for i in pending
        )
        done_frames = 0
        for index in pending:
            segment = segments[index]
            detections = []
            for _, frame in iter_segment_frames(
                str(video), int(segment["start_frame"]), int(segment["end_frame"])
            ):
                detections.append(estimator(frame, keep=keep))
                done_frames += 1
                if on_progress and done_frames % 32 == 0:
                    on_progress(done_frames / total_frames)
            detection_cache.save_segment(
                detection_cache.segment_file(match_path, index), detections
            )
            print(f"    seg{index:04d}: {len(detections)} frames")
        if on_progress:
            on_progress(1.0)

    # ---------------------------------------------------------------- phase 2
    def build_frames(
        self,
        match_path: Path,
        segments: list[dict],
        image_to_court: np.ndarray,
        on_progress: ProgressFn | None = None,
    ) -> list[PoseFrame]:
        """Select the two players in every cached frame."""
        records: list[PoseFrame] = []
        tracker = PlayerTracker(image_to_court, self.config.select)
        for index, segment in enumerate(segments):
            path = detection_cache.segment_file(match_path, index)
            if not path.is_file():
                raise RuntimeError(f"pose cache is missing seg{index:04d}: {path}")

            detections = detection_cache.load_segment(path)
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
        image_to_court = self._read_court(match_path)

        # The GPU pass dominates the runtime, so it owns most of the progress bar.
        self.build_detections(
            match_path, video, segments, image_to_court,
            on_progress=(lambda f: on_progress(0.95 * f)) if on_progress else None,
        )
        if only_detect:
            # The cache is warm but the stage produced no artifact, so it is not
            # done — leaving it COMPLETED would make the runner skip it forever.
            print(f"  pose cache ready; stage still PENDING (no {OUTPUT_FILENAME} yet)")
            return StageResult(detection_cache.pose_dir(match_path), pending=True)

        records = self.build_frames(
            match_path, segments, image_to_court,
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
        print(f"  players found in {found}/{len(records)} (frame, player) slots")
        return StageResult(output_json)


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
