"""Unit tests for the shared BST module.

No weight file, no GPU: the network is built with random weights, which is enough for
everything that can actually break here. The risk in this module is not the architecture —
that is a verbatim port of the reference implementation, and nothing about it is a
judgement call — but the two seams around it: the artifacts -> features adapter (where a
court flipped upside down would swap Top and Bottom in every prediction and still look like
a working model), and the windowing/batching (where an off-by-one shifts every prediction
by a frame).

``between_hits_windows`` is exercised in ``tests/test_stroke_classification.py``, next to
its only caller and to the frozen reference it is pinned against.
"""

from __future__ import annotations

import numpy as np
import pytest

from modules.artifacts import write_artifact
from modules.common.bst import (
    BOTTOM_INDICES,
    CLASSES_8,
    IN_DIM,
    N_CLASSES,
    SEQ_LEN,
    STROKE_CLASSES,
    TOP_INDICES,
    UNKNOWN_INDEX,
    SegmentFeatures,
    build_bst_model,
    build_window,
    predict_windows,
    to8,
    to_base,
    to_side,
)
from modules.common.bst import adapter
from modules.common.bst.classes import BASE_STROKES, L_ANKLE, NUM_KEYPOINTS, R_ANKLE
from modules.common.bst.features import create_bones, make_seq_len_same, normalize_joints
from modules.contracts import PIPELINE, POSE_PLAYERS, stage_path

# A head-on camera, same one test_pose uses: 100 px per metre, the court's far-left
# corner at image (200, 100), no perspective. Court metres -> image pixels.
SCALE, ORIGIN_X, ORIGIN_Y = 100.0, 200.0, 100.0
COURT_TO_IMAGE = [
    [SCALE, 0.0, ORIGIN_X],
    [0.0, SCALE, ORIGIN_Y],
    [0.0, 0.0, 1.0],
]
COURT_WIDTH_M, COURT_LENGTH_M = 6.10, 13.41
VIDEO_SIZE = (1920, 1080)
FPS = 30.0


def image_of(x_m: float, y_m: float) -> tuple[float, float]:
    """Where a point on the court, in metres, lands in the image."""
    return (ORIGIN_X + SCALE * x_m, ORIGIN_Y + SCALE * y_m)


# --------------------------------------------------------------------------- #
# Label space
# --------------------------------------------------------------------------- #


def test_class_list_matches_the_checkpoints_head():
    assert len(STROKE_CLASSES) == N_CLASSES == 25
    assert STROKE_CLASSES[0] == "未知球種"
    assert len(TOP_INDICES) == len(BOTTOM_INDICES) == 12
    assert UNKNOWN_INDEX == 0
    # Every logit is accounted for exactly once: the side sums event_detection builds
    # would otherwise quietly drop or double-count a stroke.
    assert sorted([UNKNOWN_INDEX, *TOP_INDICES, *BOTTOM_INDICES]) == list(range(N_CLASSES))


def test_side_is_reported_the_way_the_rest_of_the_pipeline_names_players():
    assert to_side("Top_殺球") == "top"
    assert to_side("Bottom_殺球") == "bottom"
    assert to_side("未知球種") is None
    assert {to_side("Top_殺球"), to_side("Bottom_殺球")} == set(POSE_PLAYERS)


def test_every_base_stroke_merges_into_one_of_the_eight_reported_classes():
    assert to_base("Top_放小球") == "放小球"
    assert to_base("未知球種") is None
    assert to8(None) is None
    assert {to8(s) for s in BASE_STROKES} == set(CLASSES_8)
    # The two merges that exist because BST cannot separate them.
    assert to8("長球") == to8("挑球") == "高遠球"
    assert to8("發短球") == to8("發長球") == "發球"


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


def random_features(n_frames: int, seed: int = 0) -> SegmentFeatures:
    rng = np.random.default_rng(seed)
    joints = rng.random((n_frames, 2, NUM_KEYPOINTS, 2), dtype=np.float32)
    joints[joints < 0.1] = 0.0                        # some joints are missing
    return SegmentFeatures(
        joints=joints,
        positions=rng.random((n_frames, 2, 2), dtype=np.float32),
        shuttle=rng.random((n_frames, 2), dtype=np.float32),
        start_frame=1000,
    )


def test_normalized_joints_are_free_of_the_players_size_on_screen():
    # The same pose, once near the camera and once far away: twice the pixels, same shape.
    pose = np.array([[[10.0, 20.0], [30.0, 60.0]]])          # (1, 2, 2)
    small_box = np.array([[0.0, 0.0, 40.0, 80.0]])
    big = normalize_joints(pose * 2, small_box * 2)
    small = normalize_joints(pose, small_box)
    assert np.allclose(big, small)


