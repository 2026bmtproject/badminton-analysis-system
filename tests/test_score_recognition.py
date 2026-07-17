"""Score-recognition logic: turning a Gemini response into a RallyScore.

These cover the pure, offline pieces of the stage — response parsing/salvage,
value coercion, candidate ranking, the rate limiter, and the per-segment
orchestration in ``recognize_scores`` (indexing, ordering, error handling).
Nothing here hits the network or decodes a real video: ``score_segment`` is
monkeypatched so the orchestration is tested in isolation.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from modules.contracts import RallyScore
from modules.score_recognition import recognizer
from modules.score_recognition.recognizer import (
    RateLimiter,
    ScoreRecognitionConfig,
    _coerce_int,
    _coerce_int_list,
    _is_score_jump,
    _resolve_runs,
    _salvage_number_arrays,
    candidate_rank,
    format_attempts,
    image_to_jpeg_bytes,
    parse_response,
    recognize_scores,
    refine_merged_segment,
    should_stop_retry,
)


def scoreboard_reader(timeline, absent=()):
    """A synthetic ``read_fn(a, b)`` over a scripted scoreboard timeline.

    ``timeline`` is ``[(change_frame, (a, b)), ...]`` ascending — each score holds
    from its ``change_frame`` until the next. The reader returns the *dominant*
    (most-frames) score in ``[a, b]``, mimicking a whole-window composite. Frames
    inside any ``absent`` ``(lo, hi)`` range have no visible scoreboard, so a
    window entirely within one reads ``None``.
    """
    def score_at(f):
        s = timeline[0][1]
        for cf, sc in timeline:
            if f >= cf:
                s = sc
        return s

    def read_fn(a, b):
        counts = {}
        seen = False
        for f in range(int(a), int(b) + 1):
            if any(lo <= f <= hi for lo, hi in absent):
                continue
            seen = True
            counts[score_at(f)] = counts.get(score_at(f), 0) + 1
        if not seen:
            return None
        return max(counts, key=counts.get)

    return read_fn


def gemini_response(text: str, finish: str = "STOP") -> dict:
    """Wrap raw model text in the Gemini response envelope parse_response reads."""
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


# --------------------------------------------------------------------------- #
# parse_response — the core "response -> scores" logic
# --------------------------------------------------------------------------- #


def test_parse_response_reads_single_number_per_team():
    result = parse_response(gemini_response('{"all_a": [11], "all_b": [9]}'))
    assert result["score_a"] == 11
    assert result["score_b"] == 9


def test_parse_response_current_score_is_rightmost_of_multi_game_row():
    # Match in game 3: the current score is the last number, not the first.
    result = parse_response(gemini_response('{"all_a": [21, 15, 8], "all_b": [19, 21, 11]}'))
    assert result["score_a"] == 8
    assert result["score_b"] == 11
    assert result["all_a"] == [21, 15, 8]


def test_parse_response_strips_markdown_fences():
    text = '```json\n{"all_a": [5], "all_b": [7]}\n```'
    result = parse_response(gemini_response(text))
    assert (result["score_a"], result["score_b"]) == (5, 7)


def test_parse_response_scoreboard_not_visible_returns_none():
    result = parse_response(gemini_response('{"all_a": [], "all_b": []}'))
    assert result["score_a"] is None
    assert result["score_b"] is None


def test_parse_response_falls_back_to_flat_score_keys():
    # Older/looser model output that gives flat score_a/score_b instead of arrays.
    result = parse_response(gemini_response('{"score_a": 3, "score_b": 4}'))
    assert (result["score_a"], result["score_b"]) == (3, 4)


def test_parse_response_salvages_truncated_json():
    # MAX_TOKENS cut the response mid-array: no closing brace/bracket.
    text = '{"all_a": [21, 15, 8], "all_b": [19, 21'
    result = parse_response(gemini_response(text, finish="MAX_TOKENS"))
    assert result["score_a"] == 8
    assert result["score_b"] == 21
    assert "salvaged" in result["note"]


def test_parse_response_unrecoverable_text_reports_parse_error():
    result = parse_response(gemini_response("totally not json", finish="MAX_TOKENS"))
    assert result["score_a"] is None
    assert "parse error" in result["note"]
    assert "MAX_TOKENS" in result["note"]


def test_parse_response_empty_candidates():
    result = parse_response({"candidates": []})
    assert result == {"score_a": None, "score_b": None, "note": "empty response"}


def test_parse_response_missing_text_notes_finish_reason():
    data = {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}
    result = parse_response(data)
    assert result["score_a"] is None
    assert "SAFETY" in result["note"]


# --------------------------------------------------------------------------- #
# small coercion / salvage helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value,expected",
    [(5, 5), ("7", 7), (None, None), ("x", None), ([1], None), (3.9, 3)],
)
def test_coerce_int(value, expected):
    assert _coerce_int(value) == expected


def test_coerce_int_list_drops_unparseable_and_keeps_order():
    assert _coerce_int_list([1, "2", "bad", None, 4]) == [1, 2, 4]


def test_coerce_int_list_non_list_returns_empty():
    assert _coerce_int_list("21") == []
    assert _coerce_int_list(None) == []


def test_salvage_number_arrays_from_partial_body():
    salvaged = _salvage_number_arrays('{"all_a": [1, 0], "all_b": [2')
    assert salvaged == {"all_a": [1, 0], "all_b": [2]}


def test_salvage_number_arrays_none_present():
    assert _salvage_number_arrays("no arrays here") == {}


# --------------------------------------------------------------------------- #
# candidate ranking (drives the composite-method fallback)
# --------------------------------------------------------------------------- #


def test_candidate_rank_prefers_both_scores_present():
    assert candidate_rank({"score_a": 1, "score_b": 2}) == 1
    assert candidate_rank({"score_a": 1, "score_b": None}) == 0
    assert candidate_rank({"score_a": None, "score_b": None}) == 0


def test_should_stop_retry_only_when_both_scores_present():
    assert should_stop_retry({"score_a": 1, "score_b": 2}) is True
    assert should_stop_retry({"score_a": 1, "score_b": None}) is False


def test_format_attempts_summarizes_method_and_scores():
    attempts = [
        {"method": "dominant_cluster", "score_a": None, "score_b": None},
        {"method": "median", "score_a": 11, "score_b": 9},
    ]
    assert format_attempts(attempts) == "dominant_cluster:None:None; median:11:9"


# --------------------------------------------------------------------------- #
# image encoding
# --------------------------------------------------------------------------- #


def test_image_to_jpeg_bytes_produces_a_jpeg():
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    data = image_to_jpeg_bytes(img)
    # JPEG SOI marker.
    assert data[:2] == b"\xff\xd8"


# --------------------------------------------------------------------------- #
# RateLimiter — caps API calls at rpm across workers
# --------------------------------------------------------------------------- #


def test_rate_limiter_spaces_out_calls():
    # 120 rpm == 2 tokens/sec, burst of 1: the 2nd acquire must wait ~0.5s.
    limiter = RateLimiter(rpm=120.0, burst=1)
    limiter.acquire()  # consumes the initial token immediately
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.4


def test_rate_limiter_burst_allows_immediate_calls():
    limiter = RateLimiter(rpm=60.0, burst=3)
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    # Three tokens are available up front, so no meaningful wait.
    assert time.monotonic() - start < 0.3


# --------------------------------------------------------------------------- #
# recognize_scores — per-segment orchestration (score_segment stubbed out)
# --------------------------------------------------------------------------- #


def _stub_score_segment(monkeypatch):
    """Make score_segment echo the segment's start_frame as both scores."""
    def fake(video_path, start_frame, end_frame, api_key, config, **kwargs):
        best = {
            "score_a": start_frame,
            "score_b": end_frame,
            "method": "dominant_cluster",
            "note": "",
        }
        return best, [dict(best, method="dominant_cluster")]

    monkeypatch.setattr(recognizer, "score_segment", fake)


