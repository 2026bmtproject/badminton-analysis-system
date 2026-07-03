"""Pipeline-stage wrapper implementing the BaseModule interface."""

from __future__ import annotations

from pathlib import Path

from modules.base import (
    BaseModule,
    StageState,
    StageStatus,
    _now_iso,
    write_status,
)
from modules.common.segments_io import write_segments
from modules.contracts import PIPELINE, cache_dir, resolve_input_video, stage_dir
from modules.match_segmentation.segmenter import (
    SegmentationConfig,
    segment_video,
)

OUTPUT_FILENAME = PIPELINE["match_segmentation"].output_filename


class MatchSegmentationModule(BaseModule):
    """First pipeline stage: split a match video into candidate rally segments.

    Consumes the raw match video found under ``project_path`` and writes
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
        # Optional explicit input video (relative to project_path or absolute).
        self.input_video = input_video
        self.exclude_path = exclude_path

    def _resolve_input_video(self, project_path: Path) -> Path:
        if self.input_video:
            candidate = Path(self.input_video)
            if not candidate.is_absolute():
                candidate = project_path / candidate
            if not candidate.is_file():
                raise FileNotFoundError(f"input video not found: {candidate}")
            return candidate

        # Default: the raw video under matches/{match}/input/ (see contracts).
        return resolve_input_video(project_path)

    def check_ready(self, project_path) -> bool:
        """Ready when an input video exists (this stage has no dependencies)."""
        project_path = Path(project_path)
        try:
            self._resolve_input_video(project_path)
            return True
        except FileNotFoundError:
            return False

    def get_output_path(self, project_path) -> Path:
        return stage_dir(project_path, self.name) / OUTPUT_FILENAME

    def run(self, project_path, on_progress=None) -> Path:
        """Run segmentation, write the JSON, and keep status.json up to date."""
        project_path = Path(project_path)
        out_dir = stage_dir(project_path, self.name)
        output_json = self.get_output_path(project_path)

        state = StageState(name=self.name, status=StageStatus.RUNNING, started_at=_now_iso())
        write_status(out_dir, state)

        try:
            video_path = self._resolve_input_video(project_path)
            output_json.parent.mkdir(parents=True, exist_ok=True)

            result = segment_video(
                str(video_path),
                self.config,
                exclude_path=self.exclude_path,
                on_progress=on_progress,
                workdir=str(cache_dir(project_path)),  # shared downscale cache
            )
            write_segments(str(output_json), result.segments, result.fps)

            state.status = StageStatus.COMPLETED
            state.finished_at = _now_iso()
            state.output_path = str(output_json.relative_to(project_path))
            write_status(out_dir, state)
            return output_json
        except Exception as e:
            state.status = StageStatus.FAILED
            state.finished_at = _now_iso()
            state.error = str(e)
            write_status(out_dir, state)
            raise
