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
from modules.artifacts import read_segments, write_artifact
from modules.common.config import GEMINI_API_KEY_ENV, get_gemini_api_key
from modules.common.downscale import pick_cached_downscaled_video
from modules.contracts import PIPELINE, resolve_input_video, stage_path
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
    segments needed).
    """

    name = "score_recognition"
    dependencies = PIPELINE["score_recognition"].dependencies  # ["match_segmentation"]

    def __init__(
        self,
        config: ScoreRecognitionConfig | None = None,
        input_video: str | None = None,
    ) -> None:
        self.config = config or ScoreRecognitionConfig()
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

    def _resolve_downscaled_video(self, match_path: Path, original: Path) -> Path:
        """Read frames from the lightest cached downscale, else the source.

        Reuses an existing ``cache/`` copy (>= ``min_scan_height`` and lower-res
        than the source) to decode fewer pixels; never generates one, so a bare
        cache just falls back to the original.
        """
        cached = pick_cached_downscaled_video(match_path, original, self.config.min_scan_height)
        return cached if cached is not None else original

    def get_output_path(self, match_path) -> Path:
        return stage_path(match_path, self.name) / OUTPUT_FILENAME

    def run(self, match_path, on_progress=None) -> Path:
        """Read scores for every segment and keep status.json up to date."""
        match_path = Path(match_path)
        out_dir = stage_path(match_path, self.name)
        output_json = self.get_output_path(match_path)

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

            original_video = self._resolve_input_video(match_path)
            downscaled_video = self._resolve_downscaled_video(match_path, original_video)
            segments, _ = read_segments(match_path)
            output_json.parent.mkdir(parents=True, exist_ok=True)

            rallies, meta = recognize_scores(
                str(downscaled_video),
                segments,
                api_key,
                self.config,
                on_progress=on_progress,
            )
            try:
                meta["downscaled_video"] = str(downscaled_video.relative_to(match_path))
            except ValueError:
                meta["downscaled_video"] = str(downscaled_video)
            write_artifact(
                PIPELINE["score_recognition"],
                rallies,
                output_json,
                extra={"model": self.config.model, **meta},
            )

            state.status = StageStatus.COMPLETED
            state.finished_at = _now_iso()
            state.output_path = str(output_json.relative_to(match_path))
            write_status(out_dir, state)
            return output_json
        except Exception as e:
            state.status = StageStatus.FAILED
            state.finished_at = _now_iso()
            state.error = str(e)
            write_status(out_dir, state)
            raise
