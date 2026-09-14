# Production Commentator and CommentaryService v1

PR #9 completes the content-generation boundary for one selected rally:

```
RallyFact + CompactRallyFacts + optional accepted TacticalObservation[]
  -> deterministic CommentaryPlan
  -> one structured Commentator request
  -> local schema and exact coverage checks
  -> one batch semantic evidence review
  -> deterministic wording checks
  -> CommentaryRally + generation diagnostics
```

This PR does not register the commentary stage in the main runner and does not
write `commentary.json`. PR #10 owns runner, staleness and artifact integration.

## Deterministic plan and identity

`CommentaryService.generate()` accepts already-built canonical facts and requires
the caller's segment-specific `CourtPositionToPlayer`. The upstream builders are
still authoritative for converting `top`/`bottom` court position to the caller-
confirmed `a`/`b` identity. The service never infers identity from score, serve,
alternation or event order, and it does not implement side-swap handling or ReID.

Every compact event is included in chronological context with its original full-
match `event_index`, frame/time, nullable player/type/confidence, confidence band,
warnings and any reliable sampled court zone. Low/cautious/unknown observations
remain visible. An event is eligible for output when its player identity is known;
unknown-player events remain context-only. Every eligible event is required once,
including low-confidence events, and low/cautious current events require explicit
uncertainty wording. Zero eligible events returns an empty `CommentaryRally`
without a provider call.

Score context is deliberately withheld in v1. Segment-level final scores do not
prove rally winner, scoring cause, server, score transition, match point or game
result. Highlight is an optional contextual ranking score, explicitly labeled as
neither probability nor event evidence, and never changes event inclusion.

## Tactical observations

Tactical observations are optional. An empty list still produces ordinary per-
stroke commentary and a summary. Before a supplied `TacticalObservation` reaches
the prompt, Python resolves its claims again against the current compact rally and
requires the stable observation ID, canonical claims, player/event endpoints and
evidence IDs to match. A generated proposal or tampered/cross-rally observation is
rejected before the Commentator call.

Accepted observations remain `epistemic_status=model_interpretation`; semantic
review is not deterministic truth. Their IDs are recorded in the service result as
supplied input provenance. Artifact event/summary `source_fact_ids` remain canonical
stroke facts, so model interpretations are not silently promoted to deterministic
facts.

## Wire and validation

The model-authored response is intentionally narrow:

```json
{
  "events": [
    {"event_index": 670, "text": "一句繁體中文即時賽評。"},
    {"event_index": 671, "text": "一句繁體中文即時賽評。"}
  ],
  "summary": "一句回合總結。"
}
```

The wire schema is strict, rejects extras, coercion, blank/overlong text,
duplicate JSON keys, duplicate event indices and non-JSON numeric constants.
Python rejects missing/unknown events and canonicalizes response order using the
compact rally order. The model cannot author frame, time, segment, player, stroke
type, confidence or provenance. Python joins text onto `StrokeCommentaryEvent` and
`RallyCommentarySummary` using canonical source values.

The prompt forbids invented identity/type, winner, score/scoring cause, psychology,
forehand/backhand, speed/3D trajectory, continuous movement and concrete unsupported
purpose or causal mechanisms. A small deterministic language gate rejects explicit high-risk phrases;
this is defense in depth, not an exhaustive natural-language validator. After exact
event coverage succeeds, one provider-agnostic semantic reviewer receives every
generated event and the optional summary in one batch. For an event it sees only the
canonical player/type/confidence/limitations, the previous/current/next observation,
any trusted reliable court zone and overlapping accepted TacticalObservations. For a
summary it sees the canonical stroke sequence, trusted court zones, accepted
observations and rally limitations. The payload explicitly says whether trusted court
facts are available and never resends raw pose or shuttle data.

Every eligible event requires exactly one verdict. A `pass` retains the generated
line. A `reject` or `uncertain` verdict replaces that line with a deterministic safe
fallback built only from canonical player identity, the merged stroke label, an
optional trusted court zone and the confidence band. The rejected prose is never
reused. Per-event result diagnostics retain the original verdict, violation codes and
whether generated or fallback text was emitted. Commentary is allowed limited
broadcast interpretation: wording such as `抓準機會`, `順勢`, `展開進攻`, `變換節奏`,
`接續` or `調動` may pass. This is deliberately less
strict than TacticalObservation grounding. Since the production contract makes
`CommentaryRally.summary` nullable, a summary with a hard violation is omitted while
its verdict and violation codes remain in result diagnostics; any non-pass summary is
omitted and no summary fallback is generated. Missing, duplicate or
unknown verdicts, malformed/incomplete responses and provider failures fail closed.
There is no retry, repair or per-event review loop. A semantic review pass means only
that the reviewer found the prose consistent with supplied evidence; it does not turn
commentary into a canonical source fact.

The authoritative displayed stroke taxonomy is the eight-class merge in
`modules.common.bst.classes`. `小球` merges `放小球`/`擋小球`, `高遠球` merges
`挑球`/`長球`, `平快球` merges `平球`/`推球`, and service labels merge short/long
service. These names support shot-family wording, not claims that the player stood or
moved at the net/front/rear court. For natural commentary, `放小球` and `擋小球` are
allowed lexicalizations when the canonical source is `小球`; this wording alone adds no
court-position fact. The remaining information-adding fine distinctions are not recovered.
The Commentator prompt and semantic reviewer preserve this granularity. A deterministic
defense-in-depth check derives the hidden names from authoritative `BASE12_TO_8`, applies
the source-aware `小球` exception, and otherwise rejects complete fine labels rather than
individual characters or ordinary style verbs.
Existing provider timeout, structured output, usage/model metadata, single-attempt
behavior and incomplete-response diagnostics are reused unchanged.

Event review deliberately keeps only immediate chronology plus overlapping accepted
observations. Longer-range wording such as `再度` may therefore lack its earlier local
reference even when the complete source rally supports it. This is a known diagnostic
limitation for later work; PR #9 does not resend the full rally per event or add another
review call.

The service invokes the Commentator exactly once and the batch semantic reviewer once
when eligible output events exist, regardless of stroke count. It never invokes
TacticalObservation generation or review internally; PR #10 may orchestrate those
optional stages separately. With zero eligible events, neither provider is called.

## Opt-in real smoke

Default tests never call Gemini. A later explicit smoke can set
`COMMENTARY_COMMENTATOR_SMOKE=1` plus UTF-8 RallyFact/CompactRallyFacts paths,
explicit model, human-confirmed top player, credential and output path. Optional
accepted TacticalObservations may be supplied from a UTF-8 JSON list. The smoke
makes one Commentator request and one batch-review request and saves the exact raw
responses, final validated `CommentaryRally`, and safe diagnostics as separate UTF-8
files before relying on console rendering. No real Gemini call is made by this PR's
normal validation.
