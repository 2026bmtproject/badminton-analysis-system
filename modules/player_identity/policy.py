"""Transparent ``serve_vote_v1``: who is "a" on court, from serves and score deltas.

Two independent signals meet here, one arithmetic and one visual:

* **Which scoreboard row served** follows from the score alone. Under rally scoring
  the winner of a rally serves the next one, so the row whose score went up between
  two rallies is the row serving the later one. No vision, no model.
* **Which half of the court served** comes from the rally's first stroke, but only
  when ``stroke_classification`` actually called that stroke a serve. That gate is
  the whole policy: ``event_detection``'s first hit in a segment is often the
  *return* rather than the serve, and mistaking one for the other does not add
  noise, it flips the answer. Ungated, the same vote lands between 55% and 82% and
  is confidently wrong on real matches; gated it lands between 92% and 100%.

Pairing the two gives one vote per rally for "row a is on top" or "row a is on the
bottom". Votes are pooled per court-end epoch, where the epochs come from the laws
of badminton rather than from the video: ends change after game 1, after game 2,
and when the leading side reaches 11 in the deciding game.

Nothing here touches the filesystem. The optional dense-scan fallback arrives as a
:class:`DenseServeLookup`, so a caller without that cache simply passes nothing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Protocol

from modules.contracts import POSE_PLAYERS, PlayerIdentityEpoch, RallyScore, StrokeLabel

POLICY_NAME = "serve_vote_v1"

#: The 8-class name ``stroke_classification`` writes for a serve. The gate below is a
#: string match against it rather than against BST's 25-class head, because the
#: artifact is where the merge to 8 classes has already happened.
SERVE_STROKE = "發球"

#: Votes an epoch needs before it resolves on its own evidence. Measured across 7
#: matches (20 epochs): 19 clear this, and the one that does not is filled from the
#: scoreboard convention instead.
MIN_VOTES = 5

#: A game restarts at 0-0, and the artifact records the score *before* each rally, so
#: a new game announces itself as a segment reading 0-0. Requiring the previous score
#: to be this high as well stops a single misread 0-0 mid-game from inventing an end
#: change — which would be worse than a missed one, since a spurious epoch boundary
#: makes the alternation check read the scoreboard convention backwards.
GAME_RESET_MIN_PREVIOUS = 15

#: Ends change mid-game only in the deciding game, when the leader reaches this.
DECIDER_SWITCH_POINT = 11

ROWS = ("a", "b")
FIXED_ROWS = "fixed_rows"          # scoreboard rows stay put; the mapping flips at each end change
TRACKED_ROWS = "tracked_rows"      # the graphic follows the court; the mapping is constant
_OTHER_SIDE = {"top": "bottom", "bottom": "top"}
_OTHER_ROW = {"a": "b", "b": "a"}


class DenseServeLookup(Protocol):
    """Optional per-segment serving side recovered from a dense stroke scan."""

    def serving_side(self, segment_index: int) -> str | None:
        """``"top"``/``"bottom"``, or None when this segment has no usable serve."""


@dataclass(frozen=True)
class EpochBounds:
    """One stretch of rallies between two end changes."""

    epoch_index: int
    game_index: int
    first_segment: int
    last_segment: int


@dataclass(frozen=True)
class IdentityResult:
    """What the policy concluded, plus the evidence for what it could not."""

    epochs: list[PlayerIdentityEpoch]
    convention: str | None
    unresolved: list[dict]


# ------------------------------------------------------------------ score reading
def games_from_scores(scores: list[RallyScore]) -> dict[int, int]:
    """``segment_index -> game_index``, from where the score restarts at 0-0.

    Segments whose score could not be read inherit the game in progress: they carry
    no evidence either way, and guessing a boundary from a blank is worse than
    carrying on.
    """
    games: dict[int, int] = {}
    game = 0
    previous: tuple[int, int] | None = None
    for score in sorted(scores, key=lambda s: s.segment_index):
        pair = _score_pair(score)
        if pair is None:
            games[score.segment_index] = game
            continue
        if (previous is not None and pair == (0, 0) and previous != (0, 0)
                and max(previous) >= GAME_RESET_MIN_PREVIOUS):
            game += 1
        games[score.segment_index] = game
        previous = pair
    return games


def end_epochs(scores: list[RallyScore], games: dict[int, int]) -> list[EpochBounds]:
    """Split the match at every end change the laws of badminton mandate.

    One epoch per game, and the deciding game splits again the moment the leading
    side reaches 11. That is the entire rule — which is why a side change never
    needs to be seen, only counted.
    """
    by_game: dict[int, list[int]] = defaultdict(list)
    for score in sorted(scores, key=lambda s: s.segment_index):
        by_game[games[score.segment_index]].append(score.segment_index)
    if not by_game:
        return []

    pairs = {s.segment_index: _score_pair(s) for s in scores}
    last_game = max(by_game)
    out: list[EpochBounds] = []
    for game in sorted(by_game):
        segments = by_game[game]
        split_at = None
        if game == last_game and game >= 2:
            for segment in segments:
                pair = pairs.get(segment)
                if pair is not None and max(pair) >= DECIDER_SWITCH_POINT:
                    split_at = segment
                    break
        if split_at is None:
            out.append(EpochBounds(len(out), game, segments[0], segments[-1]))
            continue
        before = [s for s in segments if s < split_at]
        after = [s for s in segments if s >= split_at]
        if before:
            out.append(EpochBounds(len(out), game, before[0], before[-1]))
        if after:
            out.append(EpochBounds(len(out), game, after[0], after[-1]))
    return out


def serving_row(scores: list[RallyScore], games: dict[int, int]) -> dict[int, str]:
    """``segment_index -> "a" | "b"``: which row served that rally.

    ``score_a``/``score_b`` hold the score *before* the rally, so the row that gained
    a point between the previous rally and this one won the previous rally, and under
    rally scoring that is exactly the row serving now. Only a clean one-point step
    inside a single game counts; anything else — a scoreboard that did not update, a
    misread, a gap where a rally went unsegmented — is evidence of nothing and is
    dropped rather than repaired.
    """
    out: dict[int, str] = {}
    previous: tuple[int, tuple[int, int]] | None = None       # (game, score)
    for score in sorted(scores, key=lambda s: s.segment_index):
        pair = _score_pair(score)
        game = games[score.segment_index]
        if pair is not None and previous is not None and previous[0] == game:
            delta = (pair[0] - previous[1][0], pair[1] - previous[1][1])
            if delta == (1, 0):
                out[score.segment_index] = "a"
            elif delta == (0, 1):
                out[score.segment_index] = "b"
        if pair is not None:
            previous = (game, pair)
    return out


# --------------------------------------------------------------- serve detection
def serving_side(
    strokes: list[StrokeLabel],
    dense: DenseServeLookup | None = None,
) -> dict[int, str]:
    """``segment_index -> "top" | "bottom"``: which half of the court served.

    The rally's first stroke answers this, but only if the classifier called it a
    serve. A first stroke labelled anything else means the serve was missed and the
    first thing detected was already the reply, whose side is the opposite of the
    answer — so the rally is dropped, not guessed at. ``dense`` fills only the
    segments the gate rejected; it never overrides a gated answer, because its
    fixed-window scan reads the hitter's side less reliably than the hit-anchored
    windows behind ``strokes``.
    """
    first: dict[int, StrokeLabel] = {}
    for stroke in sorted(strokes, key=lambda s: (s.segment_index, s.frame, s.event_index)):
        first.setdefault(stroke.segment_index, stroke)

    out: dict[int, str] = {}
    for segment, stroke in first.items():
        if stroke.stroke_type == SERVE_STROKE and stroke.player in POSE_PLAYERS:
            out[segment] = stroke.player
    if dense is None:
        return out
    for segment in first:
        if segment in out:
            continue
        side = dense.serving_side(segment)
        if side in POSE_PLAYERS:
            out[segment] = side
    return out


# ------------------------------------------------------------------------- voting
def _tally(
    epochs: list[EpochBounds],
    rows: dict[int, str],
    sides: dict[int, str],
) -> dict[int, Counter]:
    """Per epoch, how many rallies say "a is on top" versus "a is on the bottom"."""
    votes: dict[int, Counter] = {epoch.epoch_index: Counter() for epoch in epochs}
    for epoch in epochs:
        for segment in range(epoch.first_segment, epoch.last_segment + 1):
            row, side = rows.get(segment), sides.get(segment)
            if row is None or side is None:
                continue
            # The serving row is on the serving side, so "a" is on that side when the
            # server is "a", and on the other side when it is "b".
            top_row = side if row == "a" else _OTHER_SIDE[side]
            votes[epoch.epoch_index]["a" if top_row == "top" else "b"] += 1
    return votes


def _resolve_convention(resolved: dict[int, str]) -> str | None:
    """Do the scoreboard rows stay put across end changes, or follow the court?

    Read off the epochs that carried their own votes: under ``fixed_rows`` the
    mapping flips at every end change, under ``tracked_rows`` it never does. Fewer
    than two resolved epochs is not evidence of either, and says so.
    """
    order = sorted(resolved)
    if len(order) < 2:
        return None
    fixed = tracked = 0
    for earlier, later in zip(order, order[1:]):
        flipped = resolved[earlier] != resolved[later]
        if flipped == bool((later - earlier) % 2):
            fixed += 1
        else:
            tracked += 1
    if fixed == tracked:
        return None
    return FIXED_ROWS if fixed > tracked else TRACKED_ROWS


def _fill(epoch_index: int, resolved: dict[int, str], convention: str | None) -> str | None:
    """Borrow the nearest resolved epoch's answer, flipped if the ends have changed."""
    if convention is None or not resolved:
        return None
    nearest = min(resolved, key=lambda i: (abs(i - epoch_index), i))
    top_row = resolved[nearest]
    if convention == FIXED_ROWS and (epoch_index - nearest) % 2:
        top_row = _OTHER_ROW[top_row]
    return top_row


