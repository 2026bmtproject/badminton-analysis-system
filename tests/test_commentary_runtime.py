"""Synthetic production contracts; no match-specific research fixtures."""

import json
from dataclasses import replace

import pytest

from modules.artifacts import write_artifact
from modules.contracts import PIPELINE, artifact_path, Segment, HitEvent, StrokeLabel, RallyScore, HighlightScore, PoseFrame, CourtCalibration, ShuttlePoint
from modules.commentary.adapters.upstream import UpstreamStageData, read_commentary_inputs, build_rally_fact_from_stages
from modules.commentary.adapters.vision import SelectedVisionStages, CourtCalibrationPolicy
from modules.commentary.adapters.streaming_json import iter_records
from modules.commentary.identity import CourtPositionToPlayer
from modules.commentary.schemas import RallyFact
from modules.commentary.facts.builder import build_compact_rally_facts, COURT_LENGTH_M, COURT_WIDTH_M
from modules.commentary.facts.schemas import CompactRallyFacts
from modules.commentary.analysis.rally_analyzer import analyze_rally
from modules.commentary.analysis.stroke_event_analyzer import analyze_stroke_events


MAPPING = CourtPositionToPlayer(top="a", bottom="b")


@pytest.fixture
def stages():
    return UpstreamStageData(
        segments=[Segment(0, 29, 0, 0.967, 0.967), Segment(30, 90, 1, 3, 2)], fps=30,
        events=[HitEvent(10), HitEvent(30), HitEvent(45), HitEvent(60)],
        strokes=[StrokeLabel(3, 60, 1, "top", "殺球", .9),
                 StrokeLabel(1, 30, 1, "top", "小球", .9),
                 StrokeLabel(2, 45, 1, "bottom", "挑球", .9)],
        scores=[RallyScore(1, 3, 4)], shuttle_method="inpaint")


def rally(stages, mapping=MAPPING):
    return build_rally_fact_from_stages(stages=stages, segment_index=1, court_position_to_player=mapping)


def compact(stages):
    return build_compact_rally_facts(stages=stages, segment_index=1, court_position_to_player=MAPPING)


def test_selected_join_identity_order_time(stages):
    fact = rally(stages)
    assert [e.event_index for e in fact.events] == [1, 2, 3]
    assert [e.time_sec for e in fact.events] == [1, 1.5, 2]
    assert [e.stroke_type for e in fact.events] == ["小球", "挑球", "殺球"]
    assert [e.player for e in fact.events] == ["a", "b", "a"]
    assert rally(stages, CourtPositionToPlayer(top="b", bottom="a")).events[0].player == "b"
    assert fact.rally_length == 3 and fact.score.a == 3
    assert fact.server is None and fact.game_index is None
    assert fact.events[0].model_dump().get("score") is None


@pytest.mark.parametrize("change, message", [
    ({"event_index": -1}, "event_index"), ({"event_index": 99}, "event_index"),
    ({"frame": 61}, "frame mismatch"), ({"segment_index": 0}, "segment mismatch"),
])
def test_invalid_join(stages, change, message):
    stages.strokes[0] = replace(stages.strokes[0], **change)
    with pytest.raises(ValueError, match=message):
        rally(stages)


def test_duplicate_and_claim_outside(stages):
    stages.strokes.append(stages.strokes[0])
    with pytest.raises(ValueError, match="duplicate"):
        rally(stages)
    stages.strokes[-1] = StrokeLabel(0, 10, 1, None, "未知球種", .1)
    with pytest.raises(ValueError, match="segment mismatch"):
        rally(stages)


def test_mapping_required_and_unknown_preserved(stages):
    with pytest.raises(ValueError, match="mapping is required"):
        rally(stages, None)
    stages.strokes = [replace(s, player=None) for s in stages.strokes]
    fact = rally(stages, None)
    assert all(e.player is None for e in fact.events)
    assert len(analyze_stroke_events(fact)) == 3
    assert not analyze_rally(fact).patterns


def test_multi_rally_missing_score_and_highlight(stages):
    stages.scores[0].sub_scores = [[2, 4], [3, 4]]
    with pytest.raises(ValueError, match="one rally per segment"):
        rally(stages)
    stages.scores = []
    assert rally(stages).score.a is None
    assert rally(stages).highlight_score is None
    stages.highlights = [HighlightScore(0, .8)]
    assert rally(stages).highlight_score is None
    stages.highlights.append(HighlightScore(1, 0.0))
    assert rally(stages).highlight_score == 0.0
    assert rally(stages).rally_length == 3


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf"), None])
def test_invalid_fps(stages, fps):
    stages.fps = fps
    with pytest.raises(ValueError, match="fps"):
        rally(stages)


