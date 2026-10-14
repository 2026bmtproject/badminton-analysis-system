"""Offline experiment only: compare existing reads with batch-local reuse.

Run with python -m experiments.commentary.commentary_inputs --match matches/TTYvsASY
--selection 1:a 92:b. The suffix is the explicitly confirmed TOP player.
No provider is constructed; production adapters/builders remain unchanged.
"""

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import sys
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from modules.commentary.adapters import upstream, vision
from modules.commentary.analysis.rally_analyzer import analyze_rally
from modules.commentary.analysis.tactical_analyzer import _evidence_catalog
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.identity import CourtPositionToPlayer
from modules.contracts import PIPELINE, artifact_path


class BatchReads:
    """Single-thread experiment, bounded to explicit selections and one run.

    Uses the original readers, including complete pose-envelope validation.
    Copy-on-read prevents builders/callers mutating cached data. Fingerprint
    changes abort instead of returning stale data. No disk cache/index exists.
    """

    def __init__(self, segments, reuse):
        self.segments = set(segments)
        self.reuse = reuse
        self.artifacts, self.pose_rows, self.fingerprints = {}, {}, {}
        self.counts, self.seconds = defaultdict(int), defaultdict(float)
        self.pose_records_seen = self.pose_records_retained = 0
        self._artifact_reader = upstream.read_artifact
        self._pose_reader = vision.iter_records

    def _stamp(self, path):
        path = Path(path).resolve()
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self.reuse and path in self.fingerprints and self.fingerprints[path] != stamp:
            raise ValueError(f"artifact changed during batch: {path}")
        self.fingerprints[path] = stamp
        return path

    def read_artifact(self, spec, path):
        started = perf_counter()
        path = self._stamp(path)
        key = (path, spec.name)
        try:
            if not self.reuse or key not in self.artifacts:
                self.counts[spec.name] += 1
                value = self._artifact_reader(spec, path)
                self._stamp(path)
                if not self.reuse:
                    return value
                self.artifacts[key] = value
            return copy.deepcopy(self.artifacts[key])
        finally:
            self.seconds[spec.name] += perf_counter() - started

    def iter_records(self, path, key):
        started = perf_counter()
        path = self._stamp(path)
        cache_key = (path, key)
        try:
            if not self.reuse:
                self.counts["pose"] += 1
                for row in self._pose_reader(path, key):
                    self.pose_records_seen += 1
                    yield row
                return
            if cache_key not in self.pose_rows:
                self.counts["pose"] += 1
                selected = []
                for row in self._pose_reader(path, key):
                    self.pose_records_seen += 1
                    if row.get("segment_index") in self.segments:
                        selected.append(row)
                # Do not publish the cache before full tail validation succeeds.
                self._stamp(path)
                self.pose_rows[cache_key] = selected
                self.pose_records_retained += len(selected)
            for row in self.pose_rows[cache_key]:
                yield copy.deepcopy(row)
        finally:
            self.seconds["pose"] += perf_counter() - started

    def __enter__(self):
        self._stack = ExitStack()
        self._stack.enter_context(patch.object(upstream, "read_artifact", self.read_artifact))
        self._stack.enter_context(patch.object(vision, "read_artifact", self.read_artifact))
        self._stack.enter_context(patch.object(vision, "iter_records", self.iter_records))
        return self

    def __exit__(self, *args):
        return self._stack.__exit__(*args)


