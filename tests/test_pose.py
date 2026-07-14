"""Unit tests for the pose stage.

Everything here runs on synthetic detections and a synthetic court — no video, no
models, no GPU. The two neural nets are covered only through the pure logic around
them, which is where this stage's own decisions actually live: mapping people onto the
court, choosing the two players (especially the airborne ones), and the cache and CSV
that carry the result.
"""

from __future__ import annotations

import csv

import numpy as np
import pytest

from modules.contracts import COCO_KEYPOINTS, POSE_PLAYERS
from modules.pose import csv_export, detection_cache
from modules.pose.module import _to_record
from modules.pose.select import (
    COURT_LENGTH_M,
    COURT_WIDTH_M,
    PlayerTracker,
    SelectConfig,
    candidate_margins,
    candidate_mask,
    court_from_image,
    ground_points,
    select_players,
    to_court,
)

L_ANKLE, R_ANKLE = 15, 16
NUM_KEYPOINTS = 17

# A head-on camera: the court maps to a plain rectangle in the image, no perspective.
# 100 px per metre, court origin (the far-left corner) at image (200, 100).
SCALE, ORIGIN_X, ORIGIN_Y = 100.0, 200.0, 100.0
COURT_TO_IMAGE = [
    [SCALE, 0.0, ORIGIN_X],
    [0.0, SCALE, ORIGIN_Y],
    [0.0, 0.0, 1.0],
]


def image_to_court() -> np.ndarray:
    return court_from_image(COURT_TO_IMAGE)


def at_court(x_m: float, y_m: float) -> tuple[float, float]:
    """The image pixel a point (x_m, y_m) of the court sits at, on the ground."""
    return ORIGIN_X + x_m * SCALE, ORIGIN_Y + y_m * SCALE


def make_person(
    foot_x: float,
    foot_y: float,
    height: float = 180.0,
    width: float = 70.0,
    ankle_score: float = 0.9,
) -> dict:
    """One detection standing with their feet at the given image point."""
    kps = np.zeros((NUM_KEYPOINTS, 2), np.float32)
    scores = np.full(NUM_KEYPOINTS, 0.9, np.float32)
    kps[:] = (foot_x, foot_y - height / 2)          # the rest of the body, roughly
    kps[L_ANKLE] = (foot_x - 5, foot_y)
    kps[R_ANKLE] = (foot_x + 5, foot_y)
    scores[[L_ANKLE, R_ANKLE]] = ankle_score
    bbox = np.array(
        [foot_x - width / 2, foot_y - height, foot_x + width / 2, foot_y], np.float32
    )
    return {"kps": kps, "scores": scores, "bboxes": bbox}


def make_det(*people: dict) -> dict:
    """Bundle people into one frame's detection dict."""
    if not people:
        return {
            "kps": np.zeros((0, NUM_KEYPOINTS, 2), np.float32),
            "scores": np.zeros((0, NUM_KEYPOINTS), np.float32),
            "bboxes": np.zeros((0, 4), np.float32),
        }
    return {
        "kps": np.stack([p["kps"] for p in people]),
        "scores": np.stack([p["scores"] for p in people]),
        "bboxes": np.stack([p["bboxes"] for p in people]),
    }


# --------------------------------------------------------------------------- #
# court projection
# --------------------------------------------------------------------------- #


def test_court_from_image_inverts_the_stored_direction():
    # court.json stores court -> image; consumers need the other way round.
    point = np.array([[*at_court(3.05, 6.705)]])            # the centre of the court
    court = to_court(point, image_to_court())

    assert court[0] == pytest.approx([3.05 / COURT_WIDTH_M, 6.705 / COURT_LENGTH_M], abs=1e-6)


def test_court_from_image_rejects_a_singular_homography():
    with pytest.raises(ValueError):
        court_from_image([[1, 0, 0], [2, 0, 0], [0, 0, 1]])


def test_to_court_normalizes_the_corners_to_the_unit_square():
    corners = np.array([at_court(0, 0), at_court(COURT_WIDTH_M, COURT_LENGTH_M)])
    court = to_court(corners, image_to_court())

    assert court[0] == pytest.approx([0.0, 0.0], abs=1e-6)
    assert court[1] == pytest.approx([1.0, 1.0], abs=1e-6)


