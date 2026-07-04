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
