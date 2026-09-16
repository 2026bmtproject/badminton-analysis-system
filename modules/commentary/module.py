"""Production orchestration for the grounded commentary pipeline."""

from __future__ import annotations

import math
import os
import json
import re
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Iterator

from modules.artifacts import read_artifact, write_artifact
from modules.base import BaseModule, StageResult, stage_completed
from modules.commentary.adapters.upstream import build_rally_fact_from_stages, read_commentary_inputs
from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.providers.base import LLMProvider, ProviderError, ProviderResponse
from modules.commentary.services import CommentaryService
from modules.common.config import load_config
from modules.contracts import (
    PIPELINE, CommentaryRally, PlayerIdentityEpoch, Segment, artifact_path, stage_path,
)

ARTIFACT_VERSION = "commentary-rallies-v1"
DEFAULT_MODEL = "gemini-3.8-flash"
COMMENTARY_REVIEWER_THINKING_LEVEL = "low"
FAILURE_DIAGNOSTIC_VERSION = "commentary-provider-failure-v1"
FAILURE_DIAGNOSTIC_NAME = "failure_diagnostic.json"
_PROVIDER_PHASES = (
    "tactical_generation", "tactical_review",
    "commentary_generation", "commentary_review",
)


@dataclass
class _ProviderRunCounters:
    rallies_completed: int = 0
    total_calls_started: int = 0
    total_calls_completed: int = 0
    calls_started: dict[str, int] = field(
        default_factory=lambda: {phase: 0 for phase in _PROVIDER_PHASES}
    )
    calls_completed: dict[str, int] = field(
        default_factory=lambda: {phase: 0 for phase in _PROVIDER_PHASES}
    )


def _safe_provider_message(
    error: ProviderError, system_prompt: str, user_prompt: str,
) -> str:
    message = str(error)
    for request_text in (system_prompt, user_prompt):
        if request_text:
            message = message.replace(request_text, "[redacted request]")
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer|api[_ -]?key\s*[:=])\s*\S+",
        r"\1 [redacted]",
        message,
    )
    return message[:512]


