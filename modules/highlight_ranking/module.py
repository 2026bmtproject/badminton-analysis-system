"""Stage adapter for the audio-only highlight ranking policy."""
from pathlib import Path

from modules.artifacts import read_records, write_artifact
from modules.base import BaseModule, StageResult
from modules.contracts import AudioSegmentSignals, PIPELINE, artifact_path
from modules.highlight_ranking.policy import policy_metadata, score_audio_signals


class HighlightRankingModule(BaseModule):
    name = "highlight_ranking"
    dependencies = PIPELINE[name].dependencies
    optional_dependencies = PIPELINE[name].optional_dependencies

    def check_ready(self, match_path) -> bool:
        return (super().check_ready(match_path)
                and artifact_path(match_path, "audio_highlight").is_file())

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        # BaseModule.run does not enforce readiness for standalone callers.
        if not self.check_ready(match_path):
            raise RuntimeError("highlight_ranking requires completed audio_highlight with audio_signals.json")
        rows = read_records(PIPELINE["audio_highlight"], artifact_path(match_path, "audio_highlight"))
        signals = []
        for index, row in enumerate(rows):
            try:
                signals.append(AudioSegmentSignals(**{
                    field: row[field] for field in AudioSegmentSignals.__dataclass_fields__
                }))
            except (KeyError, TypeError) as exc:
                raise ValueError(f"invalid audio signals record {index}: {exc}") from exc
        scores = score_audio_signals(signals)
        output = self.get_output_path(match_path)
        write_artifact(PIPELINE[self.name], scores, output, extra={"policy": policy_metadata()})
        return StageResult(output)
