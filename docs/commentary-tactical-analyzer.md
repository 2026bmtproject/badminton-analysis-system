# Tactical Analyzer v1 (PR C)

The production boundary is `CompactRallyFacts + RallyAnalysis -> one structured
provider request -> TacticalProposal[] -> deterministic evidence gates -> TacticalFact[]`.
Research tactical analysis, prompt, provider and JSON helpers informed this code;
their free-text acceptance and response repair behavior were not copied.

```python
from modules.commentary.analysis.tactical_analyzer import analyze_tactical_facts
from modules.commentary.providers.gemini import GeminiConfig, GeminiProvider

with GeminiProvider(GeminiConfig(model="YOUR_EXPLICIT_MODEL_ID")) as provider:
    result = analyze_tactical_facts(
        provider=provider, compact_facts=compact,
        deterministic_analysis=analysis,  # optional; computed when omitted
    )
tactical_facts = result.facts
```

Credentials follow the existing `GEMINI_API_KEY` / local `config.yaml`
`gemini_api_key` convention. Model selection is explicit. Timeout defaults to
30 seconds; SDK retry attempts are restricted to one, with no model fallback.
The Gemini adapter requests JSON schema output and returns SDK-neutral model and
token usage metadata when available. It rejects empty or incomplete responses
and sanitizes transport errors. `FakeProvider` records requests and supplies
fixed responses/errors for tests without credentials.

## Grounding boundary

The analyzer revalidates compact event identity, order and frame/time consistency,
and checks supplied analysis against the existing deterministic analyzer. Full-match
event indices remain unchanged. A canonical catalog binds every evidence ID to its
selected-rally event and player. Unknown references, range/order/player mismatches,
weak evidence and duplicate proposals are filtered with deterministic reason codes.
Malformed JSON, duplicate JSON keys, schema errors and wrong response rally raise
`TacticalAnalysisError`. Provider errors remain `ProviderError` with stable codes.

Compact event positions define chronology. Start/end event indices refer to the
first/last cited observations, not numeric minima/maxima; indices may decrease or
have gaps. Only the evidence gate has the rally context to validate temporal ranges.

V1 proposals contain only an allowed observation type, event bounds, players and
evidence IDs. They cannot contain model-authored prose or confidence. Accepted
descriptions come from fixed observation templates, so a keyword blacklist is not
the safety boundary. The broader PR A `GeneratedTacticalFact` remains a compatibility
schema; it is not the provider wire contract for v1.

The five supported types are notable stroke sequence, sustained attack, attack
transition, rear-to-front stroke-type transition and differences between observed
court depths. The three named stroke patterns require exact existing deterministic
pattern support. Generic sequences require at least two eligible observations.
Court observations require reliable geometry, distinct depth zones and the same
player. They do not establish a running path or causality.

Eligible support requires known player/type and classifier confidence >= 0.70,
reusing PR B's reliable band. Raw weaker observations stay in context. Pose and
shuttle are context only in v1. Court facts marked cautious cannot support claims.
`TacticalFact.confidence` is the minimum supporting classifier score, **not** tactical
truth probability. Salience is heuristic priority. Both limitations accompany
accepted facts. Grounding checks do not prove badminton semantic truth.

Stable IDs hash the observation type, source bounds, canonical players and ordered
evidence IDs. Proposal list order does not affect IDs; results use source order.
Score/server and highlight are unnecessary for this v1 payload. Highlight must never
serve as tactical evidence or calibrated excitement certainty.

The prompt explicitly prohibits invented identity/mapping, score, winner, scoring
cause, intent, psychology, continuous movement, shuttle speed/3D trajectory,
forehand/backhand, stroke types and timing. The structured contract has no channel
for these claims to become accepted text. Pose does not authorize stroke-side
inference; forehand/backhand remains experimental and excluded.

An empty result is valid. Component failures are explicit; future orchestration
may degrade to commentary without tactical facts. There is no Commentator,
Planner, service, runner registration or artifact writer in this PR.

## Optional developer smoke

Default tests use no API key and make no Gemini calls. To inspect 3–5 prepared
real rallies manually, repeat this opt-in test with a different compact input:

```powershell
$env:COMMENTARY_GEMINI_SMOKE = "1"
$env:COMMENTARY_COMPACT_FACTS = "C:\path\compact-rally.json"
$env:COMMENTARY_GEMINI_MODEL = "YOUR_EXPLICIT_MODEL_ID"
uv run pytest tests/test_commentary_tactical_smoke.py -q
```

This smoke test requires `GEMINI_API_KEY` explicitly (no local config key fallback).
Missing opt-in, model, input setting or credentials skips with a prerequisite reason;
a nonexistent input file fails clearly. Actual provider failures remain failures.
This test performs
one billable request, accepts an empty result, and writes no commentary artifact.
Unset `COMMENTARY_GEMINI_SMOKE` afterwards. The input is an already prepared
`CompactRallyFacts` JSON, not a new upstream artifact contract.