# --------------------------------------------------------------------------- #
# ground point
# --------------------------------------------------------------------------- #


def test_ground_point_is_the_ankle_midpoint():
    det = make_det(make_person(*at_court(3.0, 10.0)))
    assert ground_points(det, 0.3)[0] == pytest.approx(at_court(3.0, 10.0), abs=1e-3)


def test_ground_point_falls_back_to_the_bbox_when_the_ankles_are_unreliable():
    # Ankles occluded (score below the floor): their midpoint means nothing, so the
    # bottom-centre of the box is used instead. Here they coincide, so move the ankles
    # somewhere absurd to prove the fallback is what answered.
    person = make_person(*at_court(3.0, 10.0), ankle_score=0.05)
    person["kps"][[L_ANKLE, R_ANKLE]] = (9999.0, 9999.0)
    det = make_det(person)

    assert ground_points(det, 0.3)[0] == pytest.approx(at_court(3.0, 10.0), abs=1e-3)


def test_ground_point_uses_a_single_confident_ankle():
    person = make_person(*at_court(3.0, 10.0))
    person["scores"][R_ANKLE] = 0.05          # only the left ankle is trustworthy
    det = make_det(person)

    x, y = at_court(3.0, 10.0)
    assert ground_points(det, 0.3)[0] == pytest.approx((x - 5, y), abs=1e-3)


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #


def test_selects_one_player_per_half():
    far = make_person(*at_court(3.0, 3.0))
    near = make_person(*at_court(3.0, 10.0))
    top, bottom = select_players(make_det(far, near), image_to_court())

    assert (top, bottom) == (0, 1)


def test_half_assignment_does_not_depend_on_detection_order():
    near = make_person(*at_court(3.0, 10.0))
    far = make_person(*at_court(3.0, 3.0))
    top, bottom = select_players(make_det(near, far), image_to_court())

    assert (top, bottom) == (1, 0)  # the far player is top however the detector ordered them


def test_keeps_a_player_whose_feet_project_past_the_baseline():
    """The case the y-margin exists for: a smash jump.

    A far player leaves the ground, so their raised ankles back-project through the
    ground plane to a point *beyond* the far baseline (negative court y). A strict
    in-court test would drop them exactly on the frames that matter.
    """
    jumper_x, baseline_y = at_court(3.0, 0.0)
    airborne = make_person(jumper_x, baseline_y - 1.6 * SCALE)   # feet 1.6 m up-court of it
    near = make_person(*at_court(3.0, 10.0))

    court_y = to_court(ground_points(make_det(airborne), 0.3), image_to_court())[0, 1]
    assert court_y < 0, "the jumper must land outside the court for this test to mean anything"

    top, bottom = select_players(make_det(airborne, near), image_to_court())
    assert (top, bottom) == (0, 1)


def test_drops_a_jumper_beyond_the_configured_margin():
    # The margin is a band, not an open door: far enough out and the person is not a player.
    jumper_x, baseline_y = at_court(3.0, 0.0)
    way_out = make_person(jumper_x, baseline_y - 8.0 * SCALE)
    near = make_person(*at_court(3.0, 10.0))

    top, bottom = select_players(make_det(way_out, near), image_to_court())
    assert (top, bottom) == (None, 1)


def test_keeps_a_player_lunging_outside_the_sideline():
    """The case the x-margin exists for, and the one that actually loses players.

    Chasing a wide shot puts a player well clear of the sideline — measured up to 1.74 m
    out on real footage. Every miss on the test match was this, not a jump.
    """
    lunging = make_person(*at_court(-1.5, 3.0))          # 1.5 m outside the left sideline
    near = make_person(*at_court(3.0, 10.0))

    top, bottom = select_players(make_det(lunging, near), image_to_court())
    assert (top, bottom) == (0, 1)


def test_ignores_people_well_off_the_side_of_the_court():
    # The margin is a band, not an open door: the crowd is metres beyond any lunge.
    spectator = make_person(*at_court(-4.0, 3.0))
    far = make_person(*at_court(3.0, 3.0))
    near = make_person(*at_court(3.0, 10.0))

    top, bottom = select_players(make_det(spectator, far, near), image_to_court())
    assert (top, bottom) == (1, 2)