def _write_provider_failure(
    path: Path, *, segment_index: int, phase: str, error: ProviderError,
    requested_model: str, latency_seconds: float, counters: _ProviderRunCounters,
    system_prompt: str, user_prompt: str,
) -> None:
    diagnostics = error.diagnostics
    usage = diagnostics.usage if diagnostics is not None else None
    payload = {
        "schema_version": FAILURE_DIAGNOSTIC_VERSION,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "segment_index": segment_index,
        "phase": phase,
        "error": {
            "code": error.code,
            "message": _safe_provider_message(error, system_prompt, user_prompt),
        },
        "provider": {
            "requested_model": (
                diagnostics.requested_model if diagnostics is not None
                else requested_model
            ),
            "returned_model": diagnostics.returned_model if diagnostics is not None else None,
            "finish_reason": diagnostics.finish_reason if diagnostics is not None else None,
            "finish_detail": diagnostics.finish_detail if diagnostics is not None else None,
            "usage": usage.model_dump(mode="json") if usage is not None else None,
            "candidate_count": diagnostics.candidate_count if diagnostics is not None else None,
            "text_present": diagnostics.text_present if diagnostics is not None else None,
            "parsed_content_present": (
                diagnostics.parsed_content_present if diagnostics is not None else None
            ),
            "latency_seconds": latency_seconds,
        },
        "call": {
            "overall_index": counters.total_calls_started,
            "phase_index": counters.calls_started[phase],
        },
        "completed": {
            "rallies": counters.rallies_completed,
            "total_provider_calls": counters.total_calls_completed,
            **counters.calls_completed,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class _ObservedProvider:
    """Attach safe rally/phase context while preserving ProviderError semantics."""

    def __init__(
        self, provider: LLMProvider, *, segment_index: int, phase: str,
        requested_model: str, failure_path: Path, counters: _ProviderRunCounters,
    ) -> None:
        self.provider = provider
        self.segment_index = segment_index
        self.phase = phase
        self.requested_model = requested_model
        self.failure_path = failure_path
        self.counters = counters

    def generate(
        self, *, system_prompt: str, user_prompt: str, response_schema,
    ) -> ProviderResponse:
        self.counters.total_calls_started += 1
        self.counters.calls_started[self.phase] += 1
        started = perf_counter()
        try:
            response = self.provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
            )
        except ProviderError as error:
            _write_provider_failure(
                self.failure_path,
                segment_index=self.segment_index,
                phase=self.phase,
                error=error,
                requested_model=self.requested_model,
                latency_seconds=perf_counter() - started,
                counters=self.counters,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            raise
        self.counters.total_calls_completed += 1
        self.counters.calls_completed[self.phase] += 1
        return response


@dataclass(frozen=True)
class CommentaryModuleConfig:
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    tactical_generator_max_output_tokens: int = 4096
    tactical_reviewer_max_output_tokens: int = 4096
    commentator_max_output_tokens: int = 4096
    commentary_reviewer_max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("commentary.model must be nonblank")
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds < 0.001):
            raise ValueError("commentary.timeout_seconds must be finite and at least 0.001")
        for name in (
            "tactical_generator_max_output_tokens", "tactical_reviewer_max_output_tokens",
            "commentator_max_output_tokens", "commentary_reviewer_max_output_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 32:
                raise ValueError(f"commentary.{name} must be an integer >= 32")

    @classmethod
    def from_project(cls) -> "CommentaryModuleConfig":
        root = load_config().get("commentary", {})
        if root is None:
            root = {}
        if not isinstance(root, dict):
            raise ValueError("config commentary must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(root) - allowed)
        if unknown:
            raise ValueError(f"unknown commentary config: {', '.join(unknown)}")
        values = dict(root)
        environment_model = os.environ.get("COMMENTARY_GEMINI_MODEL")
        if environment_model is not None:
            values["model"] = environment_model
        return cls(**values)

    def public_metadata(self) -> dict:
        return {
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": {
                "tactical_generator": self.tactical_generator_max_output_tokens,
                "tactical_reviewer": self.tactical_reviewer_max_output_tokens,
                "commentator": self.commentator_max_output_tokens,
                "commentary_reviewer": self.commentary_reviewer_max_output_tokens,
            },
            "thinking_level": {
                "tactical_generator": None,
                "tactical_reviewer": "low",
                "commentator": None,
                "commentary_reviewer": COMMENTARY_REVIEWER_THINKING_LEVEL,
            },
        }


@dataclass(frozen=True)
class CommentaryProviders:
    tactical_generator: LLMProvider
    tactical_reviewer: LLMProvider
    commentator: LLMProvider
    commentary_reviewer: LLMProvider


def _strict_identity_epoch(row: dict, index: int) -> PlayerIdentityEpoch:
    if not isinstance(row, dict):
        raise ValueError(f"invalid player_identity record {index}: expected an object")
    fields = PlayerIdentityEpoch.__dataclass_fields__
    if set(row) != set(fields):
        missing = sorted(set(fields) - set(row))
        extra = sorted(set(row) - set(fields))
        raise ValueError(f"invalid player_identity record {index}: missing={missing}, extra={extra}")
    integer_fields = ("epoch_index", "game_index", "first_segment", "last_segment", "votes")
    if any(type(row[name]) is not int or row[name] < 0 for name in integer_fields):
        raise ValueError(f"invalid player_identity record {index}: nonnegative integer required")
    if row["last_segment"] < row["first_segment"]:
        raise ValueError(f"invalid player_identity record {index}: reversed segment range")
    if {row["top"], row["bottom"]} != {"a", "b"}:
        raise ValueError(f"invalid player_identity record {index}: top/bottom must map a and b")
    agreement = row["agreement"]
    if (type(agreement) not in (int, float) or not math.isfinite(agreement)
            or not 0 <= agreement <= 1):
        raise ValueError(f"invalid player_identity record {index}: agreement must be in [0, 1]")
    if row["resolved_by"] not in {
        "vote", "convention", "hsv_fallback", "hsv_default",
    }:
        raise ValueError(f"invalid player_identity record {index}: unknown resolution policy")
    return PlayerIdentityEpoch(**row)


def read_identity_epochs(match_path: str | Path) -> list[PlayerIdentityEpoch]:
    spec = PIPELINE["player_identity"]
    envelope = read_artifact(spec, artifact_path(match_path, spec.name))
    epochs = [_strict_identity_epoch(row, index)
              for index, row in enumerate(envelope[spec.record_key])]
    ids = [epoch.epoch_index for epoch in epochs]
    if len(ids) != len(set(ids)):
        raise ValueError("invalid player_identity artifact: duplicate epoch_index")
    return epochs


def identity_mapping_for_segment(
    epochs: list[PlayerIdentityEpoch], segment_index: int,
) -> tuple[PlayerIdentityEpoch, CourtPositionToPlayer] | None:
    matches = [epoch for epoch in epochs
               if epoch.first_segment <= segment_index <= epoch.last_segment]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"invalid player_identity artifact: segment {segment_index} belongs to multiple epochs"
        )
    epoch = matches[0]
    return epoch, CourtPositionToPlayer(top=epoch.top, bottom=epoch.bottom)


class CommentaryModule(BaseModule):
    name = "commentary"
    dependencies = PIPELINE[name].dependencies
    optional_dependencies = PIPELINE[name].optional_dependencies

    def __init__(self, *, config: CommentaryModuleConfig | None = None,
                 providers: CommentaryProviders | None = None) -> None:
        self.config = config
        self.providers = providers

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def check_ready(self, match_path) -> bool:
        match_path = Path(match_path)
        return all(
            stage_completed(match_path, dependency)
            and artifact_path(match_path, dependency).is_file()
            for dependency in self.dependencies
        )

    @contextmanager
    def _provider_bundle(self, config: CommentaryModuleConfig) -> Iterator[CommentaryProviders]:
        if self.providers is not None:
            yield self.providers
            return
        # Keep normal runner/module imports SDK-neutral. Gemini is loaded only when
        # this stage actually executes with production providers.
        from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider

        with ExitStack() as stack:
            def open_provider(tokens: int, *, thinking_level=None) -> GeminiProvider:
                return stack.enter_context(GeminiProvider(GeminiConfig(
                    model=config.model,
                    timeout_seconds=config.timeout_seconds,
                    max_output_tokens=tokens,
                    thinking_level=thinking_level,
                )))

            yield CommentaryProviders(
                tactical_generator=open_provider(config.tactical_generator_max_output_tokens),
                tactical_reviewer=open_provider(
                    config.tactical_reviewer_max_output_tokens,
                    thinking_level="low",
                ),
                commentator=open_provider(config.commentator_max_output_tokens),
                commentary_reviewer=open_provider(
                    config.commentary_reviewer_max_output_tokens,
                    thinking_level=COMMENTARY_REVIEWER_THINKING_LEVEL,
                ),
            )

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        config = self.config or CommentaryModuleConfig.from_project()
        failure_path = stage_path(match_path, self.name) / FAILURE_DIAGNOSTIC_NAME
        failure_path.unlink(missing_ok=True)
        counters = _ProviderRunCounters()
        epochs = read_identity_epochs(match_path)
        segment_spec = PIPELINE["match_segmentation"]
        segment_envelope = read_artifact(segment_spec, artifact_path(match_path, segment_spec.name))
        segments = [Segment(**row) for row in segment_envelope[segment_spec.record_key]]
        if not segments:
            raise ValueError("invalid match_segmentation artifact: no segments")
        rallies: list[CommentaryRally] = []
        unsupported = []
        diagnostics = []
        jobs = []
        for segment_index, _segment in enumerate(segments):
            resolved = identity_mapping_for_segment(epochs, segment_index)
            if resolved is None:
                unsupported.append({"segment_index": segment_index,
                                    "reason": "player_identity_unavailable"})
                continue
            epoch, mapping = resolved
            try:
                stages = read_commentary_inputs(match_path, segment_index, mapping)
                rally = build_rally_fact_from_stages(
                    stages=stages, segment_index=segment_index,
                    court_position_to_player=mapping,
                )
            except ValueError as exc:
                if "cannot safely identify a sub-rally" not in str(exc):
                    raise
                unsupported.append({"segment_index": segment_index,
                                    "reason": "multiple_recovered_rallies_unsupported"})
                continue
            compact = build_compact_rally_facts(
                stages=stages, segment_index=segment_index,
                court_position_to_player=mapping,
            )
            jobs.append((segment_index, epoch, mapping, rally, compact))

        provider_context = self._provider_bundle(config) if jobs else nullcontext(None)
        with provider_context as providers:
            for job_position, (segment_index, epoch, mapping, rally, compact) in enumerate(jobs):
                assert providers is not None
                observed = CommentaryProviders(*(
                    _ObservedProvider(
                        provider,
                        segment_index=segment_index,
                        phase=phase,
                        requested_model=config.model,
                        failure_path=failure_path,
                        counters=counters,
                    )
                    for provider, phase in zip(
                        (
                            providers.tactical_generator, providers.tactical_reviewer,
                            providers.commentator, providers.commentary_reviewer,
                        ),
                        _PROVIDER_PHASES,
                        strict=True,
                    )
                ))
                tactical = analyze_tactical_observations(
                    generator=observed.tactical_generator,
                    reviewer=observed.tactical_reviewer,
                    compact_facts=compact,
                )
                result = CommentaryService(
                    provider=observed.commentator,
                    reviewer=observed.commentary_reviewer,
                    requested_model=config.model,
                    reviewer_requested_model=config.model,
                ).generate(
                    rally_fact=rally,
                    compact_facts=compact,
                    court_position_to_player=mapping,
                    tactical_observations=tactical.observations,
                )
                rallies.append(result.commentary)
                diagnostics.append({
                    "segment_index": segment_index,
                    "identity": {"epoch_index": epoch.epoch_index,
                                 "resolved_by": epoch.resolved_by,
                                 "votes": epoch.votes, "agreement": epoch.agreement},
                    "tactical": tactical.model_dump(mode="json"),
                    "commentary": result.model_dump(mode="json", exclude={"commentary"}),
                })
                counters.rallies_completed += 1
                if on_progress:
                    on_progress((job_position + 1) / max(1, len(jobs)))

        output = self.get_output_path(match_path)
        temporary = output.with_suffix(output.suffix + ".tmp")
        try:
            write_artifact(PIPELINE[self.name], rallies, temporary, extra={
                "schema_version": ARTIFACT_VERSION,
                "runtime": config.public_metadata(),
                "unsupported_segments": unsupported,
                "diagnostics": diagnostics,
            })
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return StageResult(output)