def infer_identity(
    strokes: list[StrokeLabel],
    scores: list[RallyScore],
    *,
    dense: DenseServeLookup | None = None,
    min_votes: int = MIN_VOTES,
) -> IdentityResult:
    """Run the whole policy over one match's strokes and scores.

    An epoch that neither carries ``min_votes`` of its own nor can be filled from the
    convention is left out of ``epochs`` entirely and reported in ``unresolved`` with
    its counts. A downstream analyst is better served by a hole it can see than by an
    identity that was guessed.
    """
    if min_votes < 1:
        raise ValueError(f"min_votes must be at least 1, got {min_votes}")
    if not scores:
        return IdentityResult([], None, [])

    games = games_from_scores(scores)
    epochs = end_epochs(scores, games)
    votes = _tally(epochs, serving_row(scores, games), serving_side(strokes, dense))

    resolved: dict[int, str] = {}
    for epoch in epochs:
        tally = votes[epoch.epoch_index]
        total = sum(tally.values())
        if total >= min_votes and tally["a"] != tally["b"]:
            resolved[epoch.epoch_index] = "a" if tally["a"] > tally["b"] else "b"

    convention = _resolve_convention(resolved)

    records: list[PlayerIdentityEpoch] = []
    unresolved: list[dict] = []
    for epoch in epochs:
        tally = votes[epoch.epoch_index]
        total = sum(tally.values())
        top_row = resolved.get(epoch.epoch_index)
        resolved_by = "vote"
        if top_row is None:
            top_row = _fill(epoch.epoch_index, resolved, convention)
            resolved_by = "convention"
        if top_row is None:
            unresolved.append({
                "epoch_index": epoch.epoch_index,
                "game_index": epoch.game_index,
                "first_segment": epoch.first_segment,
                "last_segment": epoch.last_segment,
                "votes": total,
                "top_is_a": tally["a"],
                "top_is_b": tally["b"],
            })
            continue
        records.append(PlayerIdentityEpoch(
            epoch_index=epoch.epoch_index,
            game_index=epoch.game_index,
            first_segment=epoch.first_segment,
            last_segment=epoch.last_segment,
            top=top_row,
            bottom=_OTHER_ROW[top_row],
            votes=total,
            agreement=(tally[top_row] / total) if total else 0.0,
            resolved_by=resolved_by,
        ))
    return IdentityResult(records, convention, unresolved)


def policy_metadata() -> dict:
    """Fresh, machine-readable parameters for the fixed v1 policy."""
    return {
        "name": POLICY_NAME,
        "serve_stroke": SERVE_STROKE,
        "min_votes": MIN_VOTES,
        "decider_switch_point": DECIDER_SWITCH_POINT,
        "vote": "score delta gives the serving row; the gated first stroke gives the serving side",
        "epochs": "one per game, deciding game split at the 11-point end change",
        "conventions": [FIXED_ROWS, TRACKED_ROWS],
    }


def _score_pair(score: RallyScore) -> tuple[int, int] | None:
    if score.score_a is None or score.score_b is None:
        return None
    return int(score.score_a), int(score.score_b)