def test_prefers_the_larger_person_when_two_share_a_half():
    # A seated official inside the widened court loses to a standing player.
    official = make_person(*at_court(0.3, 1.0), height=60, width=50)
    player = make_person(*at_court(3.0, 3.0))
    near = make_person(*at_court(3.0, 10.0))

    top, _ = select_players(make_det(official, player, near), image_to_court())
    assert top == 1


def test_missing_half_yields_none_for_that_player_only():
    near = make_person(*at_court(3.0, 10.0))
    top, bottom = select_players(make_det(near), image_to_court())

    assert (top, bottom) == (None, 0)  # one player found is better than the frame discarded


def test_empty_frame_selects_nobody():
    assert select_players(make_det(), image_to_court()) == (None, None)


def test_margins_are_configurable():
    jumper_x, baseline_y = at_court(3.0, 0.0)
    airborne = make_person(jumper_x, baseline_y - 1.6 * SCALE)
    det = make_det(airborne)

    assert select_players(det, image_to_court(), SelectConfig(y_margin=0.25))[0] == 0
    assert select_players(det, image_to_court(), SelectConfig(y_margin=0.05))[0] is None


# --------------------------------------------------------------------------- #
# PlayerTracker: selection with a memory
# --------------------------------------------------------------------------- #


def tracker(**overrides) -> PlayerTracker:
    return PlayerTracker(image_to_court(), SelectConfig(**overrides))


def test_tracker_bootstraps_from_size_then_follows_position():
    # Frame 1: no prior, so the bigger person in the half wins (that is select_players).
    # Frame 2: the player has moved slightly and a *bigger* impostor has appeared next to
    # them. Size would switch; continuity must not.
    player_a = make_person(*at_court(3.0, 3.0))
    near = make_person(*at_court(3.0, 10.0))
    t = tracker()
    assert t.update(make_det(player_a, near)) == (0, 1)

    moved = make_person(at_court(3.0, 3.0)[0] + 10, at_court(3.0, 3.0)[1])   # 10 px away
    impostor = make_person(*at_court(4.2, 3.0), height=260, width=110)       # bigger, further
    assert t.update(make_det(impostor, moved, near)) == (1, 2)


def test_tracker_reports_a_miss_rather_than_snapping_to_a_distant_impostor():
    """The rule that makes the wide sideline margin safe.

    When the player is simply not detected, the nearest remaining body inside the widened
    court is a line judge. Reaching for them would produce a confidently wrong skeleton,
    which nothing downstream can distinguish from a real one — so the answer is None.
    """
    player = make_person(*at_court(3.0, 3.0))
    near = make_person(*at_court(3.0, 10.0))
    t = tracker()
    assert t.update(make_det(player, near)) == (0, 1)

    judge = make_person(*at_court(-1.4, 3.0))          # inside the band, far from the player
    top, bottom = t.update(make_det(judge, near))
    assert top is None


def test_tracker_gate_widens_with_the_age_of_the_prior():
    # A player missing for several frames has had several frames in which to move, so the
    # same displacement that is rejected after one frame is accepted after four.
    start_x, start_y = at_court(3.0, 3.0)
    near = make_person(*at_court(3.0, 10.0))
    far_step = make_person(start_x + 300, start_y)     # 300 px: over a 1-frame gate of 120

    t = tracker()
    t.update(make_det(make_person(start_x, start_y), near))
    assert t.update(make_det(far_step, near))[0] is None          # age 1: too far

    t = tracker()
    t.update(make_det(make_person(start_x, start_y), near))
    for _ in range(2):
        t.update(make_det(near))                                  # player missing entirely
    assert t.update(make_det(far_step, near))[0] == 0             # age 3: 360 px allowed


def test_tracker_drops_a_stale_prior_and_re_acquires():
    # After prior_max_age frames the memory is worthless; the next frame must bootstrap
    # again rather than gate forever against where the player stood seconds ago.
    start_x, start_y = at_court(3.0, 3.0)
    near = make_person(*at_court(3.0, 10.0))
    t = tracker(prior_max_age=2)
    t.update(make_det(make_person(start_x, start_y), near))

    for _ in range(3):
        t.update(make_det(near))                                  # player gone long enough

    elsewhere = make_person(*at_court(0.5, 1.0))                  # nowhere near the prior
    assert t.update(make_det(elsewhere, near))[0] == 0            # re-acquired by size


