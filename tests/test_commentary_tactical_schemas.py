import pytest
from pydantic import ValidationError

from modules.commentary.facts.tactical import (
    GeneratedTacticalFact,
    TacticalAnalysisResult,
    TacticalFact,
)


def candidate():
    return dict(pattern_type="sustained_attack", description="Repeated attack",
                confidence=0.8, salience=0.5, start_event_index=42, end_event_index=49,
                players=["a"], evidence_fact_ids=["stroke:42", "stroke:49"], limitations=[])


@pytest.mark.parametrize("cls", [GeneratedTacticalFact, TacticalFact])
@pytest.mark.parametrize("changes", [
    {"end_event_index": 41}, {"start_event_index": -1}, {"confidence": 1.1},
    {"players": ["top"]}, {"players": ["a", "a"]},
    {"evidence_fact_ids": ["stroke:42", "stroke:42"]},
    {"evidence_fact_ids": ["stroke:42"]}, {"evidence_fact_ids": ["", "stroke:49"]},
])
def test_tactical_evidence_constraints(cls, changes):
    data = candidate() | changes
    if cls is TacticalFact:
        data.update(fact_id="tactical:3:0", segment_index=3)
    with pytest.raises(ValidationError):
        cls(**data)


def test_tactical_result_round_trip_and_segment_integrity():
    fact = TacticalFact(**candidate(), fact_id="tactical:3:0", segment_index=3)
    data = dict(schema_version="tactical-facts-v1", prompt_version="v1",
                segment_index=3, facts=[fact], warnings=[])
    result = TacticalAnalysisResult(**data)
    assert TacticalAnalysisResult.model_validate_json(result.model_dump_json()) == result
    assert result.facts[0].start_event_index == 42
    for change in ({"segment_index": 4}, {"facts": [fact, fact]}):
        with pytest.raises(ValidationError):
            TacticalAnalysisResult(**(data | change))
