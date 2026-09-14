"""Provider-agnostic one-rally CommentaryService."""

from modules.commentary.facts.observations import TacticalObservation
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.generation.commentator import CommentaryGenerationResult, generate_commentary
from modules.commentary.generation.planner import build_commentary_plan
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.providers.base import LLMProvider
from modules.commentary.schemas import RallyFact


class CommentaryService:
    """Accept prebuilt trusted facts; never invokes tactical analysis internally."""

    def __init__(self, *, provider: LLMProvider, reviewer: LLMProvider,
                 requested_model: str | None = None,
                 reviewer_requested_model: str | None = None):
        self._provider = provider
        self._reviewer = reviewer
        self._requested_model = requested_model
        self._reviewer_requested_model = reviewer_requested_model

    def generate(self, *, rally_fact: RallyFact, compact_facts: CompactRallyFacts,
                 court_position_to_player: CourtPositionToPlayer,
                 tactical_observations: list[TacticalObservation] | None = None,
                 include_summary: bool = True) -> CommentaryGenerationResult:
        plan = build_commentary_plan(
            rally_fact=rally_fact, compact_facts=compact_facts,
            court_position_to_player=court_position_to_player,
            tactical_observations=tactical_observations,
            include_summary=include_summary,
        )
        return generate_commentary(
            provider=self._provider, reviewer=self._reviewer, plan=plan,
            requested_model=self._requested_model,
            reviewer_requested_model=self._reviewer_requested_model,
        )