def test_invalid_segment(stages):
    with pytest.raises(ValueError, match="segment_index"):
        build_rally_fact_from_stages(stages=stages, segment_index=5, court_position_to_player=MAPPING)
    stages.segments[1].end_frame = 20
    with pytest.raises(ValueError, match="frame range"):
        rally(stages)


@pytest.mark.parametrize("change", [
    {"end_sec": .5}, {"duration_sec": 1}, {"rally_length": 2},
])
def test_rally_cross_fields(stages, change):
    with pytest.raises(ValueError):
        RallyFact.model_validate(rally(stages).model_dump() | change)


def test_rally_event_invariants(stages):
    data = rally(stages).model_dump()
    for events in ([data["events"][0]] * 3, list(reversed(data["events"])),
                   [data["events"][0] | {"time_sec": 0}, *data["events"][1:]]):
        with pytest.raises(ValueError):
            RallyFact.model_validate(data | {"events": events})
    assert RallyFact.model_validate(data | {"duration_sec": 2.001})


def pose(frame=30, keypoints=True, bbox=True):
    joints = [[2 + (i % 2) * .2, 3 + i * .1, .9] for i in range(17)]
    return PoseFrame(frame, 1, "top", joints if keypoints else None, [1, 2, 3, 5] if bbox else None)


def vision(stages, poses=None, confirmed=True, matrix=None):
    stages.vision = SelectedVisionStages(1, poses=poses if poses is not None else [pose()],
        courts=[CourtCalibration([], matrix if matrix is not None else [[1,0,0],[0,1,0],[0,0,1]], 1)],
        confirmed=confirmed)
    return stages.vision


@pytest.mark.parametrize("keypoints,bbox", [(False, False), (False, True), (True, False), (True, True)])
def test_nullable_pose(stages, keypoints, bbox):
    vision(stages, [pose(keypoints=keypoints, bbox=bbox)])
    result = compact(stages)
    assert len(result.events) == 3
    assert (result.events[0].pose is not None) == keypoints
    assert (result.events[0].court_position is not None) == (keypoints or bbox)


def test_pose_nearby_and_unavailable(stages):
    vision(stages, [pose(keypoints=False, bbox=False), pose(31)])
    assert compact(stages).events[0].pose.source_frame == 31
    stages.vision.poses = [pose(33)]
    assert compact(stages).events[0].pose is None
    stages.vision = None
    assert len(compact(stages).events) == 3


def test_pose_fact_retries_partially_populated_nearest_frame(stages):
    partial = pose(bbox=False)
    partial.keypoints = [[x, y, .1] for x, y, _ in partial.keypoints]
    partial.keypoints[9][2] = .9  # A wrist alone cannot supply a body center.
    vision(stages, [pose(31), partial])
    result = compact(stages)
    assert result.events[0].pose.source_frame == 31
    assert result.events[0].pose.frame_delta == 1
    assert len(result.events) == 3
    stages.vision.poses = [partial]
    assert compact(stages).events[0].pose is None


def test_pose_fact_candidate_order_and_tolerance(stages):
    vision(stages, [pose(62), pose(58), pose(61), pose(59)])
    assert compact(stages).events[-1].pose.source_frame == 59
    stages.vision.poses = [pose(62), pose(58)]
    assert compact(stages).events[-1].pose.source_frame == 58
    stages.vision.poses = [pose(63), pose(57)]
    assert compact(stages).events[-1].pose is None


def test_pose_fact_fallback_preserves_independent_bbox_court_input(stages):
    vision(stages, [pose(keypoints=False), pose(31, keypoints=False)])
    result = compact(stages).events[0]
    assert result.pose is None
    assert result.court_position.source_frame == 30
    assert result.court_position.position_source == "bbox_bottom_center"


def test_court_policy_and_dimensions(stages):
    assert COURT_LENGTH_M == 13.41 and COURT_WIDTH_M == 6.1
    vision(stages, confirmed=False)
    assert compact(stages).events[0].court_position is None
    stages.court_calibration_policy = CourtCalibrationPolicy.ALLOW_UNCONFIRMED
    court = compact(stages).events[0].court_position
    assert court.quality == "cautious"
    assert court.normalized_y == pytest.approx(court.court_y_m / 13.41)
    assert "court_calibration_unconfirmed_allowed_by_policy" in court.limitations


@pytest.mark.parametrize("matrix", [[], [[0,0,0]] * 3, [[float("nan"),0,0],[0,1,0],[0,0,1]], [[1,0,-100],[0,1,0],[0,0,1]]])
def test_invalid_court_degrades(stages, matrix):
    vision(stages, matrix=matrix)
    result = compact(stages)
    assert result.events[0].court_position is None
    assert result.events[0].pose is not None and len(result.events) == 3


