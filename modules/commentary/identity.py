"""Explicit, caller-supplied identities for one segment's court positions."""

from pydantic import model_validator
from typing import Literal

from modules.commentary.schemas import Player, StrictModel


class CourtPositionToPlayer(StrictModel):
    """Supply per segment; a mapping must be reconsidered after a side change.

    Geometry and stroke labels identify court positions, never identities.
    No default mapping or automatic inference is provided.
    """

    top: Player
    bottom: Player

    def resolve(self, position: str | None) -> Player | None:
        if position is None:
            return None
        if position not in ("top", "bottom"):
            raise ValueError(f"unknown court position: {position}")
        return self.top if position == "top" else self.bottom

    def position_for(self, player: Player | None) -> Literal["top", "bottom"] | None:
        if player is None:
            return None
        if player not in (self.top, self.bottom):
            raise ValueError(f"unknown player identity: {player}")
        return "top" if player == self.top else "bottom"

    @model_validator(mode="after")
    def validate_distinct_players(self) -> "CourtPositionToPlayer":
        if self.top == self.bottom:
            raise ValueError("top and bottom must map to different players")
        return self