def test_tracker_reset_forgets_the_previous_rally():
    start_x, start_y = at_court(3.0, 3.0)
    near = make_person(*at_court(3.0, 10.0))
    t = tracker()
    t.update(make_det(make_person(start_x, start_y), near))

    t.reset()
    # A new rally starts with the player somewhere else entirely; without the reset the
    # gate would reject them.
    elsewhere = make_person(*at_court(0.5, 1.0))
    assert t.update(make_det(elsewhere, near))[0] == 0


def test_tracker_handles_an_empty_frame():
    t = tracker()
    assert t.update(make_det()) == (None, None)


# --------------------------------------------------------------------------- #
# candidate pre-filter (runs before pose, so a mistake here is unrecoverable)
# --------------------------------------------------------------------------- #


def test_candidate_filter_keeps_the_players_and_drops_the_crowd():
    on_court = make_person(*at_court(3.0, 10.0))
    umpire = make_person(*at_court(1.49 * COURT_WIDTH_M, -0.35 * COURT_LENGTH_M))
    spectator = make_person(*at_court(1.90 * COURT_WIDTH_M, -0.37 * COURT_LENGTH_M))
    det = make_det(on_court, umpire, spectator)

    mask = candidate_mask(det["bboxes"], image_to_court())
    assert list(mask) == [True, False, False]


def test_candidate_filter_keeps_an_airborne_player():
    # Same jump as the selection test: the pre-filter must not throw away someone the
    # selection would have accepted, because there would be no skeleton left to select.
    jumper_x, baseline_y = at_court(3.0, 0.0)
    airborne = make_det(make_person(jumper_x, baseline_y - 1.6 * SCALE))

    assert bool(candidate_mask(airborne["bboxes"], image_to_court())[0]) is True


def test_candidate_band_always_contains_the_selection_band():
    # The invariant the whole two-stage filter rests on. A selection margin wider than
    # the cached candidate band would search where nobody was posed.
    wide = SelectConfig(x_margin=0.9, y_margin=0.8)
    assert candidate_margins(wide) == (0.9, 0.8)


def test_candidate_band_is_strictly_looser_than_the_default_selection():
    # Strictly, not merely equal: the pre-filter judges from the bbox and the selection
    # from the ankles, so they disagree slightly, and the gap is what absorbs that. Bands
    # that merely touch would silently drop borderline players -- exactly the lunging
    # ones the wide sideline margin exists to keep.
    default = SelectConfig()
    x, y = candidate_margins(default)
    assert x > default.x_margin and y > default.y_margin


def test_candidate_filter_on_an_empty_frame():
    assert len(candidate_mask(make_det()["bboxes"], image_to_court())) == 0


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


def test_record_uses_absolute_frame_numbers():
    det = make_det(make_person(*at_court(3.0, 10.0)))
    record = _to_record(det, 0, frame=6433, segment_index=7, player="bottom")

    assert (record.frame, record.segment_index, record.player) == (6433, 7, "bottom")
    assert len(record.keypoints) == len(COCO_KEYPOINTS)
    assert len(record.bbox) == 4


def test_record_for_a_missing_player_carries_no_geometry():
    record = _to_record(make_det(), None, frame=10, segment_index=0, player="top")

    # None, not zeros -- (0, 0) is a real pixel, and a consumer must be able to tell.
    assert record.keypoints is None and record.bbox is None


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def make_segments() -> list[dict]:
    return [{"start_frame": 0, "end_frame": 9}, {"start_frame": 100, "end_frame": 149}]


def test_cache_round_trips_a_ragged_segment(tmp_path):
    # The point of the counts index: frames hold different numbers of people, including none.
    frames = [
        make_det(make_person(300.0, 500.0), make_person(400.0, 900.0)),
        make_det(),
        make_det(make_person(310.0, 505.0)),
    ]
    path = tmp_path / "seg0000.npz"
    detection_cache.save_segment(path, frames)
    loaded = detection_cache.load_segment(path)

    assert [len(f["bboxes"]) for f in loaded] == [2, 0, 1]
    assert loaded[0]["kps"].shape == (2, NUM_KEYPOINTS, 2)
    assert loaded[2]["bboxes"][0] == pytest.approx(frames[2]["bboxes"][0])


