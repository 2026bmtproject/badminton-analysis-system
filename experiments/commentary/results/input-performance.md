# Commentary input performance experiment

Baseline: merged PR #8 (`98b7d73`). This branch contains offline development
instrumentation only; it does not replace production readers or change prompts,
grounding, reviewer context, thinking, token budgets or runner behavior.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -B -m experiments.commentary.commentary_inputs `
    --match matches/TTYvsASY --selection 1:a 92:b
```

Selections encode explicit segment-specific TOP identity. Here segment 1 uses
top=a/bottom=b and segment 92 uses top=b/bottom=a, as previously confirmed by
the operator. The experiment leaves court trust at the production **strict**
default; it does not reuse the earlier smoke's temporary calibration trust.
No provider is constructed and no API key is needed.

The parent runs baseline and reuse sequentially in separate child processes.
Each reports per-rally input/build time, per-reader time and physical read
counts, process peak RSS, selected pose retention, source size/mtime fingerprints,
full result hashes, and generator JSON payload byte size/hash. Peak RSS includes
imports and the entire strategy lifetime; it is not just retained cache memory.
Pose reader timing includes iterator consumption by the caller, not disk I/O
alone. First/repeated application reads are reported, but OS disk caches are not
flushed. A single trial cannot establish p95, true cold-disk performance or a
model-latency guarantee. JSON bytes are not token counts.

## Experiment A: batch-local artifact reuse

The prototype temporarily wraps reader references **inside the benchmark process**
using `BatchReads`; it is not production code and is not safe for concurrent
threads while patches are active. Original artifact readers, upstream adapters,
fact builders and deterministic analysis execute in both strategies.

Baseline performs the existing full reads per selection. Reuse reads non-pose
artifacts once and exhausts the original pose streaming reader once, retaining
only rows belonging to explicitly selected segments. It publishes cached pose
rows only after successful full envelope/tail validation. Each consumer gets an
independent deep copy. Size/mtime changes abort; source snapshots are compared
before/after each strategy and between strategies. These metadata fingerprints
detect ordinary file changes, not adversarial timestamp-preserving rewrites.

Reuse lifetime is one batch. There is no persistent cache, sidecar index, new
artifact format or implicit cross-match reuse. Memory scales with selected pose
rows and cached ordinary artifacts; large selection sets may need chunking in a
future production implementation. The first rally still pays full pose parsing.

Acceptance requires exact canonical JSON SHA-256 agreement for RallyFact,
CompactRallyFacts, deterministic analysis and the existing v2 generator payload
for every selection. Existing contract errors must remain errors. Unit tests
exercise malformed tails, source changes, copy isolation and nullable inputs.

## First local result (2026-09-12)

One sequential trial on TTYvsASY, selections `1:a 92:b`, strict court policy:

| Measurement | Baseline | Batch-local reuse |
|---|---:|---:|
| Total measured strategy time | 218.251 s | 111.041 s |
| Segment 1 input | 113.248 s | 110.327 s |
| Segment 92 input | 104.977 s | 0.689 s |
| Pose scans | 2 | 1 |
| Pose records visited | 200,956 | 100,478 |
| Pose records retained in reuse cache | none | 1,886 |
| Process peak working set | 587,751,424 bytes | 612,495,360 bytes |

The two-rally batch was **1.9655x faster**. Every RallyFact, CompactRallyFacts,
analysis and generator payload hash matched exactly, and source fingerprints
were unchanged. No Gemini calls were made. Both selections correctly produced
zero court facts under strict policy with the unconfirmed calibration. Payload
sizes were unchanged: 13,524 UTF-8 bytes for segment 1 and 21,308 for segment 92.
Fact building plus deterministic analysis took approximately 3–4 ms per rally.

The first-read cost is **not solved**: reuse still scans the full pose envelope.
This helps processing multiple selections in one batch; it is not a 1.97x claim
for a single rally or the full LLM flow. Peak memory increased by about 23.6 MiB.
Pose iterator consumption dominated baseline time (217.725 s); the measurement
does not separate disk access from Python parsing CPU. OS cache order and the
single-trial design limit stronger conclusions.