def test_recognize_scores_uses_zero_based_segment_index(monkeypatch):
    _stub_score_segment(monkeypatch)
    segments = [
        {"start_frame": 10, "end_frame": 20},
        {"start_frame": 30, "end_frame": 40},
        {"start_frame": 50, "end_frame": 60},
    ]

    rallies, meta = recognize_scores("dummy.mp4", segments, api_key="k")

    assert [r.segment_index for r in rallies] == [0, 1, 2]
    assert [m["segment_index"] for m in meta["attempts"]] == [0, 1, 2]


def test_recognize_scores_keeps_results_in_segment_order(monkeypatch):
    _stub_score_segment(monkeypatch)
    segments = [
        {"start_frame": 10, "end_frame": 20},
        {"start_frame": 30, "end_frame": 40},
    ]

    rallies, _ = recognize_scores(
        "dummy.mp4", segments, api_key="k",
        config=ScoreRecognitionConfig(concurrency=4),
    )

    # score_a echoes start_frame, so order maps back to the input segments.
    assert [(r.score_a, r.score_b) for r in rallies] == [(10, 20), (30, 40)]


def test_recognize_scores_records_failure_as_note_without_crashing(monkeypatch):
    def boom(video_path, start_frame, end_frame, api_key, config, **kwargs):
        raise RuntimeError("no frames decoded")

    monkeypatch.setattr(recognizer, "score_segment", boom)
    segments = [{"start_frame": 10, "end_frame": 20}]

    rallies, meta = recognize_scores("dummy.mp4", segments, api_key="k")

    assert len(rallies) == 1
    assert rallies[0].score_a is None
    assert rallies[0].score_b is None
    assert "no frames decoded" in meta["attempts"][0]["note"]


