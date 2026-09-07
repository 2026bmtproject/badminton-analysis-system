import pytest
from pydantic import ValidationError

from modules.commentary.identity import CourtPositionToPlayer


@pytest.mark.parametrize("top,bottom", [("a", "b"), ("b", "a")])
def test_explicit_mapping_round_trip(top, bottom):
    mapping = CourtPositionToPlayer(top=top, bottom=bottom)
    assert CourtPositionToPlayer.model_validate_json(mapping.model_dump_json()) == mapping
    assert mapping.model_dump() == {"top": top, "bottom": bottom}


@pytest.mark.parametrize("data", [
    {}, {"top": "a"}, {"top": "a", "bottom": "a"},
    {"top": "b", "bottom": "b"}, {"top": "top", "bottom": "b"},
    {"top": "a", "bottom": None}, {"top": "a", "bottom": "b", "infer": True},
])
def test_mapping_requires_two_distinct_explicit_identities(data):
    with pytest.raises(ValidationError):
        CourtPositionToPlayer(**data)
