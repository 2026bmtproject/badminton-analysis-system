"""Stage adapter for the serve_vote_v1 identity policy.

Cheap by construction: the work is arithmetic over two artifacts that already
exist, so a re-run costs milliseconds. That is what makes this a stage of its own
rather than a passenger on ``stroke_classification`` — the runner marks a stage
stale whenever an input moves under it, and ``score_recognition`` is precisely the
stage most likely to be re-read and re-tuned. Hanging that staleness on a stage
with no cache would buy a full BST pass over the match every time a score changed.
"""
from pathlib import Path
from typing import Protocol

from modules.artifacts import read_records, write_artifact
from modules.base import BaseModule, StageResult
from modules.common import console
from modules.contracts import PIPELINE, RallyScore, StrokeLabel, artifact_path
from modules.player_identity.policy import IdentityResult, infer_identity, policy_metadata
from modules.player_identity.visual import (
    HsvFallbackOutcome,
    HsvIdentityFallback,
    VisualFallbackUnavailable,
)


class VisualFallback(Protocol):
    def resolve(self, match_path: str | Path, primary: IdentityResult) -> HsvFallbackOutcome: ...


class PlayerIdentityModule(BaseModule):
    name = "player_identity"
    dependencies = PIPELINE[name].dependencies
    optional_dependencies = PIPELINE[name].optional_dependencies

    def __init__(
        self,
        use_dense: bool = True,
        use_visual: bool = True,
        visual_fallback: VisualFallback | None = None,
    ) -> None:
        #: The dense scan is a fallback, not an input: it recovers serves the stroke
        #: gate rejected. Absent or stale, the policy simply has fewer votes.
        self.use_dense = use_dense
        self.use_visual = use_visual
        self.visual_fallback = visual_fallback

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        strokes = _read(match_path, "stroke_classification", StrokeLabel)
        scores = _read(match_path, "score_recognition", RallyScore)

        dense = None
        if self.use_dense:
            from modules.player_identity.dense_serves import open_dense_serves
            dense = open_dense_serves(match_path)

        result = infer_identity(strokes, scores, dense=dense)
        visual = None
        # The primary policy always runs first. Constructing the fallback lazily is
        # significant: a fully resolved match does not even read pose/video.
        if self.use_visual and result.unresolved:
            fallback = self.visual_fallback or HsvIdentityFallback()
            try:
                outcome = fallback.resolve(match_path, result)
            except VisualFallbackUnavailable as exc:
                visual = {
                    "status": "unavailable",
                    "policy": "hsv_fallback_v1",
                    "reason": str(exc),
                }
            else:
                result, visual = outcome.result, outcome.metadata

        output = self.get_output_path(match_path)
        extra = {
            "policy": policy_metadata(),
            "convention": result.convention,
            "unresolved": result.unresolved,
            "dense_serves": dense.describe() if dense is not None else None,
        }
        if visual is not None:
            extra["visual_fallback"] = visual
        write_artifact(
            PIPELINE[self.name],
            result.epochs,
            output,
            extra=extra,
        )
        self._report(result)
        return StageResult(output)

    def _report(self, result: IdentityResult) -> None:
        console.field("convention", result.convention or "undetermined")
        for epoch in result.epochs:
            console.item(
                f"epoch {epoch.epoch_index} (game {epoch.game_index}, "
                f"segments {epoch.first_segment}-{epoch.last_segment}): "
                f"top={epoch.top} bottom={epoch.bottom} "
                f"[{epoch.votes} votes, {epoch.agreement:.0%}, {epoch.resolved_by}]"
            )
        if result.unresolved:
            console.field("unresolved", console.count(len(result.unresolved), "epoch"))
            for row in result.unresolved:
                console.item(
                    f"epoch {row['epoch_index']} (segments {row['first_segment']}-"
                    f"{row['last_segment']}): {row['votes']} votes "
                    f"(a={row['top_is_a']} b={row['top_is_b']}) — identity left unset"
                )


def _read(match_path: Path, stage: str, record_type: type) -> list:
    """Artifact records as their contract dataclass, naming the row that broke."""
    spec = PIPELINE[stage]
    rows = read_records(spec, artifact_path(match_path, stage))
    out = []
    for index, row in enumerate(rows):
        try:
            out.append(record_type(**{
                field: row[field] for field in record_type.__dataclass_fields__
                if field in row
            }))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid {stage} record {index}: {exc}") from exc
    return out
