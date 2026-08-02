"""Unit tests for the shared content-addressed segment cache.

The stage-level caches each have their own tests for their own parameters; this file
covers the behaviour they all inherit, and the invariant the whole design rests on: a
cache entry is identified by what produced it, never by where it sits in a list.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from modules.common.segment_cache import (
    MANIFEST_FILENAME,
    SegmentCache,
    atomic_savez,
    digest,
    prune,
)

PARAMS = {"model": "a.pt", "threshold": 0.5}


def segments(*bounds) -> list[dict]:
    return [{"start_frame": a, "end_frame": b} for a, b in bounds]


def cache(tmp_path, **overrides) -> SegmentCache:
    return SegmentCache(tmp_path / "cache", {**PARAMS, **overrides})


def fill(c: SegmentCache, segs, upstream=None):
    plan = c.plan(segs, upstream)
    for entry in plan.missing:
        entry.path.parent.mkdir(parents=True, exist_ok=True)
        entry.path.write_bytes(f"{entry.start_frame}-{entry.end_frame}".encode())
    c.commit(plan)
    return plan


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #


def test_key_depends_on_params_frames_and_upstream(tmp_path):
    c = cache(tmp_path)
    base = c.key_for({"start_frame": 0, "end_frame": 10})

    assert c.key_for({"start_frame": 0, "end_frame": 11}) != base
    assert c.key_for({"start_frame": 1, "end_frame": 10}) != base
    assert c.key_for({"start_frame": 0, "end_frame": 10}, "up") != base
    assert cache(tmp_path, threshold=0.6).key_for({"start_frame": 0, "end_frame": 10}) != base


def test_key_does_not_depend_on_dict_ordering(tmp_path):
    a = SegmentCache(tmp_path, {"x": 1, "y": 2})
    b = SegmentCache(tmp_path, {"y": 2, "x": 1})
    assert a.params_key == b.params_key


def test_digest_is_stable_across_processes():
    # A hash that moved between runs would silently invalidate every cache on upgrade.
    assert digest({"a": 1}, [2, 3], "x") == digest({"a": 1}, [2, 3], "x")


# --------------------------------------------------------------------------- #
# planning — the point of the whole exercise
# --------------------------------------------------------------------------- #


def test_editing_one_segment_costs_one_segment(tmp_path):
    c = cache(tmp_path)
    fill(c, segments((0, 99), (200, 299), (400, 499)))

    plan = c.plan(segments((0, 99), (200, 301), (400, 499)))
    assert [e.index for e in plan.missing] == [1]


def test_position_is_not_identity(tmp_path):
    """Inserting a rally renumbers every later one, and must recompute none of them."""
    c = cache(tmp_path)
    fill(c, segments((0, 99), (200, 299), (400, 499)))

    plan = c.plan(segments((0, 99), (120, 150), (200, 299), (400, 499)))
    assert [e.index for e in plan.missing] == [1]
    assert plan.entries[2].path.read_bytes() == b"200-299"   # was index 1 before


def test_reordering_recomputes_nothing(tmp_path):
    c = cache(tmp_path)
    fill(c, segments((0, 99), (200, 299)))

    assert not c.plan(segments((200, 299), (0, 99))).missing


def test_an_upstream_change_isolates_to_its_own_segment(tmp_path):
    c = cache(tmp_path)
    segs = segments((0, 99), (200, 299))
    fill(c, segs, ["a", "b"])

    plan = c.plan(segs, ["a", "c"])
    assert [e.index for e in plan.missing] == [1]


def test_upstream_length_must_match(tmp_path):
    with pytest.raises(ValueError):
        cache(tmp_path).plan(segments((0, 99), (200, 299)), ["only-one"])


def test_force_keeps_the_paths_but_marks_everything_missing(tmp_path):
    c = cache(tmp_path)
    before = fill(c, segments((0, 99), (200, 299)))

    plan = c.plan(segments((0, 99), (200, 299)), force=True)
    assert len(plan.missing) == 2
    assert [e.path for e in plan.entries] == [e.path for e in before.entries]


def test_an_interrupted_run_resumes_without_a_manifest(tmp_path):
    """Existence on disk decides what to recompute, not the manifest.

    A run killed halfway never gets to commit, and the segments it did finish must not
    be thrown away for it.
    """
    c = cache(tmp_path)
    segs = segments((0, 99), (200, 299))
    plan = c.plan(segs)
    plan.entries[0].path.parent.mkdir(parents=True, exist_ok=True)
    plan.entries[0].path.write_bytes(b"done before the crash")

    assert [e.index for e in c.plan(segs).missing] == [1]
    assert not (c.dir / MANIFEST_FILENAME).exists()


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #


def test_manifest_maps_index_to_file(tmp_path):
    c = cache(tmp_path)
    fill(c, segments((0, 99), (200, 299)))

    manifest = c.read_manifest()
    assert manifest["params"] == PARAMS
    assert [e["index"] for e in manifest["entries"]] == [0, 1]
    assert [e["start_frame"] for e in manifest["entries"]] == [0, 200]
    assert all((c.dir / e["file"]).is_file() for e in manifest["entries"])


def test_notes_are_reported_but_never_hashed(tmp_path):
    """A note is an input too expensive to key on: it warns, it does not invalidate."""
    segs = segments((0, 99))
    a = SegmentCache(tmp_path / "cache", PARAMS, notes={"court": "aaaa"})
    fill(a, segs)

    b = SegmentCache(tmp_path / "cache", PARAMS, notes={"court": "bbbb"})
    assert b.stale_notes() == ["court"]
    assert b.plan(segs).missing == []               # reusable in spite of the change
    assert b.params_key == a.params_key

    unchanged = SegmentCache(tmp_path / "cache", PARAMS, notes={"court": "aaaa"})
    assert unchanged.stale_notes() == []


def test_notes_on_a_fresh_directory_are_not_a_change(tmp_path):
    c = SegmentCache(tmp_path / "cache", PARAMS, notes={"court": "aaaa"})
    assert c.stale_notes() == []


def test_an_unreadable_manifest_is_not_a_crash(tmp_path):
    c = cache(tmp_path)
    fill(c, segments((0, 99)))
    (c.dir / MANIFEST_FILENAME).write_text("{ this is not json", encoding="utf-8")

    assert c.read_manifest() is None
    assert prune(c.dir) == ([], 0)


# --------------------------------------------------------------------------- #
# legacy migration
# --------------------------------------------------------------------------- #


def write_legacy(c: SegmentCache, bounds, params=None, suffix=".npz"):
    c.dir.mkdir(parents=True, exist_ok=True)
    (c.dir / "meta.json").write_text(
        json.dumps({**(params if params is not None else c.params), "segments": bounds}),
        encoding="utf-8",
    )
    for i, (start, end) in enumerate(bounds):
        (c.dir / f"seg{i:04d}{suffix}").write_bytes(f"{start}-{end}".encode())


def test_migration_renames_and_recomputes_nothing(tmp_path):
    c = cache(tmp_path)
    segs = segments((0, 99), (200, 299))
    write_legacy(c, [[0, 99], [200, 299]])

    assert c.migrate_legacy(segs) == 2

    plan = c.plan(segs)
    assert plan.missing == []
    assert plan.entries[0].path.read_bytes() == b"0-99"
    assert plan.entries[1].path.read_bytes() == b"200-299"
    assert not (c.dir / "meta.json").exists()


def test_migration_refuses_when_the_global_params_disagree(tmp_path):
    """The old meta mixed params and segments; only its params half can vouch for it."""
    c = cache(tmp_path)
    segs = segments((0, 99))
    write_legacy(c, [[0, 99]], params={**PARAMS, "threshold": 0.9})

    assert c.migrate_legacy(segs) == 0
    assert c.plan(segs).missing
    assert (c.dir / "meta.json").exists()


def test_migration_drops_entries_that_are_no_longer_segments(tmp_path):
    c = cache(tmp_path)
    write_legacy(c, [[0, 99], [200, 299]])

    assert c.migrate_legacy(segments((0, 99))) == 1
    assert not list(c.dir.glob("seg*.npz"))


def test_migration_is_a_no_op_once_a_manifest_exists(tmp_path):
    c = cache(tmp_path)
    segs = segments((0, 99))
    fill(c, segs)
    (c.dir / "meta.json").write_text(json.dumps({**c.params, "segments": [[0, 99]]}), "utf-8")

    assert c.migrate_legacy(segs) == 0


def test_discard_legacy_removes_the_old_shape_entirely(tmp_path):
    c = cache(tmp_path)
    write_legacy(c, [[0, 99], [200, 299]])

    assert c.discard_legacy() == 2
    assert not (c.dir / "meta.json").exists()
    assert not list(c.dir.glob("seg*.npz"))


# --------------------------------------------------------------------------- #
# prune
# --------------------------------------------------------------------------- #


def test_prune_lists_what_the_manifest_no_longer_references(tmp_path):
    c = cache(tmp_path)
    original = fill(c, segments((0, 99), (200, 299)))
    fill(c, segments((0, 99), (200, 305)))          # one rally re-cut

    stale, size = prune(c.dir)

    assert [p.name for p in stale] == [original.entries[1].path.name]
    assert size == original.entries[1].path.stat().st_size
    assert original.entries[1].path.exists(), "prune reports, it does not delete"


def test_prune_never_lists_the_manifest_itself(tmp_path):
    c = cache(tmp_path)
    fill(c, segments((0, 99)))
    assert prune(c.dir) == ([], 0)


# --------------------------------------------------------------------------- #
# atomic writes
# --------------------------------------------------------------------------- #


def test_atomic_savez_leaves_no_temp_file(tmp_path):
    target = tmp_path / "sub" / "entry.npz"
    atomic_savez(target, values=np.arange(4))

    assert target.is_file()
    assert [p.name for p in tmp_path.joinpath("sub").iterdir()] == ["entry.npz"]
    with np.load(target) as data:
        assert list(data["values"]) == [0, 1, 2, 3]
