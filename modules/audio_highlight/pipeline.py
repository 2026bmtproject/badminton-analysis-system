"""Production inference over the current upstream segments, without research inputs."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np

from modules.base import ProgressFn
from modules.contracts import AudioSegmentSignals, Segment
from modules.audio_highlight.aggregation import SegmentWindowSignal, aggregate_segment_signals
from modules.audio_highlight.audio import FFmpegAudioNormalizer, FFMPEG_NORMALIZATION_FILTER
from modules.audio_highlight.detector import ExportedCheerDetector, ModelArtifactError, _sha256
from modules.audio_highlight.intensity import average_rank_percentiles, rms_and_log_db
from modules.audio_highlight.windows import InferenceConfig, build_analysis_windows
from modules.audio_highlight.yamnet import YamNetEmbeddingExtractor, YAMNET_MODEL_HANDLE

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "yamnet_mean_lr_v1"
MODEL_SHA256 = "c5257098315fe51db163039cca95917dd482d1612e1f08b95d301a0dbf8f79f8"


def infer_signals(
    video: Path,
    segments: Sequence[Segment],
    audio_cache: Path,
    *,
    match_id: str,
    on_progress: ProgressFn | None = None,
) -> tuple[list[AudioSegmentSignals], dict]:
    """Preserve float32 pooling, whole-match LR batching and match-wide ranks."""
    if not segments:
        raise ValueError("segment inventory must not be empty")
    for index, segment in enumerate(segments):
        times = (segment.start_sec, segment.end_sec, segment.duration_sec)
        if any(isinstance(t, bool) or not np.isfinite(t) or t < 0 for t in times):
            raise ValueError(f"segment {index}: timestamps must be finite and non-negative")
        if segment.end_sec < segment.start_sec:
            raise ValueError(f"segment {index}: end precedes start")
    if _sha256(MODEL_DIR / "model.npz") != MODEL_SHA256:
        raise ModelArtifactError("packaged detector does not match the frozen model SHA-256")
    detector = ExportedCheerDetector.load(MODEL_DIR)
    planner = InferenceConfig()
    with FFmpegAudioNormalizer().normalize(video, audio_cache) as source:
        windows = build_analysis_windows(segments, planner, media_duration_sec=source.duration_sec)
        counts = Counter(window.segment_index for window in windows)
        missing = sorted(set(range(len(segments))) - set(counts))
        if missing:
            raise ValueError(f"segments without complete analysis windows: {missing}")
        extractor = YamNetEmbeddingExtractor()
        embeddings = np.empty((len(windows), 1024), dtype=np.float32)
        for index, window in enumerate(windows):
            embeddings[index] = extractor.embed(source.slice_absolute(window.start_sec, window.end_sec))
            if on_progress:
                on_progress(0.9 * (index + 1) / len(windows))
        # Keep the reference's single matrix operation: changing batch shape can
        # change floating-point rounding at the frozen detector boundary.
        probabilities = detector.positive_probabilities(embeddings)
        gated = np.flatnonzero(probabilities >= 0.5)
        loudness = [
            rms_and_log_db(source.slice_absolute(windows[i].start_sec, windows[i].end_sec).samples)[1]
            for i in gated
        ]
        intensities = dict(zip(gated, average_rank_percentiles(loudness), strict=True))
    indices: Counter = Counter()
    measurements = []
    for index, window in enumerate(windows):
        segment_index = window.segment_index
        measurements.append(SegmentWindowSignal(
            match_id=match_id,
            segment_index=segment_index,
            window_index_in_segment=indices[segment_index],
            candidate_count_in_segment=counts[segment_index],
            start_sec=window.start_sec,
            end_sec=window.end_sec,
            cheer_probability=float(probabilities[index]),
            predicted_cheer=index in intensities,
            cheer_intensity=float(intensities[index]) if index in intensities else None,
            intensity_method_id="cheer_intensity_v1",
        ))
        indices[segment_index] += 1
    signals = list(aggregate_segment_signals(measurements, list(range(len(segments)))))
    metadata = {
        "model": "yamnet_mean_lr_v1",
        "model_sha256": MODEL_SHA256,
        "yamnet": YAMNET_MODEL_HANDLE,
        "intensity_method": "cheer_intensity_v1",
        "aggregation_version": "segment_aggregation_v1",
        "aggregation": {"name": "p95_linear", "q": 0.95, "method": "linear"},
        "audio": {**asdict(planner), "filter": FFMPEG_NORMALIZATION_FILTER},
        "window_count": len(windows),
    }
    return signals, metadata
