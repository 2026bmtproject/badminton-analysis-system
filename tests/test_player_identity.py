"""Policy checks for serve_vote_v1, with no artifacts, video or models involved."""
import pytest

from modules.contracts import RallyScore, StrokeLabel
from modules.player_identity.policy import (
    DECIDER_SWITCH_POINT,
    FIXED_ROWS,
    MIN_VOTES,
    SERVE_STROKE,
    TRACKED_ROWS,
    end_epochs,
    games_from_scores,
    infer_identity,
    serving_row,
    serving_side,
)

OTHER_SIDE = {"top": "bottom", "bottom": "top"}


# ------------------------------------------------------------------- builders
def score(segment_index, a, b):
    return RallyScore(segment_index=segment_index, score_a=a, score_b=b)


def game_scores(rally_winners, start=0):
    """Pre-rally scores for one game, given who won each rally in turn."""
    out, a, b = [], 0, 0
    for index, winner in enumerate(rally_winners):
        out.append(score(start + index, a, b))
        if winner == "a":
            a += 1
        else:
            b += 1
    return out


def serve(segment_index, player, event_index=0, frame=None, stroke_type=SERVE_STROKE):
    return StrokeLabel(
        event_index=event_index,
        frame=segment_index * 1000 if frame is None else frame,
        segment_index=segment_index,
        player=player,
        stroke_type=stroke_type,
        confidence=0.9,
    )


def rally_strokes(segment_index, server, extra=("小球", "殺球")):
    """A serve followed by ordinary strokes alternating sides."""
    out = [serve(segment_index, server)]
    side = server
    for offset, stroke_type in enumerate(extra, start=1):
        side = OTHER_SIDE[side]
        out.append(serve(segment_index, side, event_index=offset,
                         frame=segment_index * 1000 + offset, stroke_type=stroke_type))
    return out


# --------------------------------------------------------------- game splitting
def test_game_index_follows_the_restart_at_zero():
    scores = game_scores(["a"] * 21) + game_scores(["b"] * 21, start=21)
    games = games_from_scores(scores)
    assert {games[s.segment_index] for s in scores[:21]} == {0}
    assert {games[s.segment_index] for s in scores[21:]} == {1}


def test_a_stray_zero_zero_midgame_does_not_start_a_game():
    # A misread early in a game must not invent an end change.
    scores = game_scores(["a", "b", "a", "b", "a"]) + [score(5, 0, 0)] + game_scores(["a"] * 3, start=6)
    assert max(games_from_scores(scores).values()) == 0


def test_unreadable_scores_inherit_the_game_in_progress():
    scores = [score(0, 0, 0), score(1, 1, 0), RallyScore(2, None, None), score(3, 2, 0)]
    games = games_from_scores(scores)
    assert games == {0: 0, 1: 0, 2: 0, 3: 0}


def test_two_game_match_has_one_epoch_per_game():
    scores = game_scores(["a"] * 21) + game_scores(["a"] * 21, start=21)
    epochs = end_epochs(scores, games_from_scores(scores))
    assert [(e.epoch_index, e.game_index) for e in epochs] == [(0, 0), (1, 1)]


def test_decider_splits_at_the_eleven_point_end_change():
    scores = (game_scores(["a"] * 21)
              + game_scores(["b"] * 21, start=21)
              + game_scores(["a"] * 21, start=42))
    epochs = end_epochs(scores, games_from_scores(scores))
    assert [(e.epoch_index, e.game_index) for e in epochs] == [(0, 0), (1, 1), (2, 2), (3, 2)]
    # The split lands on the first rally played at 11.
    assert epochs[3].first_segment == 42 + DECIDER_SWITCH_POINT


def test_second_game_is_not_split_when_there_is_no_decider():
    scores = game_scores(["a"] * 21) + game_scores(["a"] * 21, start=21)
    assert len(end_epochs(scores, games_from_scores(scores))) == 2


# --------------------------------------------------------------- serving row
def test_serving_row_is_whoever_won_the_previous_rally():
    scores = game_scores(["a", "a", "b", "b", "a"])
    rows = serving_row(scores, games_from_scores(scores))
    # Segment 0 has no predecessor, so its server is unknown, not guessed.
    assert rows == {1: "a", 2: "a", 3: "b", 4: "b"}


@pytest.mark.parametrize("pair", [(1, 1), (0, 0), (2, 0), (0, 2)])
def test_serving_row_drops_anything_but_a_clean_one_point_step(pair):
    scores = [score(0, 3, 3), score(1, 3 + pair[0], 3 + pair[1])]
    assert serving_row(scores, games_from_scores(scores)) == {}


def test_serving_row_does_not_step_across_a_game_boundary():
    scores = game_scores(["a"] * 21) + game_scores(["a"] * 2, start=21)
    rows = serving_row(scores, games_from_scores(scores))
    assert 21 not in rows          # first rally of game 2
    assert rows[22] == "a"


def test_serving_row_skips_unreadable_scores_without_losing_the_thread():
    scores = [score(0, 0, 0), RallyScore(1, None, None), score(2, 1, 0), score(3, 2, 0)]
    assert serving_row(scores, games_from_scores(scores)) == {2: "a", 3: "a"}


# -------------------------------------------------------------- serving side
def test_serving_side_reads_the_first_stroke_when_it_is_a_serve():
    assert serving_side(rally_strokes(0, "top")) == {0: "top"}


def test_serving_side_drops_the_rally_when_the_first_stroke_is_not_a_serve():
    # The serve was missed and the reply was detected first: its side is the
    # opposite of the answer, so using it would flip the vote.
    strokes = [serve(0, "bottom", stroke_type="殺球"), serve(0, "top", event_index=1, frame=1)]
    assert serving_side(strokes) == {}


