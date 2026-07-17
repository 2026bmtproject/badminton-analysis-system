"""Pipeline-stage wrapper implementing the BaseModule interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from modules.base import (
    BaseModule,
    StageState,
    StageStatus,
    _now_iso,
    write_status,
)
from modules.artifacts import read_segments, write_artifact
from modules.common.frame_composite import composite_median, extract_frames_in_range
from modules.contracts import (
    PIPELINE,
    CourtCalibration,
    resolve_input_video,
    stage_path,
)
from modules.court_detection import detector
from modules.court_detection.interactive import (
    get_default_corners,
    recompute_from_corners,
)

OUTPUT_FILENAME = PIPELINE["court_detection"].output_filename

# A confirm callback takes (composite_image, 16 auto points, is_manual_mode) and
# returns the (possibly user-adjusted) 16 points. ``interactive.fine_tune`` fits
# this signature; the pipeline runner passes ``None`` to stay headless.
ConfirmCallback = Callable[[np.ndarray, list, bool], list]


@dataclass
class CourtDetectionConfig:
    """Knobs for the court_detection stage.

    ``num_segments`` longest rally segments are picked; ``frames_per_segment``
    frames are sampled from each and median-composited into a single clean court
    image (moving players average away) before detection.
    """

    num_segments: int = 3
    frames_per_segment: int = 20
    resize_width: int | None = None


class CourtDetectionModule(BaseModule):
    """Court-boundary stage: detect the court once from a clean composite image.

    Consumes ``match_segmentation``'s ``segments.json`` plus the raw match video.
    The longest ``num_segments`` segments are sampled, median-composited into one
    occlusion-free court image, and passed to the robust detector. An optional
    ``confirm`` callback (injected by the CLI) lets a user drag the four corners
    to fine-tune before the result is written; the pipeline runner leaves it
    ``None`` and takes the automatic corners. Writes
    ``stages/court_detection/court.json`` plus a ``status.json``.
    """

    name = "court_detection"
    dependencies = PIPELINE["court_detection"].dependencies  # ["match_segmentation"]

    def __init__(
        self,
        config: CourtDetectionConfig | None = None,
        input_video: str | None = None,
    ) -> None:
        self.config = config or CourtDetectionConfig()
        # Optional explicit input video (relative to match_path or absolute).
        self.input_video = input_video

    def _resolve_input_video(self, match_path: Path) -> Path:
        if self.input_video:
            candidate = Path(self.input_video)
            if not candidate.is_absolute():
                candidate = match_path / candidate
            if not candidate.is_file():
                raise FileNotFoundError(f"input video not found: {candidate}")
            return candidate
        return resolve_input_video(match_path)

    def get_output_path(self, match_path) -> Path:
        return stage_path(match_path, self.name) / OUTPUT_FILENAME

    def _pick_segments(self, segments: list[dict]) -> list[tuple[int, dict]]:
        """Pick the ``num_segments`` longest segments (most stable court view)."""
        def span(item: tuple[int, dict]) -> float:
            seg = item[1]
            if seg.get("duration_sec") is not None:
                return float(seg["duration_sec"])
            return float(seg["end_frame"] - seg["start_frame"])

        ordered = sorted(enumerate(segments), key=span, reverse=True)
        return ordered[: max(1, self.config.num_segments)]

    def _build_composite(self, video: Path, picked: list[dict]) -> np.ndarray:
        """Median-composite ``frames_per_segment`` frames from each picked segment."""
        frames: list[np.ndarray] = []
        for seg in picked:
            frames.extend(
                extract_frames_in_range(
                    str(video),
                    int(seg["start_frame"]),
                    int(seg["end_frame"]),
                    self.config.frames_per_segment,
                    resize_width=self.config.resize_width,
                )
            )
        if len(frames) < 3:
            raise RuntimeError(
                f"only {len(frames)} frames sampled from {len(picked)} segment(s); "
                f"need at least 3 to build a court composite"
            )
        return composite_median(frames)

    def run(
        self,
        match_path,
        on_progress: Optional[Callable[[float], None]] = None,
        confirm: Optional[ConfirmCallback] = None,
    ) -> Path:
        """Detect the court and keep status.json up to date.

        ``confirm`` (optional) receives the composite image, the 16 auto-detected
        points and a ``is_manual_mode`` flag, and returns the confirmed points.
        The pipeline runner never passes it (headless); the CLI passes
        ``interactive.fine_tune``.
        """
        match_path = Path(match_path)
        out_dir = stage_path(match_path, self.name)
        output_json = self.get_output_path(match_path)

        state = StageState(name=self.name, status=StageStatus.RUNNING, started_at=_now_iso())
        write_status(out_dir, state)

        try:
            video = self._resolve_input_video(match_path)
            segments, _ = read_segments(match_path)
            output_json.parent.mkdir(parents=True, exist_ok=True)

            if on_progress:
                on_progress(0.1)
            picked = self._pick_segments(segments)
            picked_idx = [idx for idx, _ in picked]
            composite = self._build_composite(video, [seg for _, seg in picked])

            if on_progress:
                on_progress(0.6)
            auto = detector.detect(composite)  # (16, 2) ndarray or None
            manual_mode = auto is None
            if manual_mode:
                # Detection failed: fall back to the image corners so the user
                # (or a later UI) can mark the court manually.
                pts = recompute_from_corners(get_default_corners(composite.shape))
            else:
                pts = [(float(x), float(y)) for x, y in auto]

            if confirm is not None:
                pts = confirm(composite, pts, manual_mode)

            if on_progress:
                on_progress(0.9)
            # detector emits corners as TL, TR, BL, BR; the CourtCalibration
            # contract wants them clockwise from top-left -> TL, TR, BR, BL.
            tl, tr, bl, br = pts[0], pts[1], pts[2], pts[3]
            corners_cw = [list(tl), list(tr), list(br), list(bl)]
            homography = detector.homography_from_corners(
                np.float32([tl, tr, bl, br])
            )
            if homography is None:
                raise RuntimeError("degenerate court corners: cannot build homography")

            record = CourtCalibration(
                corners=corners_cw,
                homography=homography.tolist(),
                segment_index=None,  # one global court across the picked segments
            )
            write_artifact(
                PIPELINE["court_detection"],
                [record],
                output_json,
                extra={
                    "segments_used": picked_idx,
                    "frames_per_segment": self.config.frames_per_segment,
                    "detection_failed": manual_mode,
                    "confirmed": confirm is not None,
                },
            )

            state.status = StageStatus.COMPLETED
            state.finished_at = _now_iso()
            state.output_path = str(output_json.relative_to(match_path))
            write_status(out_dir, state)
            if on_progress:
                on_progress(1.0)
            return output_json
        except Exception as e:
            state.status = StageStatus.FAILED
            state.finished_at = _now_iso()
            state.error = str(e)
            write_status(out_dir, state)
            raise
