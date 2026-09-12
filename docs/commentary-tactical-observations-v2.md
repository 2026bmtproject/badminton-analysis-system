# TacticalObservation v2

V1 remains unchanged for compatibility and regression tests. Its closed tactic
selection and fixed descriptions cannot express novel grounded interpretations.
V2 adds an independent, versioned API with open Traditional Chinese prose:

`CompactRallyFacts -> generator -> deterministic grounding -> batch semantic reviewer -> TacticalObservation[]`

```python
from modules.commentary.analysis.observation_analyzer import analyze_tactical_observations

result = analyze_tactical_observations(
    generator=provider, reviewer=provider, compact_facts=compact,
    deterministic_analysis=analysis,  # optional; verified/recomputed locally
)
observations = result.observations
```

## Grounding and identity

The generator emits only `observation` (one concise Traditional Chinese sentence,
1–240 characters, nonblank) and 2–12 `supporting_claims` with `fact_id` and `field`.
At most five observations are permitted. Wire models forbid extras and coercion;
complete JSON parsing rejects duplicate keys, non-finite constants and malformed
responses. Language and single-sentence suitability are semantic-review duties;
length and nonblank checks are deterministic.

The resolver allows `stroke_type` on reliable strokes, and `depth_zone` /
`width_zone` on reliable court facts. These are source capabilities, not tactic
categories. Any new interpretation can use these fields without a Python change.
A new upstream observable field requires an explicit resolver and safety review.

Eligibility reuses PR #7's catalog: known player, supported upstream stroke type,
classifier confidence >= 0.70; court support additionally requires reliable court
quality. Unknown identity and low/unknown confidence remain context, never support.
Court geometry only describes sampled projected locations; no continuous path or
causal movement is supported. Pose/shuttle remain context-only; no stroke side or
physics resolver exists. Score/server/highlight are omitted, not tactical evidence.
Highlight is not a calibrated probability or globally comparable tactical signal.

Python reuses v1 input/catalog checks without changing PR B/V1 numerical behavior.
Every reference must exist in the selected rally and authorize that field. Claims
need at least two distinct events. Python derives values, players, event endpoints
and deduplicated evidence IDs. Original full-match event indices remain identities;
compact positions define chronology, including gapped/decreasing indices. No
`event_index + 1` assumption is used by v2 grounding or reviewer context.

Patterns are optional generator hints only, never inclusion vocabulary, acceptance
gates or authoritative chronology. Existing pattern helpers are unchanged; any
hint must be checked against underlying source observations. Zero patterns is legal.

Claims sort by `(compact position, fact ID, field)`. Stable IDs SHA-256 hash the
v2 version, segment, stripped observation and canonical resolved claims. Exact
duplicates collapse deterministically; genuinely different text with the same
evidence survives. Results sort by start/end compact positions then ID. This is
not semantic paraphrase deduplication.

## Minimal reviewer context

For EACH grounded candidate, send its text/ID, canonical supporting claims with
source value/frame, player, source classifier score and reliability, plus every hit
from its earliest through latest supporting position, inclusive, and one hit
before/after when available. Preserve all intervening weak/unknown hits. The row
fields are only position, original event index, player, stroke type and classifier
score. Window bounds and earlier/later-event flags prevent mistaking it for a
complete rally. A rally-spanning claim necessarily includes the whole hit span.

Do not send the full compact payload, patterns, pose, shuttle, scores, timing,
physical coordinates or unrelated outside events to the reviewer. Nearby context
can contradict wording (such as false consecutive-hit claims); it does not
authorize uncited factual premises. All candidates share ONE review request.

The reviewer judges every premise, including synonyms, hedging and implied claims,
against canonical evidence. It rejects unsupported identity/outcome/intent/
causality/psychology/motion/physics/stroke-side/type/tempo claims. Safety violation
codes are not badminton tactic categories. Candidate text is untrusted data, not
instructions. No rewriting or repair occurs. Pass/reject/uncertain decisions must
cover each candidate exactly once; only pass with no violations is accepted.

## Meaning of acceptance

Python alone attaches `grounding_status=validated`, `semantic_review=passed`, and
`epistemic_status=model_interpretation`. Generated schemas have no status fields.
Accepted objects retain canonical source claims, provenance, event/player references
and `grounding_checked_interpretation_not_proven`.

**`semantic_review=passed` does NOT mean the tactical interpretation has been
proven correct.** Python proves source correspondence, while a second LLM judgment
checks wording. Both model calls may share correlated mistakes, including missed
unsupported prose. This is not a deterministic proof or exhaustive tactical
taxonomy. Source classifier confidence remains labeled source metadata, never
tactical probability; v2 has no aggregate confidence, salience or fixed description.

