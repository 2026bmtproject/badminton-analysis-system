# Commentary production integration

`CommentaryModule` joins the commentary components already owned by this
repository. It is registered but excluded from the default runner because a
generated rally normally uses three or four Gemini requests. Production usage is
therefore on demand: select one or more segment indices after the upstream match
analysis has completed. Explicit full-match mode remains available for export and
offline analysis.

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

All selected segments complete cheap validation before production providers are
opened. The CLI then reports the eligible count and an approximate request range of
`3 × eligible` through `4 × eligible`. This is a request-count estimate, not a price or
runtime guarantee.

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

On-demand output is deliberately separate:

```text
stages/commentary/segments/segment_007.json
stages/commentary/segments/segment_007.failure.json
```

`commentary-segment-v1` embeds the existing `CommentaryRally` plus identity,
tactical, commentary and non-secret runtime diagnostics. It is atomically replaced
only after that segment succeeds. Selected runs never overwrite `commentary.json` or
the whole-stage `status.json`; a failed regeneration therefore leaves an earlier
successful segment artifact intact. Only `--all` or runner `--with-commentary` owns
the canonical `commentary-rallies-v1` artifact and normal stage status.

## Configuration and execution

`config.yaml` may contain:

```yaml
commentary:
  model: gemini-3.8-flash
  timeout_seconds: 30
  tactical_generator_max_output_tokens: 8192
  tactical_reviewer_max_output_tokens: 4096
  commentator_max_output_tokens: 8192
  commentary_reviewer_max_output_tokens: 4096
```

`COMMENTARY_GEMINI_MODEL` overrides the configured model. Credentials continue
to use `GEMINI_API_KEY` or the existing root `gemini_api_key`; they are never
written to the artifact. The intended commands are:

```powershell
uv run python -m modules.runner matches/Kunlavut
uv run python -m modules.commentary matches/Kunlavut --segment 7
uv run python -m modules.commentary matches/Kunlavut --segment 7 --segment 12
uv run python -m modules.commentary matches/Kunlavut --all
uv run python -m modules.runner matches/Kunlavut --with-commentary
```

The default runner stops after the upstream analysis and prints a short on-demand
hint; it does not emit the expensive-run warning because it makes no Commentary
requests. `--segment` is repeatable, sorted and deduplicated. Supplying neither a
segment nor `--all` is rejected before provider creation. `--segment` and `--all`
are mutually exclusive.

Commentary progress counts eligible rallies rather than video frames and exposes
the active high-level phase: fact preparation, tactical generation/review,
Commentator generation/review and deterministic validation/artifact writing. A
skipped tactical review is reported explicitly. Full-match mode shows a stronger
cost/latency warning but has no interactive confirmation, so scripted workflows
remain possible.

Runner/UI rendering, subtitles, artifact-to-frontend wiring, ReID,
forehand/backhand, performance experiments, and multi-rally decomposition remain
outside this integration.
