"""Explicit, caller-supplied identities for one segment's court positions."""

from pydantic import model_validator

from modules.commentary.schemas import Player, StrictModel


class CourtPositionToPlayer(StrictModel):
    """Supply per segment; a mapping must be reconsidered after a side change.

    Geometry and stroke labels identify court positions, never identities.
    No default mapping or automatic inference is provided.
    """

    top: Player
    bottom: Player

    @model_validator(mode="after")
    def validate_distinct_players(self) -> "CourtPositionToPlayer":
        if self.top == self.bottom:
            raise ValueError("top and bottom must map to different players")
        return self
