"""Production orchestration for the grounded commentary pipeline."""

from __future__ import annotations

import math
import os
import json
import re
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable, Iterator

from modules.artifacts import read_artifact, write_artifact
from modules.base import BaseModule, StageResult, stage_completed
from modules.commentary.adapters.upstream import build_rally_fact_from_stages, read_commentary_inputs
from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.providers.base import LLMProvider, ProviderError, ProviderResponse
from modules.commentary.schemas import RallyFact
from modules.commentary.services import CommentaryService
from modules.common import console
from modules.common.config import load_config
from modules.common.progress import SmoothProgress
from modules.contracts import (
    PIPELINE, CommentaryRally, PlayerIdentityEpoch, Segment, artifact_path, stage_path,
)

ARTIFACT_VERSION = "commentary-rallies-v1"
SEGMENT_ARTIFACT_VERSION = "commentary-segment-v1"
DEFAULT_MODEL = "gemini-3.8-flash"
TACTICAL_GENERATOR_THINKING_LEVEL = "medium"
TACTICAL_REVIEWER_THINKING_LEVEL = "low"
COMMENTATOR_THINKING_LEVEL = "medium"
COMMENTARY_REVIEWER_THINKING_LEVEL = "low"
FAILURE_DIAGNOSTIC_VERSION = "commentary-provider-failure-v1"
FAILURE_DIAGNOSTIC_NAME = "failure_diagnostic.json"
_PROVIDER_PHASES = (
    "tactical_generation", "tactical_review",
    "commentary_generation", "commentary_review",
)


class CommentarySelectionError(ValueError):
    """Requested segment selection is empty or outside persisted segmentation."""


@dataclass(frozen=True)
class CommentaryJob:
    """One preflight-validated rally ready for the existing provider path."""

    segment_index: int
    epoch: PlayerIdentityEpoch
    mapping: CourtPositionToPlayer
    rally: RallyFact
    compact: CompactRallyFacts


@dataclass(frozen=True)
class GeneratedCommentaryJob:
    segment_index: int
    rally: CommentaryRally
    identity_diagnostic: dict
    tactical_diagnostic: dict
    commentary_diagnostic: dict


@dataclass(frozen=True)
class CommentaryBatchResult:
    """Programmatic result for selected or full-match commentary generation."""

    generated: list[GeneratedCommentaryJob]
    unsupported_segments: list[dict]
    artifact_paths: list[Path]
    provider_calls: int

    @property
    def rallies(self) -> list[CommentaryRally]:
        return [item.rally for item in self.generated]