def peak_rss_bytes():
    """Process lifetime peak; strategies run in separate child processes."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        size_t = ctypes.c_size_t
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                ("peak_working_set", size_t), ("working_set", size_t),
                ("peak_paged_pool", size_t), ("paged_pool", size_t),
                ("peak_nonpaged_pool", size_t), ("nonpaged_pool", size_t),
                ("pagefile", size_t), ("peak_pagefile", size_t)]
        get_handle = ctypes.windll.kernel32.GetCurrentProcess
        get_handle.restype = wintypes.HANDLE
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not get_info(get_handle(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.peak_working_set
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_snapshot(match):
    stages = ("match_segmentation", "event_detection", "stroke_classification",
              "score_recognition", "highlight_ranking", "pose", "court_detection", "shuttle_tracking")
    return {stage: dict(size=(p := artifact_path(match, stage)).stat().st_size,
        mtime_ns=p.stat().st_mtime_ns) if artifact_path(match, stage).is_file() else None
        for stage in stages}


def provenance(match, selections, expected_sources):
    """Identify a run without publishing input artifacts or personal paths.

    Content hashing runs outside timed strategies. Size/mtime still guards
    ordinary source changes during the run; SHA-256 identifies its dataset.
    """
    root = REPO
    sources = {}
    for stage, stamp in expected_sources.items():
        if stamp is None:
            sources[stage] = None
            continue
        path = artifact_path(match, stage)
        with path.open("rb") as stream:
            content_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        sources[stage] = dict(path=path.relative_to(match).as_posix(),
            size=stamp["size"], sha256=content_hash)
    if source_snapshot(match) != expected_sources:
        raise ValueError("source artifacts changed while recording provenance")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
        check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "modules", "experiments/commentary", "uv.lock"], cwd=root)
    if dirty.returncode not in (0, 1):
        raise RuntimeError("cannot determine benchmark source state")
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--",
        "modules", "experiments/commentary", "uv.lock"], cwd=root, check=True,
        capture_output=True, text=True).stdout.strip()
    return dict(program_commit=revision, analysis_sources_dirty=dirty.returncode == 1 or bool(untracked),
        python=sys.version.split()[0], platform=platform.platform(),
        lock_sha256=hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        selections=[dict(segment_index=s, top_player=p) for s,p in selections],
        artifacts=sources, data_version=digest(sources))


def benchmark(match, selections, strategy):
    before = source_snapshot(match)
    rows = []
    started = perf_counter()
    with BatchReads([segment for segment, _ in selections], strategy == "reuse") as reads:
        for segment, top in selections:
            mapping = CourtPositionToPlayer(top=top, bottom="b" if top == "a" else "a")
            start = perf_counter()
            stages = upstream.read_commentary_inputs(match, segment, mapping)
            input_seconds = perf_counter() - start
            start = perf_counter()
            fact = upstream.build_rally_fact_from_stages(stages=stages, segment_index=segment,
                court_position_to_player=mapping)
            compact = build_compact_rally_facts(stages=stages, segment_index=segment,
                court_position_to_player=mapping)
            analysis = analyze_rally(fact)
            build_seconds = perf_counter() - start
            catalog = _evidence_catalog(compact)
            # Same generator payload shape as v2; no prompt/provider changes.
            from modules.commentary.analysis.observation_grounding import CAPABILITIES
            payload = dict(compact_rally_facts=compact.model_dump(mode="json", exclude={"score", "server"}),
                optional_pattern_hints=[p.model_dump(mode="json") for p in analysis.patterns],
                source_catalog=[dict(fact_id=ref, position=item.position,
                    allowed_fields=list(CAPABILITIES.get(item.kind, ())) if item.eligible else [])
                    for ref, item in catalog.items()])
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            hashes = {name: digest(obj.model_dump(mode="json"))
                      for name, obj in (("rally", fact), ("compact", compact), ("analysis", analysis))}
            rows.append(dict(segment_index=segment, top_player=top, input_seconds=input_seconds,
                build_seconds=build_seconds, hashes=hashes, generator_payload_hash=digest(payload),
                generator_payload_utf8_bytes=len(serialized.encode()),
                event_count=len(compact.events), court_fact_count=sum(e.court_position is not None for e in compact.events)))
            print(f"{strategy}: segment {segment} completed ({input_seconds:.3f}s input)", file=sys.stderr, flush=True)
    if before != source_snapshot(match):
        raise ValueError("source artifacts changed during benchmark")
    return dict(strategy=strategy, wall_seconds=perf_counter()-started,
        peak_rss_bytes=peak_rss_bytes(), physical_read_counts=dict(reads.counts),
        reader_seconds=dict(reads.seconds), pose_records_seen=reads.pose_records_seen,
        pose_records_retained=reads.pose_records_retained, rows=rows, sources=before,
        court_policy="strict", gemini_requests=0)


def compare(match, selections):
    results = []
    for strategy in ("baseline", "reuse"):
        args = [sys.executable, "-B", "-m", "experiments.commentary.commentary_inputs", "--match", str(match),
                "--strategy", strategy, "--selection", *[f"{s}:{p}" for s, p in selections]]
        result = subprocess.run(args, check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        results.append(json.loads(result.stdout))
    a, b = results
    if a["sources"] != b["sources"]:
        raise ValueError("artifacts changed between strategies")
    equivalent = all(x["hashes"] == y["hashes"] and x["generator_payload_hash"] == y["generator_payload_hash"]
                     for x, y in zip(a["rows"], b["rows"], strict=True))
    if not equivalent:
        raise AssertionError("baseline/reuse output mismatch")
    return dict(equivalent=equivalent, speedup=a["wall_seconds"]/b["wall_seconds"], results=results,
                note="First and repeated application reads; OS disk caches were not flushed. Single trial, no LLM timing.")


def selection(value):
    try:
        index, top = value.split(":")
        index = int(index)
        if index < 0 or top not in ("a", "b"):
            raise ValueError()
        return index, top
    except ValueError:
        raise argparse.ArgumentTypeError("selection must be SEGMENT:a or SEGMENT:b with confirmed TOP identity")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", type=Path, required=True)
    parser.add_argument("--selection", nargs="+", type=selection, required=True)
    parser.add_argument("--strategy", choices=("compare", "baseline", "reuse"), default="compare")
    args = parser.parse_args()
    result = compare(args.match, args.selection) if args.strategy == "compare" else benchmark(args.match, args.selection, args.strategy)
    sources = result["results"][0]["sources"] if args.strategy == "compare" else result["sources"]
    result["provenance"] = provenance(args.match, args.selection, sources)
    print(json.dumps(result, ensure_ascii=True, indent=2))
