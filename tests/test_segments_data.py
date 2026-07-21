"""Rally-boundary data structure: the ``Segment`` contract and the segment
records exchanged between stages (segments.json).

A "rally boundary" in this codebase is a (start_frame, end_frame) pair turned
into a :class:`Segment`-shaped record by ``build_segment_records``. These tests
pin the record shape, the frame->second conversion, and the round trip through
the JSON I/O helper.
"""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, Segment
from modules.match_segmentation.segments import (
    SEGMENT_FIELDS,
    build_segment_records,
    build_segments_from_mask,
    find_threshold_otsu3,
    looks_single_scene,
    reject_cross_outliers,
    threshold_near_distribution_edge,
    write_segments,
)


def test_segment_dataclass_fields_match_the_io_schema():
    """The Segment contract and the JSON writer must agree on the field set."""
    field_names = tuple(f.name for f in dataclasses.fields(Segment))
    assert field_names == SEGMENT_FIELDS


def test_segment_is_the_declared_record_type_for_the_stage():
    assert PIPELINE["match_segmentation"].record_type is Segment
    assert PIPELINE["match_segmentation"].output_filename == "segments.json"


def test_build_segment_records_converts_frames_to_seconds():
    # fps=30, matches the example in the segments.json contract docstring.
    records = build_segment_records([(1, 19)], fps=30.0)

    assert len(records) == 1
    rec = records[0]
    assert set(rec) == set(SEGMENT_FIELDS)
    assert rec["start_frame"] == 1
    assert rec["end_frame"] == 19
    assert rec["start_sec"] == pytest.approx(0.033)
    assert rec["end_sec"] == pytest.approx(0.633)
    assert rec["duration_sec"] == pytest.approx(0.6)


def test_build_segment_records_rounds_to_three_decimals():
    (rec,) = build_segment_records([(0, 1)], fps=3.0)
    # 1/3 = 0.333... -> rounded to 3 places.
    assert rec["end_sec"] == 0.333
    assert rec["duration_sec"] == 0.333


def test_build_segment_records_coerces_numpy_ints_to_json_safe_ints():
    """Frame indices often arrive as numpy ints; records must stay JSON-safe."""
    (rec,) = build_segment_records([(np.int64(5), np.int64(10))], fps=30.0)

    assert isinstance(rec["start_frame"], int)
    assert isinstance(rec["end_frame"], int)
    # If they were still numpy ints this would raise TypeError.
    json.dumps(rec)


def test_build_segment_records_empty():
    assert build_segment_records([], fps=30.0) == []


def test_write_then_read_segments_round_trip(tmp_path):
    out = tmp_path / "nested" / "segments.json"
    segments = [(1, 19), (40, 88)]

    write_segments(out, segments, fps=30.0)

    assert out.is_file()  # parent dirs were created
    data = read_artifact(PIPELINE["match_segmentation"], out)
    assert data["fps"] == 30.0
    assert [(r["start_frame"], r["end_frame"]) for r in data["segments"]] == segments


def test_build_segments_from_mask_finds_contiguous_low_runs():
    # low=1: frames 1-2 and 5-6 are contiguous low-motion runs.
    mask = np.array([0, 1, 1, 0, 0, 1, 1], dtype=bool)
    assert build_segments_from_mask(mask) == [(1, 2), (5, 6)]


def test_build_segments_from_mask_closes_run_at_end():
    mask = np.array([0, 0, 1, 1, 1], dtype=bool)
    assert build_segments_from_mask(mask) == [(2, 4)]


def test_build_segments_from_mask_no_low_frames():
    mask = np.zeros(5, dtype=bool)
    assert build_segments_from_mask(mask) == []


# ── reject_cross_outliers ────────────────────────────────────────────────────
def _segs(n):
    return [(i * 100, i * 100 + 50) for i in range(n)]


def test_reject_cross_outliers_drops_high_replays():
    # 10 tight match segments (~7) plus two replay outliers (~40).
    segs = _segs(12)
    avgs = [7, 7, 8, 7, 40, 7, 8, 7, 40, 7, 7, 8]
    kept, dropped = reject_cross_outliers(segs, avgs, k=3.0)
    assert dropped == [4, 8]
    assert len(kept) == 10
    assert segs[4] not in kept and segs[8] not in kept


def test_reject_cross_outliers_keeps_tight_cluster():
    # Real-only: max is ~1.8x median, well under k=3 -> nothing dropped.
    segs = _segs(10)
    avgs = [25, 26, 24, 45, 25, 27, 24, 26, 25, 25]  # median ~25, max 45 = 1.8x
    kept, dropped = reject_cross_outliers(segs, avgs, k=3.0)
    assert dropped == []
    assert kept == segs


