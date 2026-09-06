"""Fast production checks; only FFmpeg/TFHub boundaries are substituted."""
from dataclasses import replace
import hashlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from modules.artifacts import write_artifact
from modules.base import StageState, StageStatus, read_status, write_status
from modules.contracts import AudioSegmentSignals, PIPELINE, Segment, artifact_path, stage_path
from modules.audio_highlight import AudioHighlightModule
from modules.audio_highlight import pipeline, yamnet
from modules.audio_highlight.aggregation import (
    SegmentSignalsError, SegmentWindowSignal, aggregate_segment_signals, p95_linear,
)
from modules.audio_highlight.audio import NormalizedAudioSource, timestamp_to_sample_index
from modules.audio_highlight.detector import ExportedCheerDetector, ModelArtifactError
from modules.audio_highlight.intensity import average_rank_percentiles, rms_and_log_db
from modules.audio_highlight.windows import build_analysis_windows


def window(segment=0, index=0, count=1, probability=0.8, intensity=0.0):
    return SegmentWindowSignal("match", segment, index, count, float(index),
                               float(index + 3), probability, intensity is not None,
                               intensity, "cheer_intensity_v1")


def test_p95_supports_order_null_and_zero():
    windows = [window(2, probability=0.1, intensity=None),
               window(0, 1, 2, 0.9, 1.0), window(1), window(0, 0, 2, 0.2, None)]
    signals = aggregate_segment_signals(windows, [2, 0, 1])
    assert all(type(s) is AudioSegmentSignals for s in signals)
    assert [s.segment_index for s in signals] == [0, 1, 2]
    assert signals[0].cheer_confidence == np.quantile([0.2, 0.9], 0.95, method="linear")
    assert [s.cheer_intensity for s in signals] == [1.0, 0.0, None]
    assert [s.n_cheer_windows for s in signals] == [1, 1, 0]
    positive = [window(0, i, 3, 0.8, value) for i, value in enumerate([0.0, 0.2, 1.0])]
    result = aggregate_segment_signals(positive, [0])[0]
    assert result.cheer_intensity == np.quantile([0.0, 0.2, 1.0], 0.95, method="linear")
    assert result.n_cheer_windows == 3  # overlapping support, not three events


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")], [[0.5]]])
def test_p95_rejects_invalid_support(values):
    with pytest.raises(SegmentSignalsError):
        p95_linear(values)


def test_aggregation_rejects_missing_duplicate_and_inconsistent_windows():
    for windows, inventory in [([], [0]), ([window()], [0, 1]),
                               ([window(), window()], [0]), ([window()], [1]),
                               ([window(count=2)], [0])]:
        with pytest.raises(SegmentSignalsError):
            aggregate_segment_signals(windows, inventory)
    with pytest.raises(SegmentSignalsError, match="gate"):
        replace(window(), predicted_cheer=False)


def test_frozen_rms_and_match_ranks():
    assert rms_and_log_db(np.array([0.0, 1.0], dtype=np.float32)) == (
        np.sqrt(0.5), 20 * np.log10(np.sqrt(0.5) + 1e-12))
    assert rms_and_log_db(np.zeros(3))[1] == -240.0
    np.testing.assert_array_equal(average_rank_percentiles([9, 1, 1, 5]), [1, 1/6, 1/6, 2/3])
    np.testing.assert_array_equal(average_rank_percentiles([7]), [0.5])
    np.testing.assert_array_equal(average_rank_percentiles([7, 7]), [0.5, 0.5])
    assert average_rank_percentiles([]).size == 0


def test_planner_keeps_overlap_decimal_boundaries_and_complete_windows():
    segments = [Segment(0, 1, 0.1, 2.1, 2), Segment(2, 3, 1.1, 2.1, 1)]
    windows = build_analysis_windows(segments, media_duration_sec=4.1)
    assert [(w.segment_index, w.start_sec, w.end_sec) for w in windows] == [
        (0, 0.1, 3.1), (0, 1.1, 4.1), (1, 1.1, 4.1)]
    assert not build_analysis_windows(segments, media_duration_sec=3.099999)
    assert timestamp_to_sample_index(0.00003125) == 0
    assert timestamp_to_sample_index(0.00009375) == 2


