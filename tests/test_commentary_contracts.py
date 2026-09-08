"""Production artifact compatibility and source alignment invariants."""

from dataclasses import asdict, fields

import pytest
from pydantic import TypeAdapter, ValidationError

from modules.artifacts import read_artifact, write_artifact
from modules.contracts import (
    PIPELINE,
    CommentaryRally,
    RallyCommentarySummary,
    StrokeCommentaryEvent,
    pipeline_order,
)


def event(index=42, frame=1200, time=40.0, **changes):
    data = dict(segment_index=3, stroke_index=index, frame=frame, time_sec=time,
                text="A plays a drop.", source_fact_ids=[f"stroke:{index}"])
    return StrokeCommentaryEvent(**(data | changes))


@pytest.mark.parametrize("with_summary", [False, True])
def test_artifact_round_trip_preserves_full_match_indices(tmp_path, with_summary):
    summary = RallyCommentarySummary(3, "A changes the pace.", ["stroke:42"]) if with_summary else None
    rally = CommentaryRally(3, [event(), event(49, 1260, 42.0)], summary)
    spec = PIPELINE["commentary"]
    path = tmp_path / spec.output_filename
    write_artifact(spec, [rally], path)
    data = read_artifact(spec, path)
    assert data == {"rallies": [asdict(rally)]}
    restored = TypeAdapter(CommentaryRally).validate_python(data["rallies"][0])
    assert restored == rally
    assert [item.stroke_index for item in restored.events] == [42, 49]
    assert {f.name for f in fields(rally)} == {"segment_index", "events", "summary"}
    assert {f.name for f in fields(StrokeCommentaryEvent)} == {
        "segment_index", "stroke_index", "frame", "time_sec", "text", "source_fact_ids",
    }
    assert {f.name for f in fields(RallyCommentarySummary)} == {
        "segment_index", "text", "source_fact_ids",
    }


@pytest.mark.parametrize("changes", [
    {"stroke_index": -1}, {"stroke_index": True}, {"frame": -1},
    {"time_sec": -0.1}, {"time_sec": float("inf")}, {"time_sec": float("nan")},
    {"source_fact_ids": []}, {"source_fact_ids": [""]},
    {"source_fact_ids": [" "]}, {"text": ""}, {"text": " "},
])
def test_event_rejects_invalid_values(changes):
    with pytest.raises(ValidationError):
        event(**changes)


@pytest.mark.parametrize("cls,data", [
    (StrokeCommentaryEvent, dict(segment_index=3, stroke_index=42, frame=1200, time_sec=40, text="Hit")),
    (RallyCommentarySummary, dict(segment_index=3, text="Summary")),
])
def test_provenance_is_required(cls, data):
    with pytest.raises(ValidationError):
        cls(**data)


def test_summary_rejects_invented_timing():
    with pytest.raises(ValidationError):
        RallyCommentarySummary(3, "Summary", ["stroke:42"], start_sec=40)


@pytest.mark.parametrize("events", [
    [event(), event()],
    [event(49, 1260, 42), event()],
    [event(49), event()],  # deterministic tie break by full-match index
    [event(), event(49, 1199, 42)],  # time and frame contradict each other
    [event(segment_index=4)],
])
def test_rally_rejects_duplicate_unordered_or_mismatched_events(events):
    with pytest.raises(ValidationError):
        CommentaryRally(3, events)


def test_optional_summary_and_empty_or_tied_events():
    assert CommentaryRally(3, []).summary is None
    assert len(CommentaryRally(3, [event(), event(49)]).events) == 2
    with pytest.raises(ValidationError):
        CommentaryRally(3, [event()], RallyCommentarySummary(4, "Summary", ["stroke:42"]))


def test_direct_dependencies_and_order():
    spec = PIPELINE["commentary"]
    assert spec.record_type is CommentaryRally
    assert spec.record_key == "rallies"
    assert spec.output_filename == "commentary.json"
    assert spec.dependencies == ["match_segmentation", "event_detection", "stroke_classification", "score_recognition"]
    assert spec.optional_dependencies == ["highlight_ranking", "pose", "court_detection", "shuttle_tracking"]
    order = pipeline_order()
    for dependency in spec.dependencies + spec.optional_dependencies:
        assert order.index(dependency) < order.index("commentary")
    assert "audio_highlight" not in spec.dependencies + spec.optional_dependencies


def test_old_lines_envelope_is_incompatible(tmp_path):
    path = tmp_path / "commentary.json"
    path.write_text('{"lines": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="rallies"):
        read_artifact(PIPELINE["commentary"], path)