Copying cached ordinary artifacts has a measurable downside: total shuttle reader
time rose from 0.430 s to 1.388 s because the prototype deep-copies the full
18,683,588-byte shuttle envelope. Before productionizing, investigate retaining
only selected shuttle rows and explicit ownership without dropping existing
validation. Avoid simply removing copy isolation from this mutable prototype.
The pose artifact measured 156,225,886 bytes. Larger selection counts and memory
limits still need measurement; the current result covers only two segments.

## Next experiment: generator payload size

After documenting A, measure a separate candidate serializer that removes
uncitable pose/shuttle detail and redundant context while retaining every eligible
source field, all raw stroke observations (including weak/unknown), chronology
and necessary source limitations. First compare bytes and authoritative catalog
coverage offline. Do not change production prompts or send the candidate payload
to Gemini as part of this offline experiment.

Then, when explicitly authorized, compare a small fixed real-rally set using the
same model, mappings, trust settings, 4096/4096 budgets and no retries. Record
generation/review latency and input/output/thought tokens separately, plus raw,
grounding-rejected, semantic-rejected and accepted counts. Manually review source
coverage and forbidden-inference behavior; unchanged text is not expected from
stochastic generation. Alternate baseline/candidate execution order and use a
small predefined request limit. Do not introduce concurrency or thinking-budget
changes in that comparison. Only promote an optimization after both speed and
behavioral evidence support it.

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

| Artifact relative to match | Bytes | SHA-256 |
|---|---:|---|
| `stages/match_segmentation/segments.json` | 23144 | `8cab665339fd9fa344815de1c39176ce1bc2f0fd7cc3410dc70e1639edc319a9` |
| `stages/event_detection/events.json` | 49680 | `ba0823b474da9ce4d5851abc0384977877da0f630214e2d43a130cd0be29e829` |
| `stages/stroke_classification/strokes.json` | 254921 | `511d54152f775e6a268015fd5f241306007a64d6d0f968e7512c45e07d88130c` |
| `stages/score_recognition/scores.json` | 53881 | `a20843580473b7466a6d336d10df222117db1c7a625da688746c08d5e8dbaa11` |
| `stages/highlight_ranking/highlights.json` | 11925 | `f79aa70694bd27f8c07be2a75ab05b4c02038a18e1a66b18e882fb3def42e4e5` |
| `stages/pose/pose.json` | 156225886 | `c84bc4b98873a084e74e95405b9eb1871b20d107ac80ab5eb3cc0b93e388f51f` |
| `stages/court_detection/court.json` | 931 | `c727a334bef62a00ca11cc9977b9f2862dd27489c869d2cced45ec9a00d7872f` |
| `stages/shuttle_tracking/shuttle.json` | 18683588 | `bd78a7f0a19c368ebd92ab6947dc08ebce8d692275a291d4d5faa73321ff743d` |

| Measurement | Baseline | Batch-local reuse |
|---|---:|---:|
| Strategy time (s) | 106.69351489999099 | 53.283289700048044 |
| Peak working set (bytes) | 591212544 | 614395904 |
| Pose records visited | 200956 | 100478 |
| Pose records retained | 0 | 1886 |

Single two-selection run speedup: **2.0024x**. All RallyFact,
CompactRallyFacts, deterministic analysis and generator payload hashes agree.
Pose scans: 2 → 1. Source metadata stayed unchanged; no API calls occurred.

| Segment | Baseline input s | Reuse input s | Payload bytes (both) |
|---|---:|---:|---:|
| 1 | 53.672377 | 52.916657 | 13524 |
| 92 | 53.013371 | 0.359591 | 21308 |

Validation at the program commit: 34 benchmark tests passed; the combined
benchmark, runtime, observations and tactical-analyzer selection passed 236 tests.
No production reader, serializer or pipeline implementation is changed by this PR.
