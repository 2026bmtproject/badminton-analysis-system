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
    _salvage_number_arrays,
    candidate_rank,
    format_attempts,
    image_to_jpeg_bytes,
    parse_response,
    recognize_scores,
    should_stop_retry,
)


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