def test_court_detection_failed(stages):
    vision(stages).detection_failed = True
    assert compact(stages).events[0].court_position is None


def test_shuttle_null_visibility_coordinates_and_methods(stages):
    selected = vision(stages)
    selected.points = [ShuttlePoint(f, 1, m, float(f), 1., True, .9)
                       for m in ("inpaint", "viterbi") for f in range(27, 34)]
    selected.points.extend([
        ShuttlePoint(34, 1, "inpaint", 34., 1., True, None),
        ShuttlePoint(35, 1, "inpaint", 35., 1., False, .9),
        ShuttlePoint(36, 1, "inpaint", None, None, True, .9)])
    fact = compact(stages).events[0].shuttle_path
    assert fact.sample_count == 10 and fact.usable_sample_count == 7
    assert fact.incoming_unit_vector == (1., 0.)
    assert "shuttle_confidence_unknown_observations_excluded" in fact.limitations
    stages.shuttle_method = None
    result = compact(stages)
    assert all(e.shuttle_path is None for e in result.events)
    assert "shuttle_method_unknown_no_direction" in result.events[0].warnings


def test_ids_roundtrip_and_provenance(stages):
    vision(stages)
    result = compact(stages)
    assert CompactRallyFacts.model_validate_json(result.model_dump_json()) == result
    assert result == compact(stages)
    assert result.events[0].fact_id == "rally:1:stroke:1"
    assert result.events[0].pose.fact_id == "rally:1:stroke:1:pose"
    analyzed = analyze_stroke_events(rally(stages))
    assert [a.stroke_index for a in analyzed] == [1,2,3]
    assert analyzed[-1].local_facts
    ids = {e.fact_id for e in result.events}
    for analysis in analyzed:
        assert analysis.should_speak
        for fact in analysis.local_facts:
            assert set(fact.supporting_fact_ids) <= ids
    for pattern in analyze_rally(rally(stages)).patterns:
        assert set(pattern.supporting_fact_ids) <= ids


@pytest.mark.parametrize("confidence", [.1, .6])
def test_low_support_skips_derived_patterns_keeps_strokes(stages, confidence):
    stages.strokes = [replace(s, confidence=confidence) if s.event_index == 2 else s for s in stages.strokes]
    fact = rally(stages)
    analyses = analyze_stroke_events(fact)
    assert len(analyses) == 3 and all(a.should_speak for a in analyses)
    assert not analyses[-1].local_facts
    assert not analyze_rally(fact).patterns
    assert len(analyze_rally(fact).candidate_strokes) == 3


@pytest.mark.parametrize("text", [
    '[]', '{"nested":{"frames":[]}}', '{"frames":[]', '{"frames":[]} false',
    '{"frames":[{},]}', '{"frames":[{} {}]}', '{"frames":[],}',
    '{"frames":[],"frames":[]}', '{"frames":[{}],"tail":',
    '{"frames":{}}', '{"frames":[null]}', '{"frames":[],"meta":NaN}',
])
def test_streaming_rejects_invalid_envelopes(tmp_path, text):
    path = tmp_path / "pose.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        list(iter_records(path, "frames"))


def test_streaming_boundaries_and_metadata(tmp_path):
    path = tmp_path / "pose.json"
    rows = [{"segment_index": 0, "text": '"frames": [' + "x" * 65536}, {"segment_index": 1}]
    path.write_text(json.dumps({"before": {"frames": []}, "frames": rows, "after": [1, 2]}))
    assert list(iter_records(path, "frames")) == rows


def test_filesystem_production_stages(tmp_path, stages):
    for stage, records, extra in [
        ("match_segmentation", stages.segments, {"fps": 30}),
        ("event_detection", stages.events, {}),
        ("stroke_classification", stages.strokes, {"shuttle_method": "inpaint"}),
        ("score_recognition", [], {}),
        ("pose", [pose(), PoseFrame(1, 0, "top", None, None)], {}),
    ]:
        write_artifact(PIPELINE[stage], records, artifact_path(tmp_path, stage), extra)
    data = read_commentary_inputs(tmp_path, 1, MAPPING)
    assert rally(data).highlight_score is None and rally(data).score.a is None
    assert len(data.vision.poses) == 1
    assert data.shuttle_method == "inpaint"
    write_artifact(PIPELINE["highlight_ranking"], [HighlightScore(1, 0.0)], artifact_path(tmp_path, "highlight_ranking"))
    assert rally(read_commentary_inputs(tmp_path, 1, MAPPING)).highlight_score == 0.0