class CommentaryProgress:
    """Compact request warning and one overall segment-level progress display."""

    def __init__(self, *, full_match: bool) -> None:
        self.full_match = full_match
        self._bar: SmoothProgress | None = None
        self._total = 0
        self._started = perf_counter()

    def preparing(self, segment_index: int) -> None:
        console.note(f"[commentary] segment {segment_index}: preparing canonical facts")

    def request_notice(self, *, requested: int, eligible: int, unsupported: int) -> None:
        title = "WARNING: FULL-MATCH COMMENTARY" if self.full_match else "COMMENTARY REQUEST"
        console.header(title)
        console.summary([
            ("requested segments", requested),
            ("eligible", eligible),
            ("unsupported", unsupported),
            ("estimated Gemini requests", f"approximately {eligible * 3}-{eligible * 4}"),
        ])
        if self.full_match:
            console.warn(
                "This will generate LLM commentary for every eligible rally and can "
                "take significant time and API tokens. Use --segment N when full-match "
                "output is not needed."
            )
        else:
            console.note(
                "Commentary typically uses 3-4 Gemini requests per generated rally; "
                "only the selected segments will be processed."
            )
        self._total = eligible
        if eligible:
            self._bar = SmoothProgress("Commentary", eligible)
            # A one-rally redirected run must still show 0/1 then 1/1. SmoothProgress
            # normally suppresses the zero line in logs, which would make a direct
            # completion look like a bar that never existed.
            self._bar.next_log = 0.0
            self._bar.update(0, force=True)

    def phase(self, segment_index: int, position: int, phase: str) -> None:
        label = phase.replace("_", " ")
        console.note(f"[commentary] segment {segment_index} "
                     f"({position}/{self._total}): {label}")

    def skipped(self, segment_index: int, position: int, phase: str, reason: str) -> None:
        self.phase(segment_index, position, f"{phase} skipped ({reason})")

    def completed(self, segment_index: int, position: int) -> None:
        self.phase(segment_index, position, "complete")
        if self._bar is not None:
            self._bar.update(position, force=True)

    def failed(self, segment_index: int, phase: str, diagnostic_path: Path) -> None:
        console.fail(
            f"commentary segment {segment_index} during {phase}\n"
            f"diagnostic: {diagnostic_path}"
        )

    def summary(self, *, generated: int, unsupported: int, provider_calls: int) -> None:
        console.section("Commentary complete")
        console.summary([
            ("generated", generated),
            ("unsupported", unsupported),
            ("provider calls", provider_calls),
            ("elapsed", console.duration(perf_counter() - self._started)),
        ])


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
        phase_callback: Callable[[str], None] | None = None,
        failure_callback: Callable[[str, Path], None] | None = None,
    ) -> None:
        self.provider = provider
        self.segment_index = segment_index
        self.phase = phase
        self.requested_model = requested_model
        self.failure_path = failure_path
        self.counters = counters
        self.phase_callback = phase_callback
        self.failure_callback = failure_callback

    def generate(
        self, *, system_prompt: str, user_prompt: str, response_schema,
    ) -> ProviderResponse:
        if self.phase_callback is not None:
            self.phase_callback(self.phase)
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
            if self.failure_callback is not None:
                self.failure_callback(self.phase, self.failure_path)
            raise
        self.counters.total_calls_completed += 1
        self.counters.calls_completed[self.phase] += 1
        return response


