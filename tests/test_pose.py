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
    build_static_anchors,
    candidate_margins,
    candidate_mask,
    court_from_image,
    court_size,
    ground_points,
    ground_scale,
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
    height: float = 600.0,
    width: float = 70.0,
    ankle_score: float = 0.9,
) -> dict:
    """One detection standing with their feet at the given image point.

    The default height is sized so a person on this head-on fixture clears
    ``SelectConfig.min_bootstrap_size``: the court here is orthographic (100 px/m, no
    foreshortening), so :func:`court_size` returns height-in-metres directly, and a shorter
    default would score below the bootstrap floor and never be acquired — a property of the
    flat fixture, not of the selection. Tests that need an under-sized body (a distant
    official, a decoy) pass an explicit smaller ``height``.
    """
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
# perspective, and the umpire on their chair
#
# Everything above uses the head-on camera, where a metre is a metre wherever you
# stand. A broadcast camera is not head-on, and that is the whole problem: it makes
# whoever is nearest the lens the biggest, and the nearest person is not a player but
# the umpire, on a raised chair beside the net, in every frame of the match.
#
# These tests use a real perspective homography, and the two bodies are sized from the
# test match: the far player measures 5.6 ground-metres, the umpire 3.9, while in
# pixels the umpire's box is the larger of the two. Ranking by pixel area picked the
# umpire in 86% of the frames the two shared.
# --------------------------------------------------------------------------- #


def homography_from_corners(court_pts, image_pts) -> np.ndarray:
    """Solve the court -> image homography from four correspondences."""
    rows = []
    for (x_m, y_m), (u, v) in zip(court_pts, image_pts):
        rows.append([x_m, y_m, 1, 0, 0, 0, -u * x_m, -u * y_m])
        rows.append([0, 0, 0, x_m, y_m, 1, -v * x_m, -v * y_m])
    h = np.linalg.solve(np.array(rows, float), np.array(image_pts, float).ravel())
    return np.append(h, 1.0).reshape(3, 3)


# A camera behind the near baseline and above it: the far baseline is a short edge high
# in the frame, the near baseline a long one low in it. One court metre is worth 27 px
# out at the far player and 47 px in at the net — the 1.7x that pixel area mistakes for
# a difference in size.
BROADCAST = homography_from_corners(
    [(0, 0), (COURT_WIDTH_M, 0), (COURT_WIDTH_M, COURT_LENGTH_M), (0, COURT_LENGTH_M)],
    [(760, 260), (1160, 260), (1420, 900), (500, 900)],
)

#: Apparent height, in court metres, of a body standing on the ground (the players) and
#: of one sitting 1.5 m up on a chair (the umpire). Medians measured over the test match.
#: Neither is a physical height: the camera foreshortens the *ground* far more than it
#: foreshortens a standing person, so a real 1.8 m athlete measures 5.6 of these metres.
PLAYER_SIZE, UMPIRE_SIZE = 5.6, 3.9


def broadcast_to_court() -> np.ndarray:
    return court_from_image(BROADCAST)


def at_broadcast(x_m: float, y_m: float) -> tuple[float, float]:
    point = np.array([x_m, y_m, 1.0]) @ BROADCAST.T
    return tuple(point[:2] / point[2])


def metre_in_pixels(x_m: float, y_m: float) -> float:
    """How many pixels one court metre spans on the ground at that court point."""
    feet = np.array([at_broadcast(x_m, y_m)])
    return float(ground_scale(feet, broadcast_to_court())[0])


def person_at(x_m: float, y_m: float, size: float, width_ratio: float = 0.58) -> dict:
    """A body whose feet project to that court point and whose apparent height is ``size``.

    ``size`` is measured in court metres *at that point*, so a player standing anywhere
    on the court is built with the same one — which is exactly the invariant that makes
    them comparable, and the one pixels destroy.
    """
    height = size * metre_in_pixels(x_m, y_m)
    return make_person(
        *at_broadcast(x_m, y_m), height=height, width=height * width_ratio
    )


def a_player(x_m: float, y_m: float) -> dict:
    return person_at(x_m, y_m, PLAYER_SIZE)


def the_umpire() -> dict:
    """On their chair: outside the right sideline, level with the net, and never moving.

    The chair puts them 1.5 m off the ground, and the homography only maps the ground —
    so their feet back-project not to the chair but to the net line, which is the near
    part of the *far* player's half. That is how a seated official ends up competing for
    a player's slot at all.
    """
    return person_at(7.0, 6.5, UMPIRE_SIZE, width_ratio=0.64)