def test_asset_integrity_and_frozen_numpy_runtime():
    assert hashlib.sha256((pipeline.MODEL_DIR / "model.npz").read_bytes()).hexdigest() == pipeline.MODEL_SHA256
    detector = ExportedCheerDetector.load(pipeline.MODEL_DIR)
    assert detector.threshold == 0.5
    assert detector.metadata["model_id"] == "yamnet_mean_lr_v1"
    assert detector.positive_probability(detector.scaler_mean) == pytest.approx(
        1 / (1 + np.exp(-detector.lr_intercept)), abs=1e-15)


@pytest.fixture
def audio_boundary(tmp_path, monkeypatch):
    audio = tmp_path / "source.f32le"
    np.concatenate([np.full(48000, v, np.float32) for v in (0.9, 0.1, 0.8)]).tofile(audio)
    monkeypatch.setattr(pipeline.FFmpegAudioNormalizer, "normalize",
                        lambda *a, **kw: NormalizedAudioSource(audio))
    detector = ExportedCheerDetector.load(pipeline.MODEL_DIR)
    vectors = iter([(detector.scaler_mean + sign * detector.lr_coef * detector.scaler_scale).astype(np.float32)
                    for sign in (-1, 1, 1)])
    monkeypatch.setattr(pipeline, "YamNetEmbeddingExtractor",
                        lambda: SimpleNamespace(embed=lambda audio: next(vectors)))


def test_module_real_math_and_serialization(tmp_path, audio_boundary):
    module = AudioHighlightModule()
    assert module.dependencies == ["match_segmentation"]
    assert not module.check_ready(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "input/match.mp4").write_bytes(b"media")
    segments = [Segment(i, i, float(i * 3), float(i * 3), 0.0) for i in range(3)]
    write_artifact(PIPELINE["match_segmentation"], segments,
                   artifact_path(tmp_path, "match_segmentation"), extra={"fps": 30.0})
    write_status(stage_path(tmp_path, "match_segmentation"),
                 StageState("match_segmentation", StageStatus.COMPLETED))
    assert module.check_ready(tmp_path)
    progress = []
    output = module.run(tmp_path, on_progress=progress.append)
    assert output == tmp_path / "stages/audio_highlight/audio_signals.json"
    payload = json.loads(output.read_text())
    assert [s["segment_index"] for s in payload["signals"]] == [0, 1, 2]
    assert [s["cheer_intensity"] for s in payload["signals"]] == [None, 0.0, 1.0]
    assert [s["n_cheer_windows"] for s in payload["signals"]] == [0, 1, 1]
    assert "highlights" not in payload
    assert not artifact_path(tmp_path, "highlight_ranking").exists()
    state = read_status(stage_path(tmp_path, module.name))
    assert state.status == StageStatus.COMPLETED
    assert "match_segmentation" in state.inputs
    assert progress[-1] == 1.0


def test_missing_complete_window_fails_before_tfhub(tmp_path, audio_boundary):
    with pytest.raises(ValueError, match="without complete"):
        pipeline.infer_signals(tmp_path / "v.mp4", [Segment(0, 1, 8.0, 9.0, 1.0)],
                               tmp_path / "a", match_id="m")


def test_hub_cache_configuration(tmp_path, monkeypatch):
    # The repository path can contain Chinese characters; use the OS temp root.
    import tempfile
    from pathlib import Path
    cache = Path(tempfile.gettempdir()) / "audio-highlight-test-cache"
    monkeypatch.setenv("TFHUB_CACHE_DIR", str(cache))
    assert yamnet.configure_hub_cache() == cache.resolve()
    monkeypatch.setenv("TFHUB_CACHE_DIR", str(tmp_path / "中文"))
    with pytest.raises(yamnet.YamNetError, match="ASCII"):
        yamnet.configure_hub_cache()


def test_hub_corrupt_cache_error_is_actionable(monkeypatch):
    monkeypatch.setattr(yamnet, "configure_hub_cache", lambda: None)
    def fail(handle):
        raise OSError("missing saved_model.pb")
    monkeypatch.setitem(sys.modules, "tensorflow", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "tensorflow_hub", SimpleNamespace(load=fail))
    with pytest.raises(yamnet.YamNetError, match="TFHUB_CACHE_DIR"):
        yamnet._load_hub_model(yamnet.YAMNET_MODEL_HANDLE)