def test_serving_side_drops_a_serve_with_no_hitter():
    assert serving_side([serve(0, None)]) == {}


def test_serving_side_orders_strokes_by_frame_not_by_list_order():
    late = serve(0, "bottom", event_index=1, frame=500, stroke_type="殺球")
    early = serve(0, "top", event_index=0, frame=100)
    assert serving_side([late, early]) == {0: "top"}


class _Dense:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def serving_side(self, segment_index):
        self.asked.append(segment_index)
        return self.answers.get(segment_index)


def test_dense_fills_only_the_rallies_the_gate_rejected():
    strokes = rally_strokes(0, "top") + [serve(1, "bottom", stroke_type="小球")]
    dense = _Dense({0: "bottom", 1: "top"})
    assert serving_side(strokes, dense) == {0: "top", 1: "top"}
    assert dense.asked == [1]          # never consulted for the gated answer


def test_dense_answer_is_ignored_when_it_is_not_a_court_position():
    strokes = [serve(0, "top", stroke_type="小球")]
    assert serving_side(strokes, _Dense({0: "sideline"})) == {}


# ------------------------------------------------------------------- inference
def build_match(games, top_row_per_epoch, serve_gate=True):
    """Scores plus strokes for a match whose true mapping is given per epoch."""
    scores, strokes, start = [], [], 0
    for game_index, winners in enumerate(games):
        scores.extend(game_scores(winners, start=start))
        start += len(winners)
    epochs = end_epochs(scores, games_from_scores(scores))
    rows = serving_row(scores, games_from_scores(scores))
    for epoch in epochs:
        top_row = top_row_per_epoch[epoch.epoch_index]
        for segment in range(epoch.first_segment, epoch.last_segment + 1):
            row = rows.get(segment)
            if row is None:
                continue
            side = "top" if row == top_row else "bottom"
            strokes.extend(rally_strokes(segment, side) if serve_gate
                           else [serve(segment, side, stroke_type="小球")])
    return strokes, scores


def test_two_game_match_resolves_both_epochs_and_the_convention():
    strokes, scores = build_match([["a"] * 21, ["b"] * 21], {0: "a", 1: "b"})
    result = infer_identity(strokes, scores)
    assert result.convention == FIXED_ROWS
    assert [(e.epoch_index, e.top, e.bottom) for e in result.epochs] == [(0, "a", "b"), (1, "b", "a")]
    assert all(e.agreement == 1.0 and e.resolved_by == "vote" for e in result.epochs)
    assert result.unresolved == []


def test_a_scoreboard_that_follows_the_court_is_detected_not_assumed():
    strokes, scores = build_match([["a"] * 21, ["b"] * 21], {0: "a", 1: "a"})
    result = infer_identity(strokes, scores)
    assert result.convention == TRACKED_ROWS
    assert [e.top for e in result.epochs] == ["a", "a"]


def test_a_sparse_epoch_is_filled_from_the_convention():
    strokes, scores = build_match(
        [["a"] * 21, ["b"] * 21, ["a"] * 21], {0: "a", 1: "b", 2: "a", 3: "b"})
    # Strip the third epoch's serves down below the vote threshold.
    epochs = end_epochs(scores, games_from_scores(scores))
    sparse = epochs[2]
    keep = {s.segment_index for s in scores
            if not (sparse.first_segment <= s.segment_index <= sparse.last_segment)}
    thin = [s for s in strokes
            if s.segment_index in keep or s.segment_index < sparse.first_segment + 2]
    result = infer_identity(thin, scores)
    filled = [e for e in result.epochs if e.epoch_index == sparse.epoch_index]
    assert len(filled) == 1
    assert filled[0].top == "a" and filled[0].resolved_by == "convention"
    assert filled[0].votes < MIN_VOTES


def test_an_epoch_with_no_evidence_and_no_convention_is_reported_not_guessed():
    strokes, scores = build_match([["a"] * 21, ["b"] * 21], {0: "a", 1: "b"})
    only_first = [s for s in strokes if s.segment_index < 3]
    result = infer_identity(only_first, scores)
    assert result.epochs == []
    assert result.convention is None
    assert [u["epoch_index"] for u in result.unresolved] == [0, 1]
    assert all(u["votes"] < MIN_VOTES for u in result.unresolved)


def test_ungated_first_strokes_yield_no_votes_at_all():
    strokes, scores = build_match([["a"] * 21, ["b"] * 21], {0: "a", 1: "b"}, serve_gate=False)
    result = infer_identity(strokes, scores)
    assert result.epochs == []
    assert all(u["votes"] == 0 for u in result.unresolved)


def test_agreement_reports_disagreement_with_the_mapping_it_emitted():
    strokes, scores = build_match([["a"] * 21, ["b"] * 21], {0: "a", 1: "b"})
    flipped = [s for s in strokes if s.segment_index != 5]
    flipped.extend(rally_strokes(5, "bottom"))         # one rally says the opposite
    result = infer_identity(flipped, scores)
    first = result.epochs[0]
    assert first.top == "a"
    assert 0.0 < first.agreement < 1.0


def test_empty_scores_produce_nothing_rather_than_an_epoch():
    assert infer_identity([], []) == infer_identity([], [])
    result = infer_identity([serve(0, "top")], [])
    assert (result.epochs, result.convention, result.unresolved) == ([], None, [])


def test_min_votes_must_be_positive():
    with pytest.raises(ValueError, match="min_votes"):
        infer_identity([], [score(0, 0, 0)], min_votes=0)
