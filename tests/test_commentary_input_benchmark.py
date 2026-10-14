"""Behavioral guardrails for the opt-in offline batch-read experiment."""

import json
from pathlib import Path

import pytest

from experiments.commentary.commentary_inputs import BatchReads, benchmark, provenance, source_snapshot
from modules.artifacts import write_artifact
from modules.commentary.adapters import upstream, vision
from modules.commentary.adapters.streaming_json import iter_records
from modules.contracts import PIPELINE, artifact_path, Segment, HitEvent, StrokeLabel, PoseFrame


@pytest.fixture
def match(tmp_path):
    for stage, rows, extra in [
        ("match_segmentation", [Segment(0,29,0,.967,.967), Segment(30,90,1,3,2)], {"fps":30}),
        ("event_detection", [HitEvent(10), HitEvent(30), HitEvent(45)], {}),
        ("stroke_classification", [StrokeLabel(0,10,0,None,"小球",None),
            StrokeLabel(2,45,1,"bottom","高遠球",.9), StrokeLabel(1,30,1,"top","小球",.9)], {}),
        ("score_recognition", [], {}),
        ("pose", [PoseFrame(10,0,"top",None,None), PoseFrame(30,1,"top",None,None)], {}),
    ]:
        write_artifact(PIPELINE[stage], rows, artifact_path(tmp_path, stage), extra)
    return tmp_path


def test_complete_fact_and_payload_parity(match):
    selections = [(0,"a"), (1,"b"), (1,"b")]
    baseline = benchmark(match, selections, "baseline")
    reused = benchmark(match, selections, "reuse")
    for a, b in zip(baseline["rows"], reused["rows"], strict=True):
        assert a["hashes"] == b["hashes"]
        assert a["generator_payload_hash"] == b["generator_payload_hash"]
    assert baseline["physical_read_counts"]["pose"] == 3
    assert all(count == 1 for count in reused["physical_read_counts"].values())
    assert reused["pose_records_retained"] == 2
    assert reused["peak_rss_bytes"] > 0


@pytest.mark.parametrize("text", ['{"frames":[{"segment_index":1}],"tail":',
    '{"frames":[{"segment_index":1}]} garbage', '{"frames":[],"frames":[]}',
    '{"nested":{"frames":[]}}', '{"frames":[null]}', '{"frames":[],"x":NaN}'])
def test_reuse_still_validates_complete_envelope(tmp_path, text):
    path = tmp_path / "pose.json"
    path.write_text(text)
    with BatchReads([1], True) as reads:
        with pytest.raises(ValueError):
            list(vision.iter_records(path,"frames"))
        assert not reads.pose_rows


def test_selection_memory_and_copy_isolation(tmp_path):
    path = tmp_path / "pose.json"
    path.write_text(json.dumps(dict(frames=[dict(segment_index=i, nested=[1]) for i in range(20)])))
    with BatchReads([1,5], True) as reads:
        rows = list(vision.iter_records(path,"frames"))
        assert [r["segment_index"] for r in rows] == [1,5]
        rows[0]["nested"].append(9)
        assert list(vision.iter_records(path,"frames"))[0]["nested"] == [1]
        assert reads.pose_records_seen == 20 and reads.pose_records_retained == 2
        assert reads.counts["pose"] == 1
    assert vision.iter_records is iter_records


@pytest.mark.parametrize("stage", ["pose", "score_recognition"])
def test_changed_source_aborts_reuse(match, stage):
    path = artifact_path(match,stage)
    with BatchReads([1],True):
        def read():
            return list(vision.iter_records(path,"frames")) if stage == "pose" else upstream.read_artifact(PIPELINE[stage],path)
        read()
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with pytest.raises(ValueError, match="changed during batch"):
            read()


@pytest.mark.parametrize("strategy", ["baseline", "reuse"])
def test_contract_error_not_hidden(match, strategy):
    path = artifact_path(match,"stroke_classification")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["strokes"][1]["frame"] = 46
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="frame mismatch"):
        benchmark(match,[(1,"b")],strategy)


def test_small_artifact_copy_isolation(match):
    with BatchReads([1],True) as reads:
        first = upstream.read_artifact(PIPELINE["stroke_classification"], artifact_path(match,"stroke_classification"))
        first["strokes"][0]["player"] = "changed"
        second = upstream.read_artifact(PIPELINE["stroke_classification"], artifact_path(match,"stroke_classification"))
        assert second["strokes"][0]["player"] is None
        assert reads.counts["stroke_classification"] == 1


def test_provenance_identifies_data_without_absolute_paths(match):
    snapshot = source_snapshot(match)
    result = provenance(match, [(1, "b")], snapshot)
    assert len(result["program_commit"]) == 40
    assert len(result["data_version"]) == 64
    assert result["selections"] == [dict(segment_index=1, top_player="b")]
    assert result["artifacts"]["court_detection"] is None
    for item in result["artifacts"].values():
        if item:
            assert not Path(item["path"]).is_absolute()
            assert len(item["sha256"]) == 64
    path = artifact_path(match, "pose")
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="recording provenance"):
        provenance(match, [(1, "b")], snapshot)
