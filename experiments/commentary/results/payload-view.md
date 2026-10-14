# Experiment B: generator view reduction (offline)

Reference behavior: merged PR #8, `98b7d73`. The experiment branch was
fast-forwarded to main `df8ba6a` (PR #9); `git diff 98b7d73 -- modules/commentary`
was empty. Experiment A remains a benchmark-only prototype, not production code.

```powershell
.\.venv\Scripts\python.exe -B -m experiments.commentary.commentary_payloads `
    --match matches/TTYvsASY --selection 1:a 92:b
```

Strict court policy applies. Mapping is the previously human-confirmed top=a /
bottom=b for segment 1 and top=b / bottom=a for segment 92. No temporary court
confirmation, provider, credentials, Gemini requests, prompt edits, token-budget
changes, reviewer-context changes or new artifact format are involved.

## View design and removal inventory

The baseline mirrors the exact production v2 generator payload expression. An
offline test intercepts the production boundary before any provider invocation
and compares its serialized bytes with the benchmark baseline.

The candidate is explicitly `generator_view_version=benchmark-generator-view-b1`,
with top-level `rally_view`, `optional_pattern_hints`, and `source_catalog`.
It is not a valid replacement CompactRallyFacts model and does not claim to be
one: the compact `schema_version` tag is removed and replaced by the view tag.
Production domain models, serializers and computation are untouched.

Categories: A = currently citable; B = necessary context; C = provenance or
diagnostics; D = detail not usable as v2 evidence; E = redundant representation.

| Fields | Category | Treatment and rationale |
|---|---|---|
| `stroke_type`; eligible court `depth_zone`, `width_zone` | A | Retained exactly, including values and citation IDs. |
| All events, players/null players, original indices, frame/time, confidence, ordering | B/C | Retained exactly; no low-confidence inclusion gate or local renumbering. |
| All warnings, pose/shuttle/court limitations and quality | B | Retained verbatim, including unavailable or cautious context. |
| Pose `body_center_image`, `left_arm_extension`, `right_arm_extension`, `body_extension`, `stance_width_ratio`, `shoulder_angle_deg`, `hitting_arm_candidate` | D | Removed only from candidate. No v2 resolver supports these geometry/arm measurements; they cannot authorize stroke-side, movement, identity or new tactical premises. |
| Shuttle `incoming_unit_vector`, `outgoing_unit_vector`, `incoming_direction`, `outgoing_direction` | D | Removed only from candidate. No v2 resolver authorizes directional or physical claims from them. |
| Pose source frame/delta, mean keypoint confidence, usable keypoint count, ID | B/C | Retained to preserve observation availability, alignment and quality context. |
| Shuttle frame window, coordinate space, sample/usable counts, ratio, ID | B/C | Retained; unknown/unusable observations are not relabeled as reliable. |
| Complete court object, including metrics and source metadata | A/B/C | Retained even when not eligible; no automatic trust promotion. |
| Full source catalog and deterministic hints | A/B/C | Retained exactly, including empty allowed-field lists and all hint provenance. Hints remain optional, not acceptance gates. |
| Compact schema tag | C/E | Replaced by benchmark view tag to avoid falsely labeling a reduced object as canonical CompactRallyFacts. |

Repeated event metadata, provenance, nullable optional structures and raw source
confidence are deliberately NOT compacted in this first candidate: retaining
them avoids implicit reconstruction conventions or weaker unknown semantics.
Score/server are absent from the production generator baseline already; the
candidate makes no additional score/context removal. No Python tactical summaries
are generated to replace observations.

The removed numeric geometry can influence an LLM as non-citable context even
though it cannot justify accepted premises. This experiment establishes current
grounding-policy completeness, NOT identical model behavior. A real comparison
must test whether its removal changes proposal choice or review quality. All
source limitations are retained to avoid removing the qualifications with the
geometry. Expanding v2 capabilities later requires revisiting this view.

## Checks and measurement interpretation

For each authoritative `_evidence_catalog` entry, the checker visits every
currently allowed field from `CAPABILITIES`. It requires the candidate's visible
object to expose the exact value, ID, player, original event index, source
confidence, compact position, and advertised allowed field; court source frame
and reliable quality are checked too. Missing or altered eligible fields fail
the experiment. Counts include both distinct eligible fact IDs and field pairs.
Zero eligible fields is reported as vacuously complete, not as positive coverage
evidence. Synthetic confirmed-court tests ensure the two zone capabilities are
tested even if real strict-policy inputs have no court facts.

Separate checks preserve ALL event metadata, court objects, hints, source catalog,
and the exact retained subset of pose/shuttle context. The candidate is built on
a deep copy. Canonical RallyFact, CompactRallyFacts and analysis hashes before and
after serialization must match; independent candidate serialization must produce
identical bytes. The original source readers and validators still reject malformed
tails, duplicate envelope keys and event/stroke correspondence errors.

Payload SHA-256 refers to the exact compact UTF-8 JSON bytes measured (no whitespace
padding, `ensure_ascii=False`). Canonical input hashes instead use sorted JSON
keys for deterministic object comparison. Section sizes sum serialized object
bodies for strokes/pose/shuttle/court and serialized lists for patterns/catalog;
they are approximate attribution, excluding some container keys, punctuation and
null placeholders. Do not sum them as exact total bytes or call bytes tokens.

The existing Experiment A batch-local wrapper is used only to avoid repeating the
full pose scan while preparing both real selections. Both serializers receive the
same original canonical objects. No performance optimization is promoted into
production and no reviewer serialization is changed.


## Measured result (2026-09-13)

| Segment | Baseline bytes | Candidate bytes | Reduction | Percent | Eligible facts/fields: baseline -> candidate |
|---|---:|---:|---:|---:|---|
| 1 | 13524 | 9435 | 4089 | 30.24% | 6/6 -> 6/6 |
| 92 | 21308 | 14945 | 6363 | 29.86% | 10/10 -> 10/10 |

Total: **34832 -> 24380 bytes**, saving **10452 bytes (30.01%)**. Missing eligible IDs/fields: none. Both coverage checks are 100%. Real strict-policy court counts are zero; confirmed-court coverage was checked synthetically.

Serialized source counts are unchanged: segment 1 = 10 strokes, 9 pose contexts, 10 shuttle contexts, 0 courts, 0 hints, 29 catalog entries; segment 92 = 14 strokes, 14 pose contexts, 14 shuttle contexts, 0 courts, 4 hints, 42 catalog entries. Weak/cautious/unknown raw observations remain in these counts.

| Section bytes (approximate) | Segment 1 baseline -> candidate | Segment 92 baseline -> candidate |
|---|---:|---:|
| strokes | 1807 -> 1807 | 2505 -> 2505 |
| pose | 4625 -> 2291 | 7404 -> 3625 |
| shuttle | 4284 -> 2525 | 6183 -> 3595 |
| court | 0 -> 0 | 0 -> 0 |
| patterns | 2 -> 2 | 1146 -> 1146 |
| source_catalog | 2060 -> 2060 | 3139 -> 3139 |
| rally_metadata | 193 -> 151 | 194 -> 152 |

Exact payload-byte SHA-256:

- Segment 1 baseline: `e0fcb22796c54ba23506564dbb45628cb665e937811bfb0a554a838dccd978f7`
- Segment 1 candidate: `ce2073b061eace83a29b97ea0b460124fcb29ba19f096a1f4296fff62306522e`
- Segment 92 baseline: `4514ac87e747ee58091ba865eaeae320139aa015f9e7ef8cda54c795a3bcd577`
- Segment 92 candidate: `61bf89f6ba1638137b42fca9181f1f7f83281c3ff826a68e49ed7389cfe300bc`

Canonical source hashes before/after both serializers were identical (and match the original Experiment A measurements):

- Segment 1 rally: `c0970f03f9f34436d4f154f3f7b32aa50f695922a3f2e9b6c099673bed3b5501`
- Segment 1 compact: `cb86e733cecd73303eb94a2c2ad24cdb3df8a06d831ba5926cbc0f2c84404708`
- Segment 1 analysis: `f8637c22f4de2f023634e6d1ded9c4936654e10b47be178fc0e388850e970595`
- Segment 92 rally: `f52f2a7528344daac415a6b2a4651f3825368fc0d4f199aeac3e3283c671bb63`
- Segment 92 compact: `0cdcf0407a7efde586e531faa2be279ab6f434e41a50099d686ba89061ca0858`
- Segment 92 analysis: `bd250f1c8402c2102c0915a6eacc7cfe671e43c0b824d617fa9f0d87d56e8275`

Source artifact fingerprints remained unchanged. Candidate generation was repeated independently with identical serialized bytes. The offline result is **PROMISING FOR REAL MODEL COMPARISON**: substantial measured byte reduction, exact source preservation and complete eligible coverage. This is not approval to replace the production serializer; the candidate view has a different envelope and removes non-citable visual detail that may influence model selection. Actual token/latency and semantic behavior remain unmeasured. No provider or Gemini calls occurred.

Validation: Experiment B focused tests **20 passed**; full suite **768 passed, 3 skipped** (existing opt-in real-model tests). Syntax/import checks passed without importing the Gemini SDK. Tracked diff and new-file whitespace checks passed. Production files and Experiment A implementation remain unchanged.

## Versioned rerun (2026-10-14)

Program commit: `ceb6eaf3b0ebc7d6e2386e91c9343c6695c6ef7d` (benchmark code and tests).
Production baseline: `0e6578a70b831a64dd9887e389031f9136a4531f` (PR #28 main).
Both strategies use that production code; the reuse wrapper / candidate view are
opt-in benchmark-only alternatives. Analysis sources were clean at execution.
Environment: Python 3.12.13, Windows-11-10.0.26200-SP0.
`uv.lock` SHA-256: `6ab5d46d6da2e3b10e0659e4ab1215e643a9553a44561c72819d0b207c7dfda4`.
Data version: `1f0d830dda95ea15942e6466ccdb32d341bbd917bccdd04e5359c67a227b85ad` (canonical digest of relative paths, sizes
and content SHA-256 in the source manifest below).

Settings: TTYvsASY, zero-based selections `1:a 92:b`, explicitly confirmed TOP
players a/b respectively; strict court policy, sequential baseline then reuse,
no disk-cache flush, no concurrency, no providers or model inference.

Historical September results above lack a saved complete input-content manifest
and exact benchmark-source revision. They remain historical observations; their
exact timings / bytes are not claimed to be reproducible from today's checkout.
The versioned rerun below supplies a new auditable reference. Source artifacts
are private local inputs, not repository fixtures; a fresh checkout can run the
synthetic tests, but reproducing real results requires the same artifact bytes.
Different artifacts or hardware require a new run; timings are not test thresholds.

Reproduce this historical run from its program commit in a prepared Python checkout.
That commit still uses the original `benchmarks.*` module paths below; the
current entry points are listed in [the experiment README](../README.md).

```powershell
git switch --detach ceb6eaf3b0ebc7d6e2386e91c9343c6695c6ef7d
uv sync --locked
New-Item -ItemType Directory -Force .local/benchmark-runs | Out-Null
uv run --no-sync python -B -m benchmarks.commentary_inputs --match matches/TTYvsASY --selection 1:a 92:b --strategy compare | Out-File -Encoding utf8 .local/benchmark-runs/inputs.json
uv run --no-sync python -B -m benchmarks.commentary_payloads --match matches/TTYvsASY --selection 1:a 92:b | Out-File -Encoding utf8 .local/benchmark-runs/payloads.json
uv run --no-sync pytest tests/test_commentary_input_benchmark.py tests/test_commentary_payload_benchmark.py -q
```

The CLI reports `provenance`: program commit, dirty-source flag, environment,
lockfile hash, selections, source content hashes and data version. Hashing runs
outside timed strategies. Raw run JSON stays in ignored `.local/`; only this
method, manifest and concise result summary are published. Size/mtime checks
protect ordinary input changes during the run; they do not guarantee detection
of adversarial timestamp-preserving rewrites.

The shared source manifest is recorded in [Experiment A](input-performance.md#versioned-rerun-2026-10-14); both runs have exactly the same data version.

| Segment | Baseline bytes | Candidate bytes | Reduction % | Missing eligible fields |
|---|---:|---:|---:|---:|
| 1 | 13524 | 9435 | 30.24 | 0 |
| 92 | 21308 | 14945 | 29.86 | 0 |

Total **34832 → 24380 bytes**,
reduction **30.01%**. Canonical inputs unchanged;
retained context and source catalog preserved; candidate serialization deterministic.
This demonstrates byte reduction and current evidence coverage, not token savings,
LLM latency or semantic equivalence. No provider calls occurred.

| Segment | Baseline payload SHA-256 | Candidate payload SHA-256 |
|---|---|---|
| 1 | `e0fcb22796c54ba23506564dbb45628cb665e937811bfb0a554a838dccd978f7` | `ce2073b061eace83a29b97ea0b460124fcb29ba19f096a1f4296fff62306522e` |
| 92 | `4514ac87e747ee58091ba865eaeae320139aa015f9e7ef8cda54c795a3bcd577` | `61bf89f6ba1638137b42fca9181f1f7f83281c3ff826a68e49ed7389cfe300bc` |

Versioned rerun validation: 34 benchmark tests passed; the combined benchmark,
runtime, observations and tactical-analyzer selection passed 236 tests. Existing
tests exercise the production contracts individually; these new tests additionally
compare baseline/reuse output identity and the candidate's exact visible evidence
against the authoritative production catalog and intercepted generator payload.
