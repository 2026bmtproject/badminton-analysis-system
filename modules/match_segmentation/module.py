"""Pipeline-stage wrapper implementing the BaseModule interface."""

from __future__ import annotations

from pathlib import Path

from modules.base import BaseModule, StageResult
from modules.contracts import PIPELINE, artifact_path, cache_path, resolve_input_video
from modules.match_segmentation.segments import write_segments
from modules.match_segmentation.segmenter import (
    SegmentationConfig,
    segment_video,
)


class MatchSegmentationModule(BaseModule):
    """First pipeline stage: split a match video into candidate rally segments.

    Consumes the raw match video found under ``match_path`` and writes
    ``stages/match_segmentation/segments.json`` plus a ``status.json``.
    """

    name = "match_segmentation"
    dependencies = PIPELINE["match_segmentation"].dependencies  # [] — first stage

    def __init__(
        self,
        config: SegmentationConfig | None = None,
        input_video: str | None = None,
        exclude_path: str | None = None,
    ) -> None:
        self.config = config or SegmentationConfig()
        # Optional explicit input video (relative to match_path or absolute).
        self.input_video = input_video
        self.exclude_path = exclude_path

    def _resolve_input_video(self, match_path: Path) -> Path:
        return resolve_input_video(match_path, self.input_video)

    def check_ready(self, match_path) -> bool:
        """Ready when an input video exists (this stage has no dependencies)."""
        match_path = Path(match_path)
        try:
            self._resolve_input_video(match_path)
            return True
        except FileNotFoundError:
            return False

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        """Run segmentation and write the JSON."""
        output_json = self.get_output_path(match_path)
        video_path = self._resolve_input_video(match_path)
        output_json.parent.mkdir(parents=True, exist_ok=True)

        result = segment_video(
            str(video_path),
            self.config,
            exclude_path=self.exclude_path,
            on_progress=on_progress,
            workdir=str(cache_path(match_path)),  # shared downscale cache
        )
        write_segments(str(output_json), result.segments, result.fps)
        return StageResult(output_json)
