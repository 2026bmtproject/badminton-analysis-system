"""Offline coverage and source-preservation checks for Experiment B."""

import copy
import json

import pytest

from experiments.commentary.commentary_payloads import (baseline_payload, candidate_payload, coverage,
    verify_context, measure_rally, wire_bytes, run, REMOVED_POSE, REMOVED_SHUTTLE)
from modules.commentary.analysis import observation_analyzer
from modules.commentary.analysis.rally_analyzer import analyze_rally
from modules.commentary.adapters.upstream import read_commentary_inputs, build_rally_fact_from_stages
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.identity import CourtPositionToPlayer
from modules.artifacts import write_artifact
from modules.contracts import PIPELINE, artifact_path, PoseFrame, CourtCalibration
from test_commentary_input_benchmark import match


def inputs(match):
    mapping = CourtPositionToPlayer(top="b",bottom="a")
    stages = read_commentary_inputs(match,1,mapping)
    fact = build_rally_fact_from_stages(stages=stages,segment_index=1,court_position_to_player=mapping)
    compact = build_compact_rally_facts(stages=stages,segment_index=1,court_position_to_player=mapping)
    return fact, compact, analyze_rally(fact)


def test_baseline_matches_actual_production_expression(match, monkeypatch):
    _, compact, analysis = inputs(match)
    captured = {}
    class Intercept(Exception):
        pass
    def intercept(provider,payload,schema,version):
        captured.update(payload)
        raise Intercept()
    monkeypatch.setattr(observation_analyzer,"_generate",intercept)
    with pytest.raises(Intercept):
        observation_analyzer.analyze_tactical_observations(generator=None,reviewer=None,
            compact_facts=compact,deterministic_analysis=analysis)
    assert wire_bytes(captured) == wire_bytes(baseline_payload(compact,analysis))


def test_canonical_and_null_preservation(match):
    report = run(match,[(0,"a"),(1,"b")])
    assert report["provider_calls"] == 0
    for row in report["rows"]:
        assert row["canonical_hashes_before"] == row["canonical_hashes_after"]
        assert row["raw_context_unchanged"] and row["serialization_deterministic"]
        assert row["candidate_coverage"]["coverage_percent"] == 100


@pytest.mark.parametrize("changes", [{"player":None},{"stroke_confidence":None},
    {"stroke_confidence":.3},{"stroke_confidence":.6},{"stroke_type":None}])
def test_all_raw_context_preserved(match, changes):
    _, compact, _ = inputs(match)
    compact.events[0] = compact.events[0].model_copy(update=changes)
    baseline = baseline_payload(compact,None)
    candidate = candidate_payload(baseline)
    verify_context(baseline,candidate)
    assert len(candidate["rally_view"]["events"]) == 2
    for key,value in changes.items():
        assert candidate["rally_view"]["events"][0][key] == value


@pytest.mark.parametrize("indices", [(104,97),(1,0)])
def test_nonmonotonic_gapped_identity(match,indices):
    _,compact,_ = inputs(match)
    for event,index in zip(compact.events,indices):
        event.event_index = index
        event.fact_id = f"rally:1:stroke:{index}"
    baseline = baseline_payload(compact,None)
    candidate = candidate_payload(baseline)
    assert [e["event_index"] for e in candidate["rally_view"]["events"]] == list(indices)
    assert [r["position"] for r in candidate["source_catalog"]] == [0,1]
    assert coverage(compact,candidate)["coverage_percent"] == 100