@dataclass(frozen=True)
class CommentaryModuleConfig:
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    tactical_generator_max_output_tokens: int = 8192
    tactical_reviewer_max_output_tokens: int = 4096
    commentator_max_output_tokens: int = 8192
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
                "tactical_generator": TACTICAL_GENERATOR_THINKING_LEVEL,
                "tactical_reviewer": TACTICAL_REVIEWER_THINKING_LEVEL,
                "commentator": COMMENTATOR_THINKING_LEVEL,
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
    draws_own_progress = True

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
                tactical_generator=open_provider(
                    config.tactical_generator_max_output_tokens,
                    thinking_level=TACTICAL_GENERATOR_THINKING_LEVEL,
                ),
                tactical_reviewer=open_provider(
                    config.tactical_reviewer_max_output_tokens,
                    thinking_level=TACTICAL_REVIEWER_THINKING_LEVEL,
                ),
                commentator=open_provider(
                    config.commentator_max_output_tokens,
                    thinking_level=COMMENTATOR_THINKING_LEVEL,
                ),
                commentary_reviewer=open_provider(
                    config.commentary_reviewer_max_output_tokens,
                    thinking_level=COMMENTARY_REVIEWER_THINKING_LEVEL,
                ),
            )

    def _prepare_jobs(
        self,
        match_path: Path,
        segment_indices: Iterable[int] | None,
        *,
        progress: CommentaryProgress | None,
    ) -> tuple[list[int], list[CommentaryJob], list[dict]]:
        """Perform every cheap/canonical validation before providers are opened."""

        segment_spec = PIPELINE["match_segmentation"]
        segment_envelope = read_artifact(segment_spec, artifact_path(match_path, segment_spec.name))
        segments = [Segment(**row) for row in segment_envelope[segment_spec.record_key]]
        if not segments:
            raise ValueError("invalid match_segmentation artifact: no segments")

        if segment_indices is None:
            requested = list(range(len(segments)))
        else:
            requested_values = list(segment_indices)
            if not requested_values:
                raise CommentarySelectionError(
                    "at least one commentary segment must be selected"
                )
            for index in requested_values:
                if type(index) is not int or not 0 <= index < len(segments):
                    raise CommentarySelectionError(
                        f"commentary segment_index {index!r} does not exist; "
                        f"valid range is 0-{len(segments) - 1}"
                    )
            requested = sorted(set(requested_values))

        epochs = read_identity_epochs(match_path)
        unsupported: list[dict] = []
        jobs: list[CommentaryJob] = []
        for segment_index in requested:
            if progress is not None:
                progress.preparing(segment_index)
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
            jobs.append(CommentaryJob(segment_index, epoch, mapping, rally, compact))
        return requested, jobs, unsupported

    @staticmethod
    def _segment_artifact_path(match_path: Path, segment_index: int) -> Path:
        return (
            stage_path(match_path, "commentary")
            / "segments"
            / f"segment_{segment_index:03d}.json"
        )

    @classmethod
    def _segment_failure_path(cls, match_path: Path, segment_index: int) -> Path:
        return cls._segment_artifact_path(match_path, segment_index).with_suffix(".failure.json")

    @staticmethod
    def _write_segment_artifact(
        path: Path,
        generated: GeneratedCommentaryJob,
        config: CommentaryModuleConfig,
    ) -> None:
        payload = {
            "schema_version": SEGMENT_ARTIFACT_VERSION,
            "segment_index": generated.segment_index,
            "rally": asdict(generated.rally),
            "identity": generated.identity_diagnostic,
            "tactical": generated.tactical_diagnostic,
            "commentary": generated.commentary_diagnostic,
            "runtime": config.public_metadata(),
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

    def _execute_jobs(
        self,
        match_path: Path,
        jobs: list[CommentaryJob],
        unsupported: list[dict],
        *,
        config: CommentaryModuleConfig,
        progress: CommentaryProgress | None,
        segment_scope: bool,
        write_segment_artifacts: bool = False,
        summarize: bool = True,
        on_progress=None,
    ) -> CommentaryBatchResult:
        counters = _ProviderRunCounters()
        generated_jobs: list[GeneratedCommentaryJob] = []
        artifact_paths: list[Path] = []

        if not jobs:
            if progress is not None and summarize:
                progress.summary(generated=0, unsupported=len(unsupported), provider_calls=0)
            return CommentaryBatchResult([], unsupported, [], 0)

        provider_context = self._provider_bundle(config)
        with provider_context as providers:
            for job_position, job in enumerate(jobs, start=1):
                assert providers is not None
                segment_index = job.segment_index
                failure_path = (
                    self._segment_failure_path(match_path, segment_index)
                    if segment_scope
                    else stage_path(match_path, self.name) / FAILURE_DIAGNOSTIC_NAME
                )
                if segment_scope:
                    failure_path.unlink(missing_ok=True)

                def report_phase(phase: str) -> None:
                    if progress is not None:
                        progress.phase(segment_index, job_position, phase)

                def report_failure(phase: str, path: Path) -> None:
                    if progress is not None:
                        progress.failed(segment_index, phase, path)

                observed = CommentaryProviders(*(
                    _ObservedProvider(
                        provider,
                        segment_index=segment_index,
                        phase=phase,
                        requested_model=config.model,
                        failure_path=failure_path,
                        counters=counters,
                        phase_callback=report_phase,
                        failure_callback=report_failure,
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
                tactical_reviews_before = counters.calls_started["tactical_review"]
                tactical = analyze_tactical_observations(
                    generator=observed.tactical_generator,
                    reviewer=observed.tactical_reviewer,
                    compact_facts=job.compact,
                )
                if (progress is not None
                        and counters.calls_started["tactical_review"] == tactical_reviews_before):
                    progress.skipped(
                        segment_index, job_position,
                        "tactical_review", "no grounded candidates",
                    )
                result = CommentaryService(
                    provider=observed.commentator,
                    reviewer=observed.commentary_reviewer,
                    requested_model=config.model,
                    reviewer_requested_model=config.model,
                ).generate(
                    rally_fact=job.rally,
                    compact_facts=job.compact,
                    court_position_to_player=job.mapping,
                    tactical_observations=tactical.observations,
                )
                if progress is not None:
                    progress.phase(
                        segment_index, job_position,
                        "deterministic_validation_artifact_write",
                    )
                generated = GeneratedCommentaryJob(
                    segment_index=segment_index,
                    rally=result.commentary,
                    identity_diagnostic={
                        "epoch_index": job.epoch.epoch_index,
                        "resolved_by": job.epoch.resolved_by,
                        "votes": job.epoch.votes,
                        "agreement": job.epoch.agreement,
                    },
                    tactical_diagnostic=tactical.model_dump(mode="json"),
                    commentary_diagnostic=result.model_dump(
                        mode="json", exclude={"commentary"}
                    ),
                )
                generated_jobs.append(generated)
                if write_segment_artifacts:
                    path = self._segment_artifact_path(match_path, segment_index)
                    self._write_segment_artifact(path, generated, config)
                    artifact_paths.append(path)
                counters.rallies_completed += 1
                if progress is not None:
                    progress.completed(segment_index, job_position)
                if on_progress:
                    on_progress(job_position / len(jobs))

        if progress is not None and summarize:
            progress.summary(
                generated=len(generated_jobs),
                unsupported=len(unsupported),
                provider_calls=counters.total_calls_completed,
            )
        return CommentaryBatchResult(
            generated_jobs, unsupported, artifact_paths, counters.total_calls_completed
        )

    def generate_segments(
        self,
        match_path: str | Path,
        segment_indices: int | Iterable[int],
        *,
        write: bool = True,
        progress: CommentaryProgress | None = None,
    ) -> CommentaryBatchResult:
        """Generate selected rallies without touching whole-match artifact/status."""

        match_path = Path(match_path)
        if not self.check_ready(match_path):
            raise RuntimeError("commentary upstream dependencies are not complete")
        indices = [segment_indices] if isinstance(segment_indices, int) else list(segment_indices)
        config = self.config or CommentaryModuleConfig.from_project()
        requested, jobs, unsupported = self._prepare_jobs(
            match_path, indices, progress=progress,
        )
        if progress is not None:
            progress.request_notice(
                requested=len(requested), eligible=len(jobs), unsupported=len(unsupported)
            )
        result = self._execute_jobs(
            match_path, jobs, unsupported,
            config=config, progress=progress,
            segment_scope=True,
            write_segment_artifacts=write,
        )
        if not write:
            return CommentaryBatchResult(
                result.generated, result.unsupported_segments, [], result.provider_calls
            )
        return result

    def _run(self, match_path: Path, *, on_progress=None) -> StageResult:
        config = self.config or CommentaryModuleConfig.from_project()
        progress = CommentaryProgress(full_match=True)
        failure_path = stage_path(match_path, self.name) / FAILURE_DIAGNOSTIC_NAME
        failure_path.unlink(missing_ok=True)
        requested, jobs, unsupported = self._prepare_jobs(
            match_path, None, progress=progress,
        )
        progress.request_notice(
            requested=len(requested), eligible=len(jobs), unsupported=len(unsupported)
        )
        batch = self._execute_jobs(
            match_path, jobs, unsupported,
            config=config, progress=progress,
            segment_scope=False, summarize=False, on_progress=on_progress,
        )

        rallies = batch.rallies
        diagnostics = [
            {
                "segment_index": item.segment_index,
                "identity": item.identity_diagnostic,
                "tactical": item.tactical_diagnostic,
                "commentary": item.commentary_diagnostic,
            }
            for item in batch.generated
        ]

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
        progress.summary(
            generated=len(batch.generated),
            unsupported=len(unsupported),
            provider_calls=batch.provider_calls,
        )
        return StageResult(output)


def generate_commentary_segments(
    match_path: str | Path,
    segment_indices: int | Iterable[int],
    *,
    config: CommentaryModuleConfig | None = None,
    providers: CommentaryProviders | None = None,
    write: bool = True,
    progress: CommentaryProgress | None = None,
) -> CommentaryBatchResult:
    """Public on-demand API; never owns whole-stage status or commentary.json."""

    return CommentaryModule(config=config, providers=providers).generate_segments(
        match_path, segment_indices, write=write, progress=progress,
    )
