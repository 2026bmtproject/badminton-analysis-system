"""Error handling when inputs are missing or malformed.

Every entry point that consumes a file on disk should fail loudly (and with a
useful message) rather than silently doing the wrong thing.
"""

from __future__ import annotations

import json

import pytest

from modules.artifacts import read_artifact
from modules.contracts import PIPELINE, resolve_input_video
from modules.match_segmentation.module import MatchSegmentationModule
from modules.match_segmentation.segmenter import pick_default_video
from modules.match_segmentation.segments import load_excluded_frames, write_segments
from modules.runner import run_pipeline

_SEGMENTS_SPEC = PIPELINE["match_segmentation"]


# --------------------------------------------------------------------------- #
# resolve_input_video (matches/{match}/input/*.mp4)
# --------------------------------------------------------------------------- #


def test_resolve_input_video_missing_input_folder(tmp_path):
    with pytest.raises(FileNotFoundError, match="input folder not found"):
        resolve_input_video(tmp_path)


def test_resolve_input_video_folder_present_but_no_video(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "notes.txt").write_text("not a video")
    with pytest.raises(FileNotFoundError, match="no input video"):
        resolve_input_video(tmp_path)


def test_resolve_input_video_picks_first_video_and_ignores_non_video(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "readme.txt").write_text("x")
    (inp / "match.mp4").write_bytes(b"\x00")

    resolved = resolve_input_video(tmp_path)
    assert resolved.name == "match.mp4"


# --------------------------------------------------------------------------- #
# read_artifact / load_excluded_frames
# --------------------------------------------------------------------------- #


def test_read_segments_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="artifact not found"):
        read_artifact(_SEGMENTS_SPEC, tmp_path / "does_not_exist.json")


def test_read_segments_rejects_non_object_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([1, 2, 3]))  # a bare array, not the envelope
    with pytest.raises(ValueError, match="invalid match_segmentation artifact"):
        read_artifact(_SEGMENTS_SPEC, bad)


def test_read_segments_rejects_missing_segments_key(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"fps": 30.0}))
    with pytest.raises(ValueError, match="invalid match_segmentation artifact"):
        read_artifact(_SEGMENTS_SPEC, bad)


def test_load_excluded_frames_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_excluded_frames(str(tmp_path / "missing.json"))


def test_load_excluded_frames_expands_frame_ranges(tmp_path):
    path = tmp_path / "prev.json"
    write_segments(path, [(2, 4), (10, 11)], fps=30.0)
    assert load_excluded_frames(str(path)) == {2, 3, 4, 10, 11}


# --------------------------------------------------------------------------- #
# MatchSegmentationModule input resolution
# --------------------------------------------------------------------------- #


def test_module_explicit_input_video_not_found(tmp_path):
    module = MatchSegmentationModule(input_video="missing.mp4")
    with pytest.raises(FileNotFoundError, match="input video not found"):
        module._resolve_input_video(tmp_path)


def test_module_check_ready_false_without_video(tmp_path):
    module = MatchSegmentationModule()
    assert module.check_ready(tmp_path) is False


def test_module_check_ready_true_with_video(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "match.mp4").write_bytes(b"\x00")

    module = MatchSegmentationModule()
    assert module.check_ready(tmp_path) is True


# --------------------------------------------------------------------------- #
# runner / CLI convenience
# --------------------------------------------------------------------------- #


def test_run_pipeline_missing_match_path(tmp_path):
    with pytest.raises(FileNotFoundError, match="match path not found"):
        run_pipeline(tmp_path / "no_such_match")


def test_run_pipeline_stops_when_first_stage_not_ready(tmp_path, capsys):
    # A real, empty match path: the stage is not ready (no input video), so the
    # pipeline must stop early and report False rather than crash.
    assert run_pipeline(tmp_path) is False
    assert "not ready" in capsys.readouterr().out


def test_pick_default_video_no_mp4_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="no .mp4 found"):
        pick_default_video()
