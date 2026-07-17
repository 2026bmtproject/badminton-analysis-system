"""Pipeline-stage wrapper implementing the BaseModule interface."""

from __future__ import annotations

from pathlib import Path

from modules.base import BaseModule, StageResult
from modules.artifacts import read_segments, write_artifact
from modules.common.config import GEMINI_API_KEY_ENV, get_gemini_api_key
from modules.common.downscale import pick_cached_downscaled_video
from modules.contracts import PIPELINE, artifact_path, resolve_input_video
from modules.score_recognition.recognizer import (
    ScoreRecognitionConfig,
    recognize_scores,
)


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
        return resolve_input_video(match_path, self.input_video)

    def _resolve_downscaled_video(self, match_path: Path, original: Path) -> Path:
        """Read frames from the lightest cached downscale, else the source.

        Reuses an existing ``cache/`` copy (>= ``min_scan_height`` and lower-res
        than the source) to decode fewer pixels; never generates one, so a bare
        cache just falls back to the original.
        """
        cached = pick_cached_downscaled_video(match_path, original, self.config.min_scan_height)
        return cached if cached is not None else original

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        """Read scores for every segment."""
        output_json = self.get_output_path(match_path)
        api_key = get_gemini_api_key()
        if not api_key:
            raise RuntimeError(
                f"no Gemini API key found: set ${GEMINI_API_KEY_ENV} or add "
                f"'gemini_api_key' to config.yaml "
                f"(get a key at https://aistudio.google.com/app/api-keys)"
            )

        original_video = self._resolve_input_video(match_path)
        downscaled_video = self._resolve_downscaled_video(match_path, original_video)
        segments, fps = read_segments(match_path)
        output_json.parent.mkdir(parents=True, exist_ok=True)

        rallies, meta = recognize_scores(
            str(downscaled_video),
            segments,
            api_key,
            self.config,
            on_progress=on_progress,
            fps=fps,
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
        return StageResult(output_json)