def base_meta(**overrides) -> dict:
    kwargs = dict(
        pose_mode="balanced",
        person_min_area=0.0,
        candidate_margins=candidate_margins(SelectConfig()),
        video="m.mp4",
        segments=make_segments(),
    )
    return detection_cache.build_meta(**{**kwargs, **overrides})


def test_cache_is_kept_when_the_meta_matches(tmp_path):
    assert detection_cache.prepare(tmp_path, base_meta()) is False   # created empty
    detection_cache.save_segment(detection_cache.segment_file(tmp_path, 0), [make_det()])

    assert detection_cache.prepare(tmp_path, base_meta()) is True    # reused
    assert detection_cache.segment_file(tmp_path, 0).is_file()


def test_retuning_the_selection_within_the_cached_band_reuses_the_cache(tmp_path):
    # The payoff of the split: the usual retune costs no GPU at all, because everyone
    # inside the candidate band already has a skeleton.
    detection_cache.prepare(tmp_path, base_meta())
    detection_cache.save_segment(detection_cache.segment_file(tmp_path, 0), [make_det()])

    tuned = candidate_margins(SelectConfig(x_margin=0.2, y_margin=0.4))
    assert detection_cache.prepare(tmp_path, base_meta(candidate_margins=tuned)) is True


@pytest.mark.parametrize(
    "changed",
    [
        {"pose_mode": "performance"},          # different weights -> different skeletons
        {"person_min_area": 0.001},            # filters before pose -> different people
        {"video": "other.mp4"},
        {"segments": [{"start_frame": 0, "end_frame": 11}]},   # re-cut segments
        # Searching wider than what was posed: those people are not in the cache, so
        # reusing it would quietly search an empty region.
        {"candidate_margins": candidate_margins(SelectConfig(y_margin=0.8))},
    ],
)
def test_cache_is_wiped_when_an_input_changes(tmp_path, changed):
    detection_cache.prepare(tmp_path, base_meta())
    detection_cache.save_segment(detection_cache.segment_file(tmp_path, 0), [make_det()])

    assert detection_cache.prepare(tmp_path, base_meta(**changed)) is False
    assert not detection_cache.segment_file(tmp_path, 0).is_file()


def test_refresh_cache_wipes_a_matching_cache(tmp_path):
    detection_cache.prepare(tmp_path, base_meta())
    detection_cache.save_segment(detection_cache.segment_file(tmp_path, 0), [make_det()])

    assert detection_cache.prepare(tmp_path, base_meta(), force=True) is False
    assert not detection_cache.segment_file(tmp_path, 0).is_file()


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #


def test_csv_has_the_columns_bst_expects(tmp_path):
    det = make_det(make_person(*at_court(3.0, 10.0)))
    records = [
        _to_record(det, None, frame=100, segment_index=0, player="top").__dict__,
        _to_record(det, 0, frame=100, segment_index=0, player="bottom").__dict__,
    ]
    path = tmp_path / "seg.csv"
    csv_export.write_segment_csv(path, records, start_frame=100)

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    header, top, bottom = rows
    assert header[:7] == ["frame", "player", "det_idx",
                          "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"]
    assert header[7:10] == ["nose_x", "nose_y", "nose_s"]
    assert len(header) == 7 + 3 * len(COCO_KEYPOINTS)

    # Frames are local to the segment, so a clip cut from it lines up with row 0.
    assert [top[0], top[1]] == ["0", "top"]
    assert [bottom[0], bottom[1]] == ["0", "bottom"]
    assert all(cell == "" for cell in top[3:])     # the player that was not found
    assert bottom[3] != "" and len(bottom) == len(header)


def test_csv_export_writes_one_file_per_segment(tmp_path):
    det = make_det(make_person(*at_court(3.0, 10.0)))
    segments = make_segments()
    records = [
        _to_record(det, 0, frame=0, segment_index=0, player=p).__dict__ for p in POSE_PLAYERS
    ] + [
        _to_record(det, 0, frame=100, segment_index=1, player=p).__dict__ for p in POSE_PLAYERS
    ]

    paths = csv_export.export(tmp_path, records, segments, stem="M")
    assert [p.name for p in paths] == ["M_seg0000_skeleton.csv", "M_seg0001_skeleton.csv"]