def test_a_missing_joint_is_never_confused_with_a_real_one():
    """Pins the centre-align quirk the weights were fitted with — see normalize_joints.

    A missing joint does not come out at zero: it skips the bbox subtraction but not the
    centre alignment, so it lands at ``-centre``. What matters is that this is one fixed
    value, well outside the box, and so cannot be mistaken for an observed joint.
    """
    box = np.array([[0.0, 0.0, 40.0, 80.0]])                 # centre (20, 40), diagonal 89.44
    pose = np.array([[[0.0, 0.0], [30.0, 60.0]]])            # first joint absent
    normalized = normalize_joints(pose, box)

    diagonal = np.hypot(40.0, 80.0)                          # 89.44
    assert normalized[0, 0] == pytest.approx([-20.0 / diagonal, -40.0 / diagonal])
    # The real joint is measured from the box centre. It can only reach -centre by sitting
    # exactly on the box's top-left corner — which is the pixel (0, 0) that *means* missing
    # in the first place, so the two can never collide.
    assert normalized[0, 1] == pytest.approx([10.0 / diagonal, 20.0 / diagonal])


def test_a_bone_needs_both_of_its_ends():
    joints = np.zeros((1, 2, NUM_KEYPOINTS, 2), dtype=np.float32)
    joints[0, 0, 0] = (1.0, 1.0)                             # nose present
    joints[0, 0, 1] = (2.0, 3.0)                             # L_eye present
    # BONE_PAIRS[0] is (0, 1) — both ends present, so it is the difference between them.
    # BONE_PAIRS[3] is (1, 3) — L_ear is absent, so that bone must be zero, not (-2, -3).
    bones = create_bones(joints)
    assert np.array_equal(bones[0, 0, 0], [1.0, 2.0])
    assert np.array_equal(bones[0, 0, 3], [0.0, 0.0])


def test_a_short_window_is_padded_and_says_how_much_of_it_is_real():
    features = random_features(30)
    jnb, positions, shuttle, video_len = build_window(features, 0, 30)
    assert jnb.shape == (SEQ_LEN, 2, IN_DIM)
    assert positions.shape == (SEQ_LEN, 2, 2) and shuttle.shape == (SEQ_LEN, 2)
    assert jnb.dtype == np.float32
    assert video_len == 30
    assert np.array_equal(jnb[30:], np.zeros_like(jnb[30:]))  # the padding is zeros


def test_a_long_window_is_strided_down_rather_than_cropped():
    # 250 frames into 100: the whole stroke has to stay in view, so it is subsampled.
    n = 250
    joints = np.zeros((n, 2, NUM_KEYPOINTS, 2), np.float32)
    positions = np.zeros((n, 2, 2), np.float32)
    shuttle = np.arange(n * 2, dtype=np.float32).reshape(n, 2)
    _, _, strided, video_len = make_seq_len_same(SEQ_LEN, joints, positions, shuttle)
    assert video_len <= SEQ_LEN
    # The last real frame comes from near the end of the window, not from frame 100.
    assert strided[video_len - 1][0] > shuttle[SEQ_LEN][0]


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #


def test_predictions_do_not_depend_on_how_they_were_batched():
    model = build_bst_model().eval()
    features = random_features(60)
    windows = [(max(0, f - 15), min(60, f + 16)) for f in range(60)]

    one_batch = predict_windows(model, features, windows, batch_size=256)
    many_batches = predict_windows(model, features, windows, batch_size=7)

    assert one_batch.shape == (60, N_CLASSES)
    assert np.allclose(one_batch.sum(axis=1), 1.0, atol=1e-5)
    assert np.allclose(one_batch, many_batches, atol=1e-5)


def test_no_windows_is_an_empty_result_rather_than_a_crash():
    model = build_bst_model().eval()
    assert predict_windows(model, random_features(5), []).shape == (0, N_CLASSES)


# --------------------------------------------------------------------------- #
# Adapter: artifacts -> features
# --------------------------------------------------------------------------- #


def skeleton_at(x_m: float, y_m: float) -> tuple[list, list]:
    """A crude standing skeleton whose ankles are at the given court position."""
    foot_x, foot_y = image_of(x_m, y_m)
    keypoints = [[foot_x, foot_y - 170.0, 0.9] for _ in range(NUM_KEYPOINTS)]
    keypoints[L_ANKLE] = [foot_x - 10.0, foot_y, 0.9]
    keypoints[R_ANKLE] = [foot_x + 10.0, foot_y, 0.9]
    bbox = [foot_x - 30.0, foot_y - 180.0, foot_x + 30.0, foot_y]
    return keypoints, bbox


def write_match(tmp_path, *, pose_records, shuttle_records, segments):
    for stage, records, extra in (
        ("match_segmentation", segments, {"fps": FPS}),
        ("court_detection", [{"corners": [[0, 0]] * 4, "homography": COURT_TO_IMAGE,
                              "segment_index": None}], None),
        ("pose", pose_records, None),
        ("shuttle_tracking", shuttle_records, None),
    ):
        spec = PIPELINE[stage]
        write_artifact(spec, records, stage_path(tmp_path, stage) / spec.output_filename, extra)
    return tmp_path


