"""Config parsing.

There is no YAML config file yet (``config.yaml.example`` is only a placeholder
comment), so the parsed configuration surfaces are:

* the ``SegmentationConfig`` dataclass and how the CLI (``parse_args``) maps
  flags onto it, and
* ``StageState`` <-> ``status.json`` (de)serialization, the per-stage state the
  runner reads back.
"""

from __future__ import annotations

import json

import pytest

from modules.base import StageState, StageStatus, read_status, write_status
from modules.common.config import (
    GEMINI_API_KEY_ENV,
    get_gemini_api_key,
    load_config,
)
from modules.match_segmentation.segmenter import (
    DEFAULT_AVG_THR_SCALE,
    DEFAULT_FINAL_MERGE_GAP,
    DEFAULT_FRAME_STEP,
    DEFAULT_MERGE_MIN_RATIO,
    DEFAULT_MIN_SEGMENT_SECONDS,
    DEFAULT_SCAN_MAX_HEIGHT,
    SegmentationConfig,
    parse_args,
)


# --------------------------------------------------------------------------- #
# SegmentationConfig + CLI parsing
# --------------------------------------------------------------------------- #


def test_segmentation_config_defaults():
    cfg = SegmentationConfig()
    assert cfg.merge_min_ratio == DEFAULT_MERGE_MIN_RATIO
    assert cfg.min_segment_seconds == DEFAULT_MIN_SEGMENT_SECONDS
    assert cfg.avg_thr_scale == DEFAULT_AVG_THR_SCALE
    assert cfg.avg_thr_pct is None
    assert cfg.frame_step == DEFAULT_FRAME_STEP
    assert cfg.final_merge_gap == DEFAULT_FINAL_MERGE_GAP
    assert cfg.scan_max_height == DEFAULT_SCAN_MAX_HEIGHT


def test_parse_args_uses_defaults_when_no_flags(monkeypatch):
    monkeypatch.setattr("sys.argv", ["segmenter", "in.mp4", "out.json"])
    args = parse_args()

    assert args.video_path == "in.mp4"
    assert args.output_json == "out.json"
    assert args.avg_thr_scale == DEFAULT_AVG_THR_SCALE
    assert args.merge_min_ratio == DEFAULT_MERGE_MIN_RATIO
    assert args.avg_thr_pct is None
    assert args.frame_step == DEFAULT_FRAME_STEP
    assert args.final_merge_gap == DEFAULT_FINAL_MERGE_GAP
    assert args.scan_max_height == DEFAULT_SCAN_MAX_HEIGHT
    assert args.exclude_path is None


def test_parse_args_positional_paths_are_optional(monkeypatch):
    monkeypatch.setattr("sys.argv", ["segmenter"])
    args = parse_args()
    assert args.video_path is None
    assert args.output_json is None


def test_parse_args_maps_flags_onto_config(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "segmenter",
            "in.mp4",
            "out.json",
            "--avg-thr-scale", "0.4",
            "--merge-min-ratio", "0.8",
            "--avg-thr-pct", "0.05",
            "--frame-step", "5",
            "--final-merge-gap", "10",
            "--scan-max-height", "720",
            "--exclude", "prev.json",
        ],
    )
    args = parse_args()

    # This mirrors how main() builds the config from the namespace.
    cfg = SegmentationConfig(
        merge_min_ratio=args.merge_min_ratio,
        avg_thr_scale=args.avg_thr_scale,
        avg_thr_pct=args.avg_thr_pct,
        frame_step=max(int(args.frame_step), 1),
        final_merge_gap=int(args.final_merge_gap),
        scan_max_height=int(args.scan_max_height),
    )

    assert cfg.avg_thr_scale == 0.4
    assert cfg.merge_min_ratio == 0.8
    assert cfg.avg_thr_pct == 0.05
    assert cfg.frame_step == 5
    assert cfg.final_merge_gap == 10
    assert cfg.scan_max_height == 720
    assert args.exclude_path == "prev.json"


def test_parse_args_rejects_non_numeric_frame_step(monkeypatch):
    monkeypatch.setattr("sys.argv", ["segmenter", "in.mp4", "out.json", "--frame-step", "abc"])
    with pytest.raises(SystemExit):
        parse_args()


# --------------------------------------------------------------------------- #
# config.yaml loader + Gemini API key resolution
# --------------------------------------------------------------------------- #


def test_load_config_missing_file_returns_empty(tmp_path):
    assert load_config(tmp_path / "config.yaml") == {}


def test_load_config_reads_yaml(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("gemini_api_key: from-file\n", encoding="utf-8")
    assert load_config(cfg) == {"gemini_api_key": "from-file"}


def test_get_gemini_api_key_prefers_env_over_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("gemini_api_key: from-file\n", encoding="utf-8")
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "from-env")
    assert get_gemini_api_key(cfg) == "from-env"


def test_get_gemini_api_key_falls_back_to_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("gemini_api_key: from-file\n", encoding="utf-8")
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert get_gemini_api_key(cfg) == "from-file"


def test_get_gemini_api_key_none_when_unset_everywhere(tmp_path, monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert get_gemini_api_key(tmp_path / "config.yaml") is None


# --------------------------------------------------------------------------- #
# StageState <-> status.json
# --------------------------------------------------------------------------- #


def test_stage_state_round_trip_through_dict():
    state = StageState(
        name="match_segmentation",
        status=StageStatus.COMPLETED,
        started_at="2026-07-03T00:00:00+00:00",
        finished_at="2026-07-03T00:01:00+00:00",
        output_path="stages/match_segmentation/segments.json",
    )
    restored = StageState.from_dict(state.to_dict())
    assert restored == state


def test_stage_state_to_dict_serializes_status_as_plain_string():
    d = StageState(name="s", status=StageStatus.RUNNING).to_dict()
    assert d["status"] == "running"
    # Must be a plain JSON string, not an Enum repr.
    assert json.dumps(d)


def test_stage_state_from_dict_defaults_missing_status_to_pending():
    restored = StageState.from_dict({"name": "s"})
    assert restored.status == StageStatus.PENDING


def test_read_status_missing_returns_none(tmp_path):
    assert read_status(tmp_path) is None


def test_write_then_read_status_round_trip(tmp_path):
    state = StageState(name="match_segmentation", status=StageStatus.FAILED, error="boom")
    write_status(tmp_path, state)

    restored = read_status(tmp_path)
    assert restored is not None
    assert restored.name == "match_segmentation"
    assert restored.status == StageStatus.FAILED
    assert restored.error == "boom"
    assert restored.updated_at is not None  # stamped on write