def test_the_umpire_lands_in_the_far_players_half_just_outside_the_sideline():
    # The fixture is only worth anything if it puts the umpire where the real one goes.
    feet = ground_points(make_det(the_umpire()), 0.3)
    court = to_court(feet, broadcast_to_court())[0]

    assert court == pytest.approx([1.148, 0.485], abs=0.01)   # measured: (1.153, 0.484)
    assert court[1] < 0.5                                     # ... which is the top half
    assert court[0] > 1.0                                     # ... and outside the sideline


def test_ground_scale_grows_towards_the_camera():
    far = metre_in_pixels(3.05, 0.5)          # a metre at the far baseline
    net = metre_in_pixels(3.05, 6.7)          # the same metre at the net
    near = metre_in_pixels(3.05, 12.9)        # and at the near baseline

    assert far < net < near
    assert near / far > 2.0                   # the foreshortening pixels get fooled by


def test_court_size_is_blind_to_how_far_away_the_person_is():
    """The property the whole fix rests on: the same athlete scores the same anywhere.

    Two players of identical build, one at each baseline. In pixels the near one is more
    than twice the far one; on the court they are the same person.
    """
    far, near = a_player(3.0, 1.0), a_player(3.0, 12.0)
    det = make_det(far, near)
    feet = ground_points(det, 0.3)

    pixels = det["bboxes"][:, 3] - det["bboxes"][:, 1]
    assert pixels[1] / pixels[0] > 2.0                        # wildly different in pixels

    size = court_size(det["bboxes"], feet, broadcast_to_court())
    assert size[0] == pytest.approx(size[1], rel=1e-6)        # identical on the court
    assert size[0] == pytest.approx(PLAYER_SIZE, rel=1e-6)


def test_pixel_area_is_what_used_to_pick_the_umpire():
    """The bug, pinned: by the old ranking the umpire is simply the better candidate.

    Without this the fix below could pass for the wrong reason — because the umpire was
    never a threat in the fixture, rather than because the ranking now sees through them.
    """
    player, umpire = a_player(3.0, 2.55), the_umpire()
    boxes = make_det(player, umpire)["bboxes"]
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])

    assert area[1] > area[0]                                  # the umpire wins on pixels
    assert area[1] / area[0] == pytest.approx(1.6, abs=0.1)


def test_prefers_the_player_over_the_bigger_umpire_on_their_chair():
    """The regression: the far player's slot goes to the far player, not the umpire.

    The umpire is inside the sideline margin (which is sized for a lunging player and
    cannot be tightened past them), is in the top half, and out-sizes the player in
    pixels. Only measuring them against the court tells the two apart.
    """
    umpire = the_umpire()
    player = a_player(3.0, 2.55)
    near = a_player(3.0, 10.0)

    top, bottom = select_players(make_det(umpire, player, near), broadcast_to_court())
    assert (top, bottom) == (1, 2)


def test_an_airborne_player_still_outranks_the_umpire():
    """A smash is where the far player is *most* likely to be lost, and must not be.

    Mid-jump their ankles are off the ground, so they back-project past the far baseline
    — further from the camera, where a court metre is worth fewer pixels, so they measure
    *larger*. The correction leans the right way on exactly the frames that matter.
    """
    grounded = a_player(3.0, 2.0)
    airborne = person_at(3.0, -1.5, PLAYER_SIZE)   # feet project past the far baseline
    umpire = the_umpire()

    det = make_det(umpire, airborne)
    size = court_size(det["bboxes"], ground_points(det, 0.3), broadcast_to_court())
    grounded_det = make_det(grounded)
    grounded_size = court_size(
        grounded_det["bboxes"], ground_points(grounded_det, 0.3), broadcast_to_court()
    )

    assert size[1] > grounded_size[0]             # the jump *raises* their score
    assert select_players(det, broadcast_to_court())[0] == 1


def test_tracker_does_not_spend_the_rally_following_the_umpire():
    """The failure this cost: one bad bootstrap and the tracker never comes back.

    The umpire does not move, so once they are mistaken for the player they sit at
    distance zero from their own prior forever — continuity, the very thing that makes
    the tracker good, then keeps it wrong for the whole rally. The bootstrap has to be
    right, so it ranks on the court like everything else.
    """
    umpire = the_umpire()
    near = a_player(3.0, 10.0)
    t = PlayerTracker(broadcast_to_court(), SelectConfig())

    for step in range(6):
        # The player works their way up the far court; the umpire never budges.
        player = a_player(3.0 + 0.2 * step, 2.55 + 0.3 * step)
        top, bottom = t.update(make_det(umpire, player, near))
        assert (top, bottom) == (1, 2), f"lost the player to the umpire on frame {step}"


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


