# Commentary production integration

`CommentaryModule` is the production stage that joins the commentary components
already owned by this repository. It runs after `player_identity` and writes
`stages/commentary/commentary.json` through the normal artifact and checkpoint
system.

The runtime path is:

```text
stage artifacts + player_identity epoch
  -> RallyFact -> CompactRallyFacts
  -> TacticalObservation v2 generator -> one batch tactical review
  -> CommentaryPlan -> one Commentator request -> one batch commentary review
  -> deterministic event fallback / optional summary removal
  -> CommentaryRally[]
```

Each rally makes at most one request for each of the four model roles. A valid
empty tactical result continues to commentary. Provider, structured-response,
canonical-join, or coverage failures fail the stage and do not publish a partial
artifact.

## Identity and unsupported segments

`player_identity/identity.json` is a hard dependency. Each resolved court-end
epoch maps the current `top` and `bottom` court positions to stable identities
`a` and `b`. Serve-vote-bound identities correspond to scoreboard rows;
`hsv_default` remains an explicitly visual, match-local binding. Commentary selects
the unique epoch containing the segment and
passes that mapping through the existing `CourtPositionToPlayer` boundary. It
does not infer identity from scores, serves, event order, appearance, or prior
commentary. A segment absent from all resolved epochs is recorded in
`unsupported_segments` and is not generated. Overlapping epochs are treated as
an invalid artifact.

`a` and `b` are canonical scoreboard identities, `top` and `bottom` are current
court positions, and human display names remain a separate future presentation
concern.

Segments with multiple recovered `sub_scores` remain explicitly unsupported
because the current domain has no safe sub-rally event membership.

## Court, vision, highlight, and score

Pose, court detection, shuttle tracking, and highlight ranking are optional
dependencies. A court fact is trusted only when the existing artifact has
`confirmed: true`, meaning an operator confirmed the calibration, and the normal
homography, projection, pose/bbox quality, and court-bound checks also pass.
Confirmation never bypasses geometry validation. Unconfirmed or invalid court
data produces no trusted court zone.

Highlight remains contextual: `0.0` is preserved, absence becomes unknown, and
it never controls rally or stroke inclusion. Score is retained as segment
context by the deterministic builder but is withheld from outcome inference;
the stage adds no winner, scoring-cause, or server inference.

The current selected-segment pose reader validates the complete pose envelope.
Running it for every supported segment can repeatedly scan a large pose artifact.
This is known performance debt; this integration deliberately does not adopt the
experimental indexes, caches, or payload serializers.

## Artifact contract and staleness

The artifact envelope uses `schema_version: commentary-rallies-v1`. Stable
product records are the existing `CommentaryRally` objects. Every event persists
the original full-match event index, Python-derived frame/time, canonical `a/b`
player identity, reviewed or deterministic-fallback text, and fact provenance.
The summary is nullable. Diagnostic metadata contains model/token/call outcomes
and reviewer decisions, never API keys, prompts, raw provider responses, or full
vision artifacts.

The standard `BaseModule` status records fingerprints for every hard dependency
and every optional artifact present at generation time. Changes to identity,
strokes, court confirmation/data, highlight data, or other declared inputs make
the completed commentary stage stale. This repository currently has no general
code/config-version fingerprint mechanism, so prompt/model/config invalidation
requires an explicit forced run; the artifact records the effective non-secret
runtime configuration for diagnosis.

## Configuration and execution

`config.yaml` may contain:

```yaml
commentary:
  model: gemini-3.8-flash
  timeout_seconds: 30
  tactical_generator_max_output_tokens: 4096
  tactical_reviewer_max_output_tokens: 4096
  commentator_max_output_tokens: 4096
  commentary_reviewer_max_output_tokens: 4096
```

`COMMENTARY_GEMINI_MODEL` overrides the configured model. Credentials continue
to use `GEMINI_API_KEY` or the existing root `gemini_api_key`; they are never
written to the artifact. The normal command is:

```powershell
uv run python -m modules.runner matches/TTYvsASY
```

The runner has no global `--gpu` option. Existing compute stages select their
devices through their current interfaces. Commentary is also directly runnable
with `uv run python -m modules.commentary matches/TTYvsASY` after its dependencies
have completed.

Runner/UI rendering, subtitles, artifact-to-frontend wiring, ReID,
forehand/backhand, performance experiments, and multi-rally decomposition remain
outside this integration.
