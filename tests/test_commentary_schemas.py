import json

import pytest
from pydantic import ValidationError

from modules.commentary.facts.schemas import CompactRallyFacts, CompactStrokeFact
from modules.commentary.schemas import (
    GeneratedCommentary, GeneratedRallyTextBatch, GeneratedStrokeBatchItem,
    GeneratedStrokeText, RallyFact, RallyFactEvent,
)


def stroke_data():
    return dict(event_index=42, frame=1200, time_sec=40.0, player="a",
                stroke_type="drop", stroke_confidence=0.1)


@pytest.mark.parametrize("changes", [
    {"event_index": -1}, {"event_index": 1.5}, {"player": "top"},
    {"stroke_confidence": 1.01}, {"stroke_confidence": -0.1},
    {"time_sec": float("nan")}, {"unexpected": True},
])
def test_domain_rejects_invalid_indices_players_confidence(changes):
    with pytest.raises(ValidationError):
        RallyFactEvent(**(stroke_data() | changes))


@pytest.mark.parametrize("highlight", [None, 0.0, 1.0])
def test_highlights_do_not_gate_ordinary_low_confidence_events(highlight):
    fact = RallyFact(segment_index=3, game_index=None, start_sec=39, end_sec=45,
                     duration_sec=6, score={"a": None, "b": None}, server=None,
                     events=[stroke_data()], rally_length=1, highlight_score=highlight)
    assert fact.events[0].event_index == 42
    assert fact.events[0].stroke_confidence == 0.1
    assert RallyFact.model_validate_json(fact.model_dump_json()) == fact


def test_compact_facts_allow_unavailable_optional_vision():
    stroke = CompactStrokeFact(**stroke_data(), fact_id="stroke:42", pose=None,
                               court_position=None, shuttle_path=None, warnings=[])
    rally = CompactRallyFacts(schema_version="compact-rally-facts-v1", segment_index=3,
                              fps=30, start_frame=1170, end_frame=1350, start_sec=39,
                              end_sec=45, score={"a": 1, "b": 2}, server=None,
                              events=[stroke], warnings=[])
    assert CompactRallyFacts.model_validate_json(rally.model_dump_json()) == rally
    assert rally.events[0].event_index == 42


def test_unresolved_player_remains_unknown():
    assert RallyFactEvent(**(stroke_data() | {"player": None})).player is None


def test_generated_text_cannot_supply_source_timing():
    data = dict(segment_index=3, events=[dict(stroke_index=42, text="A drop.", source_fact_ids=["stroke:42"])], summary=None)
    assert GeneratedRallyTextBatch(**data).events[0].stroke_index == 42
    for field in ("frame", "time_sec", "start_sec"):
        with pytest.raises(ValidationError):
            GeneratedRallyTextBatch(**(data | {"events": [data["events"][0] | {field: 40}]}))
    with pytest.raises(ValidationError):
        GeneratedRallyTextBatch(**(data | {"events": [dict(stroke_index=42, text="A drop.")]}))


@pytest.mark.parametrize("field,value", [
    ("stroke_confidence", True), ("stroke_confidence", False),
    ("stroke_confidence", "0.9"), ("time_sec", True),
    ("time_sec", False), ("time_sec", "1.0"),
])
def test_numeric_aliases_reject_coercion(field, value):
    data = stroke_data() | {field: value}
    with pytest.raises(ValidationError):
        RallyFactEvent(**data)
    with pytest.raises(ValidationError):
        RallyFactEvent.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("confidence,time", [(0, 0), (1, 40), (.9, 40.5)])
def test_numeric_aliases_preserve_valid_json_numbers(confidence, time):
    data = stroke_data() | {"stroke_confidence": confidence, "time_sec": time}
    event = RallyFactEvent.model_validate_json(json.dumps(data))
    assert event.stroke_confidence == confidence and event.time_sec == time
    assert RallyFactEvent.model_validate_json(event.model_dump_json()) == event


@pytest.mark.parametrize("model,extra,limit", [
    (GeneratedCommentary, {"segment_index": 3}, 240),
    (GeneratedStrokeText, {}, 120),
    (GeneratedStrokeBatchItem, {"stroke_index": 42}, 120),
])
@pytest.mark.parametrize("text", ["", " ", "\t", " \t\r\n "])
def test_generated_text_rejects_blank(model, extra, limit, text):
    with pytest.raises(ValidationError):
        model(**extra, text=text, source_fact_ids=["stroke:42"])


@pytest.mark.parametrize("model,extra,limit", [
    (GeneratedCommentary, {"segment_index": 3}, 240),
    (GeneratedStrokeText, {}, 120),
    (GeneratedStrokeBatchItem, {"stroke_index": 42}, 120),
])
def test_generated_text_preserves_nonblank_semantics_and_lengths(model, extra, limit):
    assert model(**extra, text=" A drop. ", source_fact_ids=["stroke:42"]).text == " A drop. "
    assert model(**extra, text="x" * limit, source_fact_ids=["stroke:42"])
    with pytest.raises(ValidationError):
        model(**extra, text="x" * (limit + 1), source_fact_ids=["stroke:42"])
