"""Policy and stage checks without audio inference or video inputs."""
from dataclasses import replace
import json
import subprocess
import sys

import pytest

from modules.artifacts import write_artifact
from modules.base import StageState, StageStatus, read_status, write_status
from modules.contracts import AudioSegmentSignals, PIPELINE, artifact_path, stage_path
from modules.highlight_ranking import HighlightRankingModule
from modules.highlight_ranking.policy import score_audio_signal, score_audio_signals


@pytest.mark.parametrize("p,i,n,expected", [
    (1, 1, 1, 1), (0, None, 0, 0), (1, None, 0, .5),
    (0, 1, 1, .5), (.8, 0.0, 1, .4), (.3, .7, 4, .5),
])
def test_formula(p, i, n, expected):
    signal = AudioSegmentSignals(7, p, i, n)
    result = score_audio_signal(signal)
    assert result.segment_index == 7
    assert result.score == pytest.approx(expected)
    assert signal.cheer_intensity is i


@pytest.mark.parametrize("field,value", [
    ("cheer_confidence", v) for v in [-.1, 1.1, float("nan"), float("inf"), None, "0.5", True]
] + [
    ("cheer_intensity", v) for v in [-.1, 1.1, float("nan"), float("inf"), "0.5", False]
] + [
    ("n_cheer_windows", v) for v in [-1, 1.5, True]
] + [("segment_index", v) for v in [-1, 1.5, True]])
def test_invalid_fields(field, value):
    signal = replace(AudioSegmentSignals(0, .8, .4, 1), **{field: value})
    with pytest.raises(ValueError, match=field):
        score_audio_signals([signal])


@pytest.mark.parametrize("i,n", [(None, 1), (0.0, 0), (.8, 0)])
def test_support_consistency(i, n):
    with pytest.raises(ValueError, match="requires"):
        score_audio_signals([AudioSegmentSignals(0, .5, i, n)])


def test_empty_and_duplicates():
    with pytest.raises(ValueError, match="empty"):
        score_audio_signals([])
    signal = AudioSegmentSignals(3, .5, None, 0)
    with pytest.raises(ValueError, match="duplicate segment_index=3"):
        score_audio_signals([signal, signal])


def test_order_coverage_determinism_and_support_not_scored():
    signals = [AudioSegmentSignals(9, .8, .4, 2), AudioSegmentSignals(2, .2, None, 0),
               AudioSegmentSignals(5, .8, .4, 100)]
    result = score_audio_signals(signals)
    assert [s.segment_index for s in result] == [2, 5, 9]
    assert len(result) == len(signals)
    assert all(0 <= s.score <= 1 for s in result)
    assert result[1].score == result[2].score
    assert result == score_audio_signals(list(reversed(signals)))


def make_audio(match_path, rows):
    source = artifact_path(match_path, "audio_highlight")
    write_artifact(PIPELINE["audio_highlight"], rows, source)
    write_status(stage_path(match_path, "audio_highlight"),
                 StageState("audio_highlight", StageStatus.COMPLETED))
    return source


def test_stage_artifact_metadata_status_and_cli(tmp_path):
    module = HighlightRankingModule()
    assert not module.check_ready(tmp_path)
    source = make_audio(tmp_path, [AudioSegmentSignals(8, 1, None, 0),
                                   AudioSegmentSignals(2, .2, 0.0, 1)])
    original = source.read_bytes()
    assert module.check_ready(tmp_path)  # no segments, CV artifacts, or video
    progress = []
    output = module.run(tmp_path, on_progress=progress.append)
    spec = PIPELINE[module.name]
    assert spec.output_filename == "highlights.json"
    assert spec.record_key == "highlights"
    assert output == tmp_path / "stages/highlight_ranking/highlights.json"
    payload = json.loads(output.read_text())
    assert payload["highlights"] == [
        {"segment_index": 2, "score": .1}, {"segment_index": 8, "score": .5}]
    assert payload["policy"] == {
        "name": "audio_additive_v1", "confidence_weight": .5, "intensity_weight": .5,
        "null_intensity": "zero_contribution", "score_range": [0.0, 1.0],
        "ranking_order": ["score_desc", "cheer_confidence_desc", "segment_index_asc"],
    }
    state = read_status(stage_path(tmp_path, module.name))
    assert state.status == StageStatus.COMPLETED
    assert set(state.inputs) == {"audio_highlight"}
    assert progress[-1] == 1.0
    before = output.read_bytes()
    subprocess.run([sys.executable, "-m", "modules.highlight_ranking", str(tmp_path)],
                   check=True, capture_output=True)
    assert output.read_bytes() == before
    assert source.read_bytes() == original


@pytest.mark.parametrize("rows", [[], [{}], [None], [{
    "segment_index": 0, "cheer_confidence": float("nan"),
    "cheer_intensity": None, "n_cheer_windows": 0,
}]])
def test_invalid_artifact_fails_stage(tmp_path, rows):
    source = make_audio(tmp_path, [])
    source.write_text(json.dumps({"signals": rows}), encoding="utf8")
    module = HighlightRankingModule()
    with pytest.raises(ValueError):
        module.run(tmp_path)
    assert read_status(stage_path(tmp_path, module.name)).status == StageStatus.FAILED
    assert not module.get_output_path(tmp_path).exists()


def test_incomplete_audio_rejected_even_with_file(tmp_path):
    make_audio(tmp_path, [AudioSegmentSignals(0, .5, None, 0)])
    write_status(stage_path(tmp_path, "audio_highlight"),
                 StageState("audio_highlight", StageStatus.PENDING))
    module = HighlightRankingModule()
    assert not module.check_ready(tmp_path)
    with pytest.raises(RuntimeError, match="completed audio_highlight"):
        module.run(tmp_path)


def test_runner_registration_and_dependencies():
    from modules.runner import available_modules
    from modules.contracts import topological_order
    modules = available_modules()
    module = modules["highlight_ranking"]
    assert isinstance(module, HighlightRankingModule)
    assert module.dependencies == PIPELINE[module.name].dependencies == ["audio_highlight"]
    assert module.optional_dependencies == PIPELINE[module.name].optional_dependencies == []
    assert "commentary" not in modules
    order = topological_order({name: [*m.dependencies, *m.optional_dependencies]
                               for name, m in modules.items()})
    assert order.index("match_segmentation") < order.index("audio_highlight") < order.index(module.name)


def test_import_does_not_load_inference():
    subprocess.run([sys.executable, "-c",
                    "import modules.highlight_ranking; import sys; "
                    "assert not any(n == 'tensorflow' or n == 'tensorflow_hub' "
                    "or n.startswith('modules.audio_highlight') for n in sys.modules)"],
                   check=True, capture_output=True)