def test_recognize_scores_reports_progress_to_completion(monkeypatch):
    _stub_score_segment(monkeypatch)
    segments = [{"start_frame": i, "end_frame": i + 1} for i in range(4)]
    seen: list[float] = []

    recognize_scores(
        "dummy.mp4", segments, api_key="k",
        on_progress=seen.append,
    )

    assert len(seen) == 4
    assert seen[-1] == pytest.approx(1.0)


def test_recognize_scores_empty_segments_returns_empty(monkeypatch):
    _stub_score_segment(monkeypatch)
    rallies, meta = recognize_scores("dummy.mp4", [], api_key="k")
    assert rallies == []
    assert meta["attempts"] == []


# --------------------------------------------------------------------------- #
# merged-segment refine — jump detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        ((2, 2), (3, 2), False),   # normal single rally, +1 one side
        ((2, 2), (2, 3), False),   # normal single rally, +1 other side
        ((2, 2), (3, 3), True),    # merged: +1 to both (seg41 case)
        ((3, 3), (5, 3), True),    # merged: +2 to one side (seg42 case)
        ((11, 9), (13, 9), True),  # merged: +2 (seg55 case)
        ((15, 20), (0, 0), False), # game reset is not a jump
        ((5, 3), (5, 2), False),   # score going backwards = misread, not a merge
        ((2, 2), (2, 2), False),   # unchanged (duplicate/dead), not a merge
    ],
)
def test_is_score_jump(prev, cur, expected):
    assert _is_score_jump(prev, cur) is expected


# --------------------------------------------------------------------------- #
# merged-segment refine — pure bisection resolver
# --------------------------------------------------------------------------- #


def test_resolve_runs_single_rally_returns_one_run():
    read_fn = scoreboard_reader([(0, (3, 3))])
    runs = _resolve_runs(read_fn, 0, 500, window=50)
    assert runs == [((3, 3), 0)]


def test_resolve_runs_finds_one_transition_near_the_change():
    # seg41 shape: 3:2 for the first half, 3:3 after frame 272.
    read_fn = scoreboard_reader([(0, (3, 2)), (272, (3, 3))])
    runs = _resolve_runs(read_fn, 0, 545, window=50)
    scores = [s for s, _ in runs]
    assert scores == [(3, 2), (3, 3)]
    boundary = runs[1][1]
    assert abs(boundary - 272) <= 50   # localized to within the window


def test_resolve_runs_handles_short_early_rally_needing_recursion():
    # seg42 shape: a short 4:3 rally at the very start, then 5:3 dominates.
    read_fn = scoreboard_reader([(0, (4, 3)), (116, (5, 3))])
    runs = _resolve_runs(read_fn, 0, 935, window=50)
    scores = [s for s, _ in runs]
    assert scores == [(4, 3), (5, 3)]
    assert abs(runs[1][1] - 116) <= 50


def test_resolve_runs_finds_two_transitions():
    read_fn = scoreboard_reader([(0, (5, 5)), (300, (6, 5)), (600, (6, 6))])
    runs = _resolve_runs(read_fn, 0, 900, window=50)
    assert [s for s, _ in runs] == [(5, 5), (6, 5), (6, 6)]


def test_resolve_runs_missing_scoreboard_does_not_crash():
    # The board is absent for the whole segment: no run can be resolved.
    read_fn = scoreboard_reader([(0, (3, 3))], absent=[(0, 500)])
    runs = _resolve_runs(read_fn, 0, 500, window=50)
    assert runs == []