## Calls, diagnostics and failures

Exactly one generator call, then zero or one batch reviewer call; no retries,
per-observation calls or rewrite loops. Existing provider timeout, single-attempt
SDK behavior and incomplete-response diagnostics are unchanged. Separate injected
FakeProviders support both roles offline; the same production provider can handle
both. SDK-neutral imports do not load Gemini. Each successful phase records its
prompt version, returned model, optional usage (including thoughts) and latency.
The caller owns explicit requested-model config, as in v1.

Result `schema_version=tactical-observations-v2` distinguishes:

- `generated_empty`: generator returned no candidates, reviewer skipped.
- `grounding_rejected`: all candidates failed source gates, reviewer skipped.
- `semantic_rejected`: grounded candidates existed, all rejected/uncertain.
- `accepted`: at least one accepted model interpretation.

Counts, indexed grounding rejection reasons and per-candidate review verdicts are
retained. Malformed/schema-invalid output or missing/duplicate/unknown review IDs
raises `ObservationAnalysisError`, not a successful empty result. Invalid compact
input retains v1 `TacticalAnalysisError`. Provider failures propagate the original
`ProviderError` with safe diagnostics (including partial-content presence); partial
responses never enter accepted observations. A failed review returns no partial
successful result. No prompts, credentials or partial generated text are added to
provider errors.

PR D can consume accepted interpretations as optional enrichment beside unchanged
deterministic strokes. They must not override source facts or suppress strokes.
Future orchestration may choose degradation on component failure. This PR has no
Commentator, Planner, runner/module registration, writer, UI or mapping config.

## Opt-in developer smoke

Default tests never call Gemini. Use a separate explicit v2 flag so a v1 opt-in
cannot accidentally trigger the additional billable stage. Model selection has no
silent default, matching v1 project policy. Prepare production compact facts with
human-confirmed segment mapping, then set:

```powershell
$env:COMMENTARY_GEMINI_V2_SMOKE = "1"
$env:COMMENTARY_COMPACT_FACTS = "C:\path\compact-rally.json"
$env:COMMENTARY_GEMINI_MODEL = "YOUR_EXPLICIT_MODEL_ID"
# GEMINI_API_KEY must already be present; never print it.
uv run pytest tests/test_commentary_observations_smoke.py -k test_real_gemini_observations_smoke -s
```

The v2 smoke explicitly configures **4096 max output tokens for the generator
and 4096 for the reviewer**. Thinking can consume this allowance. The shared
Gemini provider default remains 2048 for compatibility; callers selecting v2
should configure the intended phase budgets explicitly. Timeout, model selection,
thinking configuration and the single-attempt retry policy are unchanged.

Missing settings/credentials skip explicitly; invalid input path and real API/
schema errors fail. This performs at most one generator and one reviewer request,
prints phase latency/models/token usage, raw count, grounding rejections, verdict
counts and accepted observations. Incomplete responses print safe provider
diagnostics and still fail. Unset the v2 flag afterward. Unit fake-review tests
verify contract/gating, not the model's ability to detect every semantic violation;
a small manual real smoke remains necessary before assessing that behavior.

## Real smoke evidence and limits

TTYvsASY segment 92 was tested with `gemini-3.8-flash` and human-confirmed
`top=b, bottom=a` mapping. With both phase budgets at 4096, one generator and one
batch reviewer request completed with STOP and no retries. Four proposals passed
deterministic grounding; semantic review accepted three and rejected one with
`text_evidence_mismatch`. All three accepted observations combined stroke type
with cited court depth/width zones, describing sampled hitting positions without
continuous movement or causal claims. Generation took approximately 8.935 seconds
(12,464 input / 1,104 output / 2,062 thought tokens); review took 6.665 seconds
(3,984 input / 411 output / 2,014 thought tokens). These are single-run measurements,
not latency guarantees or a tactical accuracy benchmark.

The court run used **explicit temporary operator trust in memory only**. The
persisted calibration stayed `confirmed=false`; the production strict policy was
not changed. It produced 12 court facts, eight eligible for tactical evidence.
This validates the evidence path conditional on calibration trust, not projection
accuracy or automatic calibration verification. Earlier 2048-token phase runs
exhausted their output allowance and correctly failed closed with MAX_TOKENS.

Limited review context cannot positively establish an uncited earlier occurrence
(e.g. "again") or an exact whole-rally count. Such wording needs adequate cited
support or rejection/uncertainty; other candidates' evidence does not authorize
uncited premises. The earlier generated "again" sentence was not repeated in the
successful run, so its real-review behavior remains untested. Accepted objects
remain model interpretations, and semantic review is not a correctness proof.