def test_reference_rally_patterns_with_full_match_offset(stages):
    types = ["發球", "高遠球", "殺球", "小球", "撲球"]
    stages.events = [HitEvent(10)] + [HitEvent(30 + i * 10) for i in range(5)]
    stages.strokes = [StrokeLabel(i + 1, 30 + i * 10, 1,
        "top" if i % 2 == 0 else "bottom", kind, .9) for i, kind in enumerate(types)]
    analysis = analyze_rally(rally(stages))
    assert [p.name for p in analysis.patterns] == [
        "sustained_attack", "lift_to_attack_transition",
        "rear_court_stroke_to_front_court_stroke", "stroke_diversity", "serve_return_pattern"]
    assert analysis.patterns[-1].supporting_fact_ids == [f"rally:1:stroke:{i}" for i in (1, 2, 3)]
    events = analyze_stroke_events(rally(stages), context_size=2)
    assert [s.event_index for s in events[-1].previous_strokes] == [3, 4]
    with pytest.raises(ValueError, match="context_size"):
        analyze_stroke_events(rally(stages), context_size=5)


@pytest.mark.parametrize("first,second,name", [
    ("高遠球", "高遠球", "rear_exchange_continuation"),
    ("高遠球", "小球", "rear_court_stroke_to_front_court_stroke"),
    ("小球", "小球", "net_exchange_continuation"),
    ("平快球", "平快球", "flat_exchange_continuation"),
    ("小球", "挑球", "net_to_lift_transition"),
    ("挑球", "殺球", "lift_to_attack_transition"),
])
def test_reference_local_pairs(stages, first, second, name):
    stages.strokes = [replace(s, stroke_type={1: first, 2: second}.get(s.event_index, s.stroke_type)) for s in stages.strokes]
    analyses = analyze_stroke_events(rally(stages))
    assert name in [f.name for f in analyses[1].local_facts]


def test_missing_labels_and_empty_rally_are_retained(stages):
    stages.strokes = []
    fact = rally(stages, None)
    assert len(analyze_stroke_events(fact)) == len(compact(stages).events) == 3
    assert all(e.stroke_type is None for e in fact.events)
    stages.events = [HitEvent(10)]
    assert rally(stages).rally_length == 0
    assert analyze_stroke_events(rally(stages)) == []


def test_source_order_does_not_renumber(stages):
    stages.events = [HitEvent(10), HitEvent(60), HitEvent(30)]
    stages.strokes = []
    assert [e.event_index for e in rally(stages).events] == [2, 1]


def test_correct_homography_direction_and_court_edge(stages):
    p = pose(keypoints=False)
    # Court -> image doubles x and y; bbox foot maps to (3.05, 13.405).
    p.bbox = [5.1, 20, 7.1, 26.81]
    vision(stages, [p], matrix=[[2,0,0],[0,2,0],[0,0,1]])
    court = compact(stages).events[0].court_position
    assert court.court_x_m == pytest.approx(3.05)
    assert court.court_y_m == pytest.approx(13.405)
    assert court.normalized_y < 1 and court.width_zone == "center"


def test_displacement_is_between_same_player_observations(stages):
    first, last = pose(), pose(60)
    last.keypoints = [[x + 1, y, c] for x, y, c in last.keypoints]
    vision(stages, [first, last])
    facts = compact(stages)
    assert facts.events[0].court_position.displacement_from_previous_hit_m is None
    assert facts.events[-1].court_position.displacement_from_previous_hit_m == pytest.approx(1)


def test_missing_pose_geometry_and_unknown_shuttle_quality(stages):
    p = pose(bbox=False)
    p.keypoints = [[x,y,.1] for x,y,c in p.keypoints]
    selected = vision(stages, [p])
    selected.points = [ShuttlePoint(f, 1, "inpaint", float(f), 2, True, None) for f in (29,30,31)]
    result = compact(stages).events[0]
    assert result.pose is None and result.court_position is None
    assert result.shuttle_path.quality == "unavailable"
    assert result.shuttle_path.incoming_unit_vector is None
    assert selected.points[0].confidence is None


def test_streaming_validates_tail_after_selected_segment(tmp_path, stages):
    for stage, rows, extra in [
        ("match_segmentation", stages.segments, {"fps":30}),
        ("event_detection", stages.events, {}),
        ("stroke_classification", stages.strokes, {}),
        ("score_recognition", [], {}),
    ]:
        write_artifact(PIPELINE[stage], rows, artifact_path(tmp_path, stage), extra)
    path = artifact_path(tmp_path, "pose")
    path.parent.mkdir(parents=True)
    path.write_text('{"frames":[{"frame":30,"segment_index":1,"player":"top","keypoints":null,"bbox":null}],"tail":')
    with pytest.raises(ValueError):
        read_commentary_inputs(tmp_path, 1, MAPPING)
