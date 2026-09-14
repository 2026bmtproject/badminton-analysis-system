"""One structured Commentator request and deterministic canonical joining."""

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Literal

from modules.contracts import CommentaryRally, RallyCommentarySummary, StrokeCommentaryEvent
from modules.commentary.providers.base import LLMProvider, TokenUsage
from modules.commentary.schemas import FactId, NonNegativeFloat, NonNegativeInt, StrictModel
from modules.common.bst.classes import BASE12_TO_8

from .planner import CommentaryPlan
from .response import CommentatorResponse, parse_commentator_response
from .review_response import (
    CommentaryEventVerdict,
    CommentarySummaryVerdict,
    ReviewViolationCode,
)
from .reviewer import (
    CommentaryReviewMetadata,
    review_commentary,
)


COMMENTATOR_VERSION = "rally-commentator-v1"
PROMPT_PATH = Path(__file__).parents[1] / "prompts" / f"{COMMENTATOR_VERSION}.txt"


class CommentaryGenerationError(ValueError):
    """A complete provider response cannot safely satisfy the output contract."""


class CommentaryGenerationMetadata(StrictModel):
    prompt_version: Literal["rally-commentator-v1"] = COMMENTATOR_VERSION
    requested_model: str | None
    returned_model: str | None
    usage: TokenUsage | None
    latency_seconds: NonNegativeFloat
    provider_calls: Literal[0, 1]


class CommentaryEventOutputDiagnostic(StrictModel):
    event_index: NonNegativeInt
    reviewer_verdict: Literal["pass", "reject", "uncertain"]
    violation_codes: list[ReviewViolationCode]
    text_source: Literal["generated", "deterministic_fallback"]


class CommentaryGenerationResult(StrictModel):
    commentary: CommentaryRally
    eligible_event_indices: list[int]
    supplied_tactical_observation_ids: list[FactId]
    generation: CommentaryGenerationMetadata
    semantic_review: CommentaryReviewMetadata
    event_review_verdicts: list[CommentaryEventVerdict]
    event_output_diagnostics: list[CommentaryEventOutputDiagnostic]
    summary_review_verdict: CommentarySummaryVerdict | None
    summary_omitted_by_review: bool


_CAUTIOUS_WORDS = re.compile(r"可能|似乎|看似|看來|研判|辨識|疑似|或許")
_SENTENCE_END = re.compile(r"[。！？!?]+")
_HIDDEN_FINE_STROKE_LABELS = frozenset(
    fine for fine, merged in BASE12_TO_8.items() if fine != merged
)
_ALLOWED_COMMENTARY_LEXICALIZATIONS = {
    "小球": frozenset({"放小球", "擋小球"}),
}
_FORBIDDEN = {
    "outcome_or_score": ("贏得", "獲勝", "勝出", "致勝", "得分", "拿下這一分", "拿下一分",
                         "拿下此分", "比分", "比數", "賽末點", "局點", "最後一拍", "失誤"),
    "stroke_side": ("正手", "反手", "forehand", "backhand"),
    "motion_or_physics": ("球速", "速度", "公里", "km/h", "3D", "三維", "飛行軌跡", "落點",
                          "一路跑", "跑向", "跑動", "移動到", "衝向"),
    "psychology": ("緊張", "心態", "信心動搖", "心理"),
}

_DEPTH_WORDS = {"rear": "後場", "mid": "中場", "front": "前場"}
_WIDTH_WORDS = {"left": "左側", "center": "中央", "right": "右側"}


def _safe_event_fallback(event) -> str:
    """Format only canonical event fields; rejected generated prose is ignored."""
    location = ""
    if event.court_zone is not None:
        location = (
            f"於{_DEPTH_WORDS[event.court_zone.depth_zone]}"
            f"{_WIDTH_WORDS[event.court_zone.width_zone]}"
        )
    cautious = event.confidence_band != "reliable"
    if event.stroke_type is not None:
        if cautious:
            return f"球員 {event.player} 這拍可能{location}以{event.stroke_type}回擊。"
        return f"球員 {event.player} {location}以{event.stroke_type}回擊。"
    if cautious:
        return f"球員 {event.player} 這拍可能{location}完成一次擊球。"
    return f"球員 {event.player} {location}完成一次擊球。"