def test_reject_cross_outliers_needs_enough_segments():
    segs = _segs(5)
    avgs = [7, 7, 40, 7, 8]
    kept, dropped = reject_cross_outliers(segs, avgs, k=3.0, min_segments=8)
    assert dropped == [] and kept == segs


def test_reject_cross_outliers_bails_when_too_many_would_drop():
    # If the "outliers" are actually half the set, the median is not a clean
    # match cluster, so the guard refuses to drop anything.
    segs = _segs(10)
    avgs = [1, 1, 1, 1, 1, 50, 50, 50, 50, 50]
    kept, dropped = reject_cross_outliers(segs, avgs, k=3.0, max_drop_ratio=0.30)
    assert dropped == [] and kept == segs


# ── looks_single_scene ───────────────────────────────────────────────────────
def test_single_scene_true_for_clean_clip_distribution():
    # A clean single shot: every frame differs only slightly from the last.
    rng = np.random.default_rng(0)
    scores = rng.integers(0, 3, size=2000).astype(float)  # 0..2, no cuts
    assert looks_single_scene(scores) is True


def test_single_scene_false_when_scene_cuts_present():
    # A broadcast: mostly low motion, but a sprinkling of big cut spikes.
    rng = np.random.default_rng(1)
    scores = rng.integers(0, 6, size=2000).astype(float)
    scores[::120] = 60  # periodic scene cuts -> long high tail
    assert looks_single_scene(scores) is False


def test_single_scene_empty_is_false():
    assert looks_single_scene(np.array([], dtype=float)) is False


# ── find_threshold_otsu3 ─────────────────────────────────────────────────────
def _broadcast_scores() -> np.ndarray:
    """The three masses a real broadcast produces, in their measured ratios."""
    rng = np.random.default_rng(7)
    still = rng.normal(1.0, 0.3, size=3500)
    rally = rng.normal(6.0, 2.0, size=5500)
    cuts = rng.normal(45.0, 12.0, size=1000)
    return np.clip(np.concatenate([still, rally, cuts]), 0.0, None)


def test_otsu3_threshold_lands_between_still_and_rally():
    threshold = find_threshold_otsu3(_broadcast_scores())
    assert 1.5 < threshold < 6.0


def test_otsu3_cuts_lower_than_a_2class_split_would():
    """The property the switch to three classes buys."""
    scores = _broadcast_scores()
    log_scores = np.log10(np.maximum(scores, 1.0))
    hist, edges = np.histogram(log_scores, bins=256)
    p = hist.astype(float) / hist.sum()
    centers = (edges[:-1] + edges[1:]) / 2.0
    omega = np.cumsum(p)
    mu = np.cumsum(p * centers)
    denom = np.where(omega * (1.0 - omega) > 0, omega * (1.0 - omega), 1e-12)
    two_class = float(10.0 ** centers[int(np.argmax((mu[-1] * omega - mu) ** 2 / denom))])

    assert find_threshold_otsu3(scores) < two_class


def test_otsu3_threshold_is_insensitive_to_bin_count():
    """No knob that swings the answer."""
    scores = _broadcast_scores()
    thresholds = [find_threshold_otsu3(scores, nbins=n) for n in (64, 256, 1024)]
    assert max(thresholds) - min(thresholds) < 1.0


def test_otsu3_empty_scores_raises():
    with pytest.raises(ValueError):
        find_threshold_otsu3(np.array([], dtype=float))


def test_otsu3_constant_scores_keep_everything_low():
    """One population = no split to make; everything must stay low motion."""
    scores = np.full(500, 7.0)
    threshold = find_threshold_otsu3(scores)
    assert threshold > scores.max()
    assert bool(np.all(scores < threshold))


def test_otsu3_single_frame_keeps_everything_low():
    threshold = find_threshold_otsu3(np.array([4.0]))
    assert threshold > 4.0


# ── threshold_near_distribution_edge ─────────────────────────────────────────
def test_threshold_in_the_body_is_not_flagged():
    assert threshold_near_distribution_edge(_broadcast_scores(), 10.0) is False


def test_threshold_out_in_the_tail_is_flagged():
    scores = _broadcast_scores()
    assert threshold_near_distribution_edge(scores, 0.1) is True
    assert threshold_near_distribution_edge(scores, 999.0) is True


def test_threshold_edge_check_empty_is_false():
    assert threshold_near_distribution_edge(np.array([], dtype=float), 3.0) is False