def test_refine_merged_segment_reports_scores_and_seconds():
    # 25 fps, change at frame 272 -> 10.88s.
    read_fn = scoreboard_reader([(0, (3, 2)), (272, (3, 3))])
    sub_scores, split_secs = refine_merged_segment(
        read_fn, start_frame=0, end_frame=545, fps=25.0, min_split_sec=2.0,
    )
    assert sub_scores == [[3, 2], [3, 3]]
    assert len(split_secs) == 1
    assert split_secs[0] == pytest.approx(272 / 25.0, abs=2.0)  # within window/fps


def test_refine_merged_segment_single_rally_returns_none():
    read_fn = scoreboard_reader([(0, (7, 4))])
    assert refine_merged_segment(read_fn, 0, 400, 25.0) == (None, None)


# --------------------------------------------------------------------------- #
# merged-segment refine — end-to-end through recognize_scores
# --------------------------------------------------------------------------- #


def test_recognize_scores_refines_a_merged_segment(monkeypatch):
    """First pass reads final scores; the jump on seg1 triggers a bisection that
    recovers the intermediate rally into sub_scores/split_secs."""
    segments = [
        {"start_frame": 0, "end_frame": 100},      # seg0 -> 2:2
        {"start_frame": 200, "end_frame": 745},    # seg1 -> merged 3:2, 3:3
        {"start_frame": 800, "end_frame": 900},    # seg2 -> 4:3
    ]
    finals = {0: (2, 2), 1: (3, 3), 2: (4, 3)}

    def fake_score_segment(video_path, start_frame, end_frame, api_key, config, **kwargs):
        # Map a segment by its start_frame to its final score.
        a, b = finals[next(i for i, s in enumerate(segments)
                           if s["start_frame"] == start_frame)]
        best = {"score_a": a, "score_b": b, "method": "dominant_cluster", "note": ""}
        return best, [dict(best)]

    # Refine reads windows inside seg1 [200,745]; scoreboard flips at frame 472.
    window_reader = scoreboard_reader([(200, (3, 2)), (472, (3, 3))])

    def fake_window(video_path, start, end, api_key, config, rate_limiter=None, stop_event=None):
        return window_reader(start, end)

    monkeypatch.setattr(recognizer, "score_segment", fake_score_segment)
    monkeypatch.setattr(recognizer, "_read_window_score", fake_window)

    rallies, meta = recognize_scores("dummy.mp4", segments, api_key="k", fps=25.0)

    merged = rallies[1]
    assert (merged.score_a, merged.score_b) == (3, 3)   # scalar stays the final score
    assert merged.sub_scores == [[3, 2], [3, 3]]
    assert len(merged.split_secs) == 1
    # Untouched segments carry no sub-scores.
    assert rallies[0].sub_scores is None
    assert rallies[2].sub_scores is None


def test_recognize_scores_without_fps_skips_refine(monkeypatch):
    """No fps -> refine pass is off, behaviour is exactly as before."""
    segments = [
        {"start_frame": 0, "end_frame": 100},
        {"start_frame": 200, "end_frame": 745},
    ]
    finals = {0: (2, 2), 1: (3, 3)}

    def fake_score_segment(video_path, start_frame, end_frame, api_key, config, **kwargs):
        idx = 0 if start_frame == 0 else 1
        a, b = finals[idx]
        best = {"score_a": a, "score_b": b, "method": "dominant_cluster", "note": ""}
        return best, [dict(best)]

    def boom(*a, **k):  # refine must not be called
        raise AssertionError("refine ran without fps")

    monkeypatch.setattr(recognizer, "score_segment", fake_score_segment)
    monkeypatch.setattr(recognizer, "_read_window_score", boom)

    rallies, _ = recognize_scores("dummy.mp4", segments, api_key="k")  # no fps
    assert rallies[1].sub_scores is None


def test_recognized_rallies_round_trip_through_scores_io(monkeypatch, tmp_path):
    """A recognized rally is a RallyScore and survives the artifact writer."""
    from modules.artifacts import read_artifact, write_artifact
    from modules.contracts import PIPELINE

    spec = PIPELINE["score_recognition"]

    _stub_score_segment(monkeypatch)
    segments = [{"start_frame": 10, "end_frame": 20}]
    rallies, _ = recognize_scores("dummy.mp4", segments, api_key="k")

    assert isinstance(rallies[0], RallyScore)
    out = tmp_path / "scores.json"
    write_artifact(spec, rallies, out, extra={"model": "gemini-2.5-flash"})

    data = read_artifact(spec, out)
    assert data["model"] == "gemini-2.5-flash"
    assert data["rallies"][0]["segment_index"] == 0
    assert data["rallies"][0]["score_a"] == 10
