"""Upstream stage adapter for frozen crowd-cheer measurements."""
from pathlib import Path

from modules.artifacts import read_segments, write_artifact
from modules.base import BaseModule, StageResult, artifact_fingerprint
from modules.contracts import PIPELINE, Segment, artifact_path, cache_path, resolve_input_video
from modules.audio_highlight.pipeline import infer_signals


class AudioHighlightModule(BaseModule):
    name = "audio_highlight"
    dependencies = PIPELINE[name].dependencies

    def __init__(self, input_video: str | None = None) -> None:
        self.input_video = input_video

    def check_ready(self, match_path) -> bool:
        if not super().check_ready(match_path):
            return False
        try:
            resolve_input_video(match_path, self.input_video)
        except FileNotFoundError:
            return False
        return artifact_path(match_path, "match_segmentation").is_file()

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        video = resolve_input_video(match_path, self.input_video)
        records, _ = read_segments(match_path)
        segments = [Segment(**{key: row[key] for key in Segment.__dataclass_fields__}) for row in records]
        signals, metadata = infer_signals(
            video, segments, cache_path(match_path) / "audio" / "audio.f32le",
            match_id=match_path.name, on_progress=on_progress,
        )
        stat = video.stat()
        metadata["source"] = {"filename": video.name, "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        metadata["segments_fingerprint"] = artifact_fingerprint(match_path, "match_segmentation")
        output = self.get_output_path(match_path)
        write_artifact(PIPELINE[self.name], signals, output, extra=metadata)
        return StageResult(output)
