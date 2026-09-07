from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from typing import get_type_hints

import pytest

from modules.artifacts import read_artifact, write_artifact
from modules.contracts import (
    AudioSegmentSignals,
    HighlightScore,
    PIPELINE,
    artifact_path,
    pipeline_order,
)
from modules.runner import available_modules


def test_audio_segment_signals_exact_frozen_contract() -> None:
    assert [field.name for field in fields(AudioSegmentSignals)] == [
        "segment_index",
        "cheer_confidence",
        "cheer_intensity",
        "n_cheer_windows",
    ]
    assert get_type_hints(AudioSegmentSignals) == {
        "segment_index": int,
        "cheer_confidence": float,
        "cheer_intensity": float | None,
        "n_cheer_windows": int,
    }
    missing = AudioSegmentSignals(0, 0.97, None, 0)
    low = AudioSegmentSignals(1, 0.52, 0.0, 1)
    assert missing.cheer_intensity is None
    assert low.cheer_intensity == 0.0
    assert low.cheer_intensity is not None
    with pytest.raises(FrozenInstanceError):
        missing.cheer_confidence = 0.5  # type: ignore[misc]


def test_audio_signals_artifact_round_trip_preserves_null(tmp_path) -> None:
    spec = PIPELINE["audio_highlight"]
    output = artifact_path(tmp_path, "audio_highlight")
    records = [
        AudioSegmentSignals(0, 0.97, None, 0),
        AudioSegmentSignals(1, 0.52, 0.0, 1),
    ]
    write_artifact(spec, records, output, extra={"aggregation_version": "v1"})
    envelope = read_artifact(spec, output)
    assert envelope == {
        "signals": [
            {
                "segment_index": 0,
                "cheer_confidence": 0.97,
                "cheer_intensity": None,
                "n_cheer_windows": 0,
            },
            {
                "segment_index": 1,
                "cheer_confidence": 0.52,
                "cheer_intensity": 0.0,
                "n_cheer_windows": 1,
            },
        ],
        "aggregation_version": "v1",
    }
    restored = [AudioSegmentSignals(**record) for record in envelope["signals"]]
    assert restored == records


def test_audio_highlight_stage_emits_measurements() -> None:
    spec = PIPELINE["audio_highlight"]
    assert spec.dependencies == ["match_segmentation"]
    assert spec.output_filename == "audio_signals.json"
    assert spec.record_key == "signals"
    assert spec.record_type is AudioSegmentSignals


def test_highlight_score_contract_is_owned_by_ranking_stage() -> None:
    assert [field.name for field in fields(HighlightScore)] == [
        "segment_index",
        "score",
    ]
    spec = PIPELINE["highlight_ranking"]
    assert spec.dependencies == ["audio_highlight"]
    assert spec.output_filename == "highlights.json"
    assert spec.record_key == "highlights"
    assert spec.record_type is HighlightScore


def test_pipeline_orders_measurement_ranking_and_commentary_without_cycle() -> None:
    commentary = PIPELINE["commentary"]
    assert "highlight_ranking" in commentary.optional_dependencies
    assert "highlight_ranking" not in commentary.dependencies
    assert "audio_highlight" not in commentary.dependencies
    assert "stroke_classification" in commentary.dependencies
    assert "score_recognition" in commentary.dependencies

    order = pipeline_order()
    assert order.index("match_segmentation") < order.index("audio_highlight")
    assert order.index("audio_highlight") < order.index("highlight_ranking")
    assert order.index("highlight_ranking") < order.index("commentary")
    assert len(order) == len(PIPELINE) == len(set(order))


def test_audio_and_ranking_registered_commentary_unimplemented() -> None:
    runnable = available_modules()
    from modules.audio_highlight import AudioHighlightModule
    from modules.highlight_ranking import HighlightRankingModule
    assert isinstance(runnable["audio_highlight"], AudioHighlightModule)
    assert isinstance(runnable["highlight_ranking"], HighlightRankingModule)
    assert "commentary" not in runnable