def _validate_text(
    text: str, *, cautious: bool, label: str,
    canonical_stroke_type: str | None = None,
) -> None:
    allowed = _ALLOWED_COMMENTARY_LEXICALIZATIONS.get(
        canonical_stroke_type, frozenset()
    )
    hidden = sorted(
        fine for fine in _HIDDEN_FINE_STROKE_LABELS
        if fine in text and fine not in allowed
    )
    if hidden:
        raise CommentaryGenerationError(
            f"{label} reconstructs unsupported hidden fine stroke class"
        )
    for category, phrases in _FORBIDDEN.items():
        if any(phrase.casefold() in text.casefold() for phrase in phrases):
            raise CommentaryGenerationError(f"{label} contains forbidden {category} wording")
    sentences = [part for part in _SENTENCE_END.split(text) if part.strip()]
    if len(sentences) > 1:
        raise CommentaryGenerationError(f"{label} must be one concise sentence")
    if cautious and not _CAUTIOUS_WORDS.search(text):
        raise CommentaryGenerationError(f"{label} requires cautious wording")
    if re.search(r"\d+\s*比\s*\d+", text):
        raise CommentaryGenerationError(f"{label} contains unsupported score wording")


def generate_commentary(*, provider: LLMProvider, reviewer: LLMProvider,
                        plan: CommentaryPlan, requested_model: str | None = None,
                        reviewer_requested_model: str | None = None) -> CommentaryGenerationResult:
    """Generate and batch-review one rally; provider errors propagate unchanged."""
    tactical_ids = [item.fact_id for item in plan.tactical_observations]
    if not plan.required_event_indices:
        return CommentaryGenerationResult(
            commentary=CommentaryRally(segment_index=plan.segment_index, events=[], summary=None),
            eligible_event_indices=[], supplied_tactical_observation_ids=tactical_ids,
            generation=CommentaryGenerationMetadata(
                requested_model=requested_model, returned_model=None, usage=None,
                latency_seconds=0.0, provider_calls=0,
            ),
            semantic_review=CommentaryReviewMetadata(
                requested_model=reviewer_requested_model, returned_model=None, usage=None,
                latency_seconds=0.0, provider_calls=0,
            ),
            event_review_verdicts=[], event_output_diagnostics=[], summary_review_verdict=None,
            summary_omitted_by_review=False,
        )
    payload = plan.model_dump(mode="json")
    started = perf_counter()
    response = provider.generate(
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        response_schema=CommentatorResponse,
    )
    latency = perf_counter() - started
    try:
        generated = parse_commentator_response(response.text)
    except ValueError as exc:
        raise CommentaryGenerationError("invalid structured Commentator response") from exc
    by_index = {event.event_index: event for event in generated.events}
    if set(by_index) != set(plan.required_event_indices) or len(generated.events) != len(plan.required_event_indices):
        raise CommentaryGenerationError("generated events must exactly cover eligible event indices")
    if plan.summary_requested != (generated.summary is not None):
        raise CommentaryGenerationError("generated summary presence does not match plan")
    reviewed, review_metadata = review_commentary(
        provider=reviewer, plan=plan, generated=generated,
        requested_model=reviewer_requested_model,
    )
    contexts = {event.event_index: event for event in plan.events}
    verdicts = {item.event_index: item for item in reviewed.events}
    output = []
    event_diagnostics = []
    for index in plan.required_event_indices:
        context = contexts[index]
        authored = by_index[index]
        verdict = verdicts[index]
        use_generated = verdict.verdict == "pass"
        text = authored.text if use_generated else _safe_event_fallback(context)
        _validate_text(
            text, cautious=context.confidence_band != "reliable",
            label=f"event {index}", canonical_stroke_type=context.stroke_type,
        )
        output.append(StrokeCommentaryEvent(
            segment_index=plan.segment_index, stroke_index=index,
            frame=context.frame, time_sec=context.time_sec, text=text,
            source_fact_ids=[context.source_fact_id],
        ))
        event_diagnostics.append(CommentaryEventOutputDiagnostic(
            event_index=index, reviewer_verdict=verdict.verdict,
            violation_codes=list(verdict.violation_codes),
            text_source="generated" if use_generated else "deterministic_fallback",
        ))
    summary = None
    summary_omitted = (
        generated.summary is not None
        and reviewed.summary.verdict != "pass"
    )
    if generated.summary is not None and not summary_omitted:
        _validate_text(
            generated.summary, cautious=False, label="summary",
            canonical_stroke_type=(
                "小球"
                if any(event.stroke_type == "小球" for event in contexts.values())
                else None
            ),
        )
        summary = RallyCommentarySummary(
            segment_index=plan.segment_index, text=generated.summary,
            source_fact_ids=[contexts[index].source_fact_id for index in plan.required_event_indices],
        )
    return CommentaryGenerationResult(
        commentary=CommentaryRally(plan.segment_index, output, summary),
        eligible_event_indices=list(plan.required_event_indices),
        supplied_tactical_observation_ids=tactical_ids,
        generation=CommentaryGenerationMetadata(
            requested_model=requested_model, returned_model=response.model,
            usage=response.usage, latency_seconds=latency, provider_calls=1,
        ),
        semantic_review=review_metadata,
        event_review_verdicts=reviewed.events,
        event_output_diagnostics=event_diagnostics,
        summary_review_verdict=reviewed.summary,
        summary_omitted_by_review=summary_omitted,
    )