# --------------------------------------------------------------------------- #
# static-distractor exclusion — the umpire's real tell is that it never moves,   #
# not its size (which overlaps, and can exceed, a deep player's).                #
# --------------------------------------------------------------------------- #


def _rally_frames(n: int = 24) -> list[dict]:
    """A rally: the umpire pinned on their chair, the far player sweeping up-court."""
    umpire = the_umpire()
    frames = []
    for step in range(n):
        # Both players move each frame — ~0.35 m up-court and across — so no single grid
        # cell of theirs is occupied for long, unlike the motionless umpire.
        player = a_player(2.0 + 0.12 * step, 1.5 + 0.35 * step)
        near = a_player(4.0 - 0.1 * step, 11.5 - 0.25 * step)
        frames.append(make_det(umpire, player, near))
    return frames


def _umpire_feet() -> np.ndarray:
    return ground_points(make_det(the_umpire()), 0.3)[0]


def test_build_static_anchors_finds_the_umpire_and_not_the_moving_player():
    anchors = build_static_anchors(_rally_frames(), SelectConfig())

    # Exactly one fixture — the umpire — and it sits on their (unmoving) feet.
    assert len(anchors) == 1
    assert np.linalg.norm(anchors[0] - _umpire_feet()) < SelectConfig().anchor_grid

    # The player never anchors: they move, so no cell of theirs clears the occupancy
    # and tightness tests. (A pinned player would be a bug in the test, not the code.)
    player_feet = ground_points(_rally_frames()[-1], 0.3)[1]
    assert np.linalg.norm(anchors[0] - player_feet) > 200


def test_anchor_refuses_the_umpire_even_when_it_is_the_only_candidate():
    """The lock's source: player briefly gone, umpire the only body left in the half.

    With the floor set below the umpire's size, only the anchor can keep it out — so this
    isolates the anchor's contribution from the bootstrap floor's.
    """
    anchors = build_static_anchors(_rally_frames(), SelectConfig())
    only_umpire = make_det(the_umpire(), a_player(3.0, 10.0))
    cfg = SelectConfig(min_bootstrap_size=0.0)   # floor cannot be what rejects the umpire

    without = PlayerTracker(broadcast_to_court(), cfg)
    assert without.update(only_umpire)[0] is not None      # bootstraps onto the umpire

    withanchor = PlayerTracker(broadcast_to_court(), cfg, anchors=anchors)
    assert withanchor.update(only_umpire)[0] is None        # anchor refuses it


def test_anchor_breaks_the_stationary_lock_and_re_acquires_the_player():
    """End to end: the player leaves, the umpire stays, the player returns.

    Without anchors the widening gate latches onto the motionless umpire and the rally
    never comes back. With anchors the top slot is honestly empty while the player is gone
    and holds the real player — never the umpire — whenever one is present.
    """
    anchors = build_static_anchors(_rally_frames(), SelectConfig())
    umpire_feet = _umpire_feet()
    # Floor set below the umpire's size so only the anchor — not the floor — can be what
    # keeps the umpire out of the slot.
    cfg = SelectConfig(min_bootstrap_size=0.0)

    def is_umpire(feet, idx):
        return idx is not None and np.linalg.norm(feet[idx] - umpire_feet) < 30.0

    t = PlayerTracker(broadcast_to_court(), cfg, anchors=anchors)
    present, umpire_picks = 0, 0
    for step in range(24):
        if 8 <= step < 16:
            det = make_det(the_umpire(), a_player(3.0, 10.0))     # player gone
        else:
            det = make_det(the_umpire(), a_player(2.0 + 0.1 * step, 2.0), a_player(3.0, 10.0))
        feet = ground_points(det, 0.3)
        top, _ = t.update(det)
        if is_umpire(feet, top):
            umpire_picks += 1
        if 8 <= step < 16:
            assert top is None, f"top should be empty while the player is gone (step {step})"
        else:
            present += 1
            assert top is not None, f"lost the present player (step {step})"

    assert umpire_picks == 0
    assert present > 0


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