@pytest.mark.parametrize("confirmed", [True,False])
def test_court_trust_and_field_coverage(match,confirmed):
    poses = [PoseFrame(frame,1,player,[[2+.01*i,y+.01*i,.9] for i in range(17)],None)
             for frame,player,y in [(30,"top",2),(45,"bottom",10)]]
    write_artifact(PIPELINE["pose"],poses,artifact_path(match,"pose"))
    write_artifact(PIPELINE["court_detection"],[CourtCalibration(
        [[0,0],[6.1,0],[6.1,13.41],[0,13.41]], [[1,0,0],[0,1,0],[0,0,1]],None)],
        artifact_path(match,"court_detection"),dict(confirmed=confirmed,detection_failed=False))
    fact,compact,analysis=inputs(match)
    assert all((e.court_position is not None)==confirmed for e in compact.events)
    baseline=baseline_payload(compact,analysis)
    candidate=candidate_payload(baseline)
    verify_context(baseline,candidate)
    result=coverage(compact,candidate)
    assert result["eligible_fact_count"] == (4 if confirmed else 2)
    assert result["eligible_field_count"] == (6 if confirmed else 2)
    assert result["missing"] == []
    assert measure_rally(fact,compact,analysis)["canonical_inputs_unchanged"]
    for a,b in zip(baseline["compact_rally_facts"]["events"],candidate["rally_view"]["events"]):
        assert a["court_position"]==b["court_position"]
        assert a["pose"]["limitations"]==b["pose"]["limitations"]
        assert not REMOVED_POSE.intersection(b["pose"])


@pytest.mark.parametrize("mutation", ["delete_field","wrong_value","wrong_player","delete_catalog","drop_event"])
def test_coverage_checker_detects_losses(match,mutation):
    _,compact,analysis=inputs(match)
    candidate=candidate_payload(baseline_payload(compact,analysis))
    event=candidate["rally_view"]["events"][0]
    if mutation=="delete_field": del event["stroke_type"]
    elif mutation=="wrong_value": event["stroke_type"]="invented"
    elif mutation=="wrong_player": event["player"]="a"
    elif mutation=="delete_catalog": candidate["source_catalog"]=[]
    else: candidate["rally_view"]["events"].pop(0)
    assert coverage(compact,candidate)["missing"]


def test_hints_and_no_new_claims(match):
    _,compact,_=inputs(match)
    compact.events[0].stroke_type="高遠球"
    compact.events[1].stroke_type="殺球"
    baseline=baseline_payload(compact,None)
    assert baseline["optional_pattern_hints"]
    candidate=candidate_payload(baseline)
    assert candidate["optional_pattern_hints"]==baseline["optional_pattern_hints"]
    assert candidate["source_catalog"]==baseline["source_catalog"]
    assert wire_bytes(candidate)==wire_bytes(candidate_payload(copy.deepcopy(baseline)))
    verify_context(baseline,candidate)


def test_shuttle_quality_and_limitations_retained(match):
    from modules.commentary.facts.schemas import CompactShuttlePathFact
    _,compact,analysis=inputs(match)
    e=compact.events[0]
    e.shuttle_path=CompactShuttlePathFact(fact_id=e.fact_id+":shuttle",start_frame=29,end_frame=31,
        coordinate_space="image",quality="unavailable",sample_count=2,usable_sample_count=0,usable_ratio=0,
        incoming_unit_vector=None,outgoing_unit_vector=None,incoming_direction=None,outgoing_direction=None,
        limitations=["unknown confidence", "image space only"])
    baseline=baseline_payload(compact,analysis)
    candidate=candidate_payload(baseline)
    verify_context(baseline,candidate)
    shuttle=candidate["rally_view"]["events"][0]["shuttle_path"]
    assert shuttle["limitations"]==e.shuttle_path.limitations
    assert shuttle["quality"]=="unavailable" and shuttle["usable_ratio"]==0
    assert not REMOVED_SHUTTLE.intersection(shuttle)


@pytest.mark.parametrize("bad", ["tail", "join"])
def test_original_source_errors_preserved(match,bad):
    if bad=="tail":
        artifact_path(match,"pose").write_text('{"frames":[],"tail":',encoding="utf-8")
    else:
        p=artifact_path(match,"stroke_classification")
        value=json.loads(p.read_text(encoding="utf-8"))
        value["strokes"][1]["frame"]=46
        p.write_text(json.dumps(value),encoding="utf-8")
    with pytest.raises(ValueError):
        run(match,[(1,"b")])