@pytest.fixture
def match(tmp_path):
    """A two-frame rally: top player on the far baseline, bottom on the near one."""
    segments = [{"start_frame": 100, "end_frame": 101,
                 "start_sec": 0.0, "end_sec": 0.1, "duration_sec": 0.1}]
    pose_records = []
    for frame in (100, 101):
        for player, y_m in (("top", 0.0), ("bottom", COURT_LENGTH_M)):
            keypoints, bbox = skeleton_at(COURT_WIDTH_M / 2, y_m)
            pose_records.append({"frame": frame, "segment_index": 0, "player": player,
                                 "keypoints": keypoints, "bbox": bbox})
    shuttle_records = [
        {"frame": 100, "segment_index": 0, "method": "inpaint",
         "x": 960.0, "y": 540.0, "visible": True, "confidence": 0.9},
        {"frame": 101, "segment_index": 0, "method": "inpaint",
         "x": None, "y": None, "visible": False, "confidence": 0.0},
        # The other trajectory over the same frames, which must be ignored.
        {"frame": 100, "segment_index": 0, "method": "viterbi",
         "x": 10.0, "y": 10.0, "visible": True, "confidence": 0.9},
    ]
    return write_match(tmp_path, pose_records=pose_records,
                       shuttle_records=shuttle_records, segments=segments)


def test_the_far_player_lands_at_the_far_end_of_the_court(match):
    """The one that fails silently: flip y and Top/Bottom swap in every prediction."""
    features = adapter.load_segment_features(match, video_size_px=VIDEO_SIZE)[0]

    assert len(features) == 2
    assert features.start_frame == 100
    # Slot 0 is "top" — the far player, at court y ~= 0. Slot 1 is "bottom", at y ~= 1.
    assert features.positions[0, 0] == pytest.approx([0.5, 0.0], abs=0.02)
    assert features.positions[0, 1] == pytest.approx([0.5, 1.0], abs=0.02)


def test_the_shuttle_is_a_fraction_of_the_frame_and_zero_when_it_is_not_visible(match):
    features = adapter.load_segment_features(match, video_size_px=VIDEO_SIZE)[0]
    assert features.shuttle[0] == pytest.approx([0.5, 0.5])   # (960, 540) of 1920x1080
    assert np.array_equal(features.shuttle[1], [0.0, 0.0])    # not visible


def test_the_requested_shuttle_method_is_the_one_that_is_read(match):
    """shuttle.json holds both trajectories over the same frames; BST gets exactly one."""
    inpaint = adapter.load_segment_features(match, video_size_px=VIDEO_SIZE)[0]
    viterbi = adapter.load_segment_features(
        match, shuttle_method="viterbi", video_size_px=VIDEO_SIZE
    )[0]

    assert inpaint.shuttle[0] == pytest.approx([960 / 1920, 540 / 1080])
    assert viterbi.shuttle[0] == pytest.approx([10 / 1920, 10 / 1080])


def test_asking_for_a_trajectory_that_was_never_tracked_is_an_error(tmp_path):
    """Silently reading zeros would be a rally where the shuttle never moved."""
    match = write_match(
        tmp_path,
        segments=[{"start_frame": 0, "end_frame": 0,
                   "start_sec": 0.0, "end_sec": 0.0, "duration_sec": 0.0}],
        pose_records=[],
        shuttle_records=[{"frame": 0, "segment_index": 0, "method": "inpaint",
                          "x": 1.0, "y": 1.0, "visible": True, "confidence": 0.5}],
    )
    with pytest.raises(RuntimeError, match="no 'viterbi' points"):
        adapter.load_segment_features(
            match, shuttle_method="viterbi", video_size_px=VIDEO_SIZE
        )
    with pytest.raises(ValueError, match="unknown shuttle method"):
        adapter.load_segment_features(match, shuttle_method="kalman", video_size_px=VIDEO_SIZE)


def test_a_player_who_was_not_found_is_zeros_rather_than_a_gap(tmp_path):
    segments = [{"start_frame": 0, "end_frame": 1,
                 "start_sec": 0.0, "end_sec": 0.1, "duration_sec": 0.1}]
    keypoints, bbox = skeleton_at(COURT_WIDTH_M / 2, 1.0)
    pose_records = [
        {"frame": 0, "segment_index": 0, "player": "top",
         "keypoints": keypoints, "bbox": bbox},
        # The bottom player is missing in frame 0, and both players in frame 1.
        {"frame": 0, "segment_index": 0, "player": "bottom",
         "keypoints": None, "bbox": None},
    ]
    match = write_match(tmp_path, pose_records=pose_records, shuttle_records=[
        {"frame": 0, "segment_index": 0, "method": "inpaint",
         "x": 1.0, "y": 1.0, "visible": True, "confidence": 0.5},
    ], segments=segments)

    features = adapter.load_segment_features(match, video_size_px=VIDEO_SIZE)[0]

    assert len(features) == 2                                  # the frame with no pose at all
    assert not np.array_equal(features.joints[0, 0], np.zeros((NUM_KEYPOINTS, 2)))
    assert np.array_equal(features.joints[0, 1], np.zeros((NUM_KEYPOINTS, 2)))
    assert np.array_equal(features.positions[0, 1], [0.0, 0.0])
    assert np.array_equal(features.joints[1], np.zeros((2, NUM_KEYPOINTS, 2)))


def test_the_fps_the_windows_are_sized_from_comes_from_segmentation(match):
    assert adapter.read_fps(match) == FPS
