"""Opt-in real-model equivalence; all outputs stay in the upstream match.

Set AUDIO_HIGHLIGHT_REFERENCE to a read-only research checkout and
AUDIO_HIGHLIGHT_MATCH to an upstream match containing identical input media
and segments. Ordinary unit runs do not import TensorFlow or the reference.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest


@pytest.mark.skipif(
    not (os.environ.get("AUDIO_HIGHLIGHT_REFERENCE") and os.environ.get("AUDIO_HIGHLIGHT_MATCH")),
    reason="opt-in real-model regression needs reference checkout and prepared match",
)
def test_real_frozen_pipeline_equivalence(monkeypatch):
    reference = Path(os.environ["AUDIO_HIGHLIGHT_REFERENCE"]).resolve()
    match = Path(os.environ["AUDIO_HIGHLIGHT_MATCH"]).resolve()
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(reference / "src"))
    from audio_highlight.full_inference import infer_full_match
    from audio_highlight.audio import NormalizedAudioSource
    from audio_highlight.intensity import rms_and_log_db
    from audio_highlight.cheer_intensity import IntensitySourceWindow, assign_cheer_intensity
    from audio_highlight.segment_signals import SegmentWindowSignal, aggregate_segment_signals
    from modules.audio_highlight import AudioHighlightModule
    from modules.audio_highlight.yamnet import configure_hub_cache
    from modules.contracts import artifact_path, resolve_input_video

    configure_hub_cache()
    cache = match / "cache" / "audio_regression"
    cache.mkdir(parents=True, exist_ok=True)
    segments_file = artifact_path(match, "match_segmentation")
    segments = json.loads(segments_file.read_text(encoding="utf-8"))["segments"]
    # No manifests, labels, evaluation artifacts or reference-side writes.
    print("Running independent reference FFmpeg + YAMNet + frozen LR", flush=True)
    result = infer_full_match(
        match_id=match.name,
        video_path=resolve_input_video(match),
        segments_path=segments_file,
        audio_cache_path=cache / "reference.f32le",
        model_dir=reference / "artifacts/models/yamnet_mean_lr_v1",
    )
    with NormalizedAudioSource(cache / "reference.f32le") as source:
        intensity_source = [IntensitySourceWindow(
            match_id=w.match_id, segment_index=w.segment_index,
            window_index_in_segment=w.window_index_in_segment,
            candidate_count_in_segment=w.candidate_count_in_segment,
            relative_window_position=w.relative_window_position,
            start_sec=w.start_sec, end_sec=w.end_sec,
            cheer_probability=w.cheer_probability,
            log_rms_db=rms_and_log_db(source.slice_absolute(w.start_sec, w.end_sec).samples)[1],
        ) for w in result.windows]
    intensity = assign_cheer_intensity(intensity_source)
    windows = [SegmentWindowSignal(**{
        key: asdict(w)[key] for key in SegmentWindowSignal.__dataclass_fields__
    }) for w in intensity]
    expected = aggregate_segment_signals(windows, list(range(len(segments))))
    print("Running independent upstream FFmpeg + YAMNet + frozen LR", flush=True)
    output = AudioHighlightModule().run(match)
    envelope = json.loads(output.read_text(encoding="utf-8"))
    actual = envelope["signals"]
    assert [w.segment_index for w in expected] == [w["segment_index"] for w in actual]
    support_equal = [w.n_cheer_windows for w in expected] == [w["n_cheer_windows"] for w in actual]
    null_equal = [w.cheer_intensity is None for w in expected] == [w["cheer_intensity"] is None for w in actual]
    assert support_equal and null_equal
    confidence_diff = max(abs(e.cheer_confidence - a["cheer_confidence"]) for e, a in zip(expected, actual, strict=True))
    intensity_diff = max((abs(e.cheer_intensity - a["cheer_intensity"]) for e, a in zip(expected, actual, strict=True)
                          if e.cheer_intensity is not None), default=0.0)
    report = {
        "segment_count": len(actual), "window_count": len(result.windows),
        "cheer_windows": sum(w.n_cheer_windows for w in expected),
        "non_null_segments": sum(w.cheer_intensity is not None for w in expected),
        "null_segments": sum(w.cheer_intensity is None for w in expected),
        "max_confidence_difference": confidence_diff,
        "max_intensity_difference": intensity_diff,
        "support_counts_equal": support_equal, "null_masks_equal": null_equal,
    }
    (cache / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    assert envelope["window_count"] == len(result.windows)
    assert confidence_diff <= 1e-12
    assert intensity_diff <= 1e-12
    assert not artifact_path(match, "highlight_ranking").exists()
