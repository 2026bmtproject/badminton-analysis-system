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
from modules.common.config import GEMINI_API_KEY_ENV, get_gemini_api_key
from modules.common.downscale import pick_cached_scan_video
from modules.common.scores_io import write_scores
from modules.common.segments_io import read_segments
from modules.contracts import PIPELINE, resolve_input_video, stage_dir
from modules.score_recognition.recognizer import (
    ScoreRecognitionConfig,
    recognize_scores,
)

OUTPUT_FILENAME = PIPELINE["score_recognition"].output_filename


class ScoreRecognitionModule(BaseModule):
    """Second pipeline stage: read the scoreboard for every rally segment.

    Consumes ``match_segmentation``'s ``segments.json`` plus the raw match video,
    and writes ``stages/score_recognition/scores.json`` plus a ``status.json``.
    Frames are sampled straight from the source video per segment (no pre-cut
    clips needed).
    """

    name = "score_recognition"
    dependencies = PIPELINE["score_recognition"].dependencies  # ["match_segmentation"]

    def __init__(
        self,
        config: ScoreRecognitionConfig | None = None,
        input_video: str | None = None,
    ) -> None:
        self.config = config or ScoreRecognitionConfig()
        # Optional explicit input video (relative to project_path or absolute).
        self.input_video = input_video

    def _resolve_input_video(self, project_path: Path) -> Path:
        if self.input_video:
            candidate = Path(self.input_video)
            if not candidate.is_absolute():
                candidate = project_path / candidate
            if not candidate.is_file():
                raise FileNotFoundError(f"input video not found: {candidate}")
            return candidate
        return resolve_input_video(project_path)

    def _resolve_scan_video(self, project_path: Path, original: Path) -> Path:
        """Read frames from the lightest cached downscale, else the source.

        Reuses an existing ``cache/`` copy (>= ``min_scan_height`` and lower-res
        than the source) to decode fewer pixels; never generates one, so a bare
        cache just falls back to the original.
        """
        cached = pick_cached_scan_video(project_path, original, self.config.min_scan_height)
        return cached if cached is not None else original

    def _segments_path(self, project_path: Path) -> Path:
        dep = PIPELINE["match_segmentation"]
        return stage_dir(project_path, dep.name) / dep.output_filename

    def get_output_path(self, project_path) -> Path:
        return stage_dir(project_path, self.name) / OUTPUT_FILENAME

    def run(self, project_path, on_progress=None) -> Path:
        """Read scores for every segment and keep status.json up to date."""
        project_path = Path(project_path)
        out_dir = stage_dir(project_path, self.name)
        output_json = self.get_output_path(project_path)

        state = StageState(name=self.name, status=StageStatus.RUNNING, started_at=_now_iso())
        write_status(out_dir, state)

        try:
            api_key = get_gemini_api_key()
            if not api_key:
                raise RuntimeError(
                    f"no Gemini API key found: set ${GEMINI_API_KEY_ENV} or add "
                    f"'gemini_api_key' to config.yaml "
                    f"(get a key at https://aistudio.google.com/app/api-keys)"
                )

            original_video = self._resolve_input_video(project_path)
            scan_video = self._resolve_scan_video(project_path, original_video)
            segments = read_segments(self._segments_path(project_path))["segments"]
            output_json.parent.mkdir(parents=True, exist_ok=True)

            rallies, meta = recognize_scores(
                str(scan_video),
                segments,
                api_key,
                self.config,
                on_progress=on_progress,
            )
            try:
                meta["scan_video"] = str(scan_video.relative_to(project_path))
            except ValueError:
                meta["scan_video"] = str(scan_video)
            write_scores(output_json, rallies, self.config.model, extra=meta)

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
