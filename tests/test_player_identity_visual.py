"""Offline HSV fallback tests; no model, video, or external repository required."""
from __future__ import annotations

import json

import numpy as np

from modules.contracts import PlayerIdentityEpoch
from modules.player_identity.policy import IdentityResult, MIN_VOTES
from modules.player_identity.visual import (
    EpochAppearance,
    HsvFallbackConfig,
    RegionAppearance,
    SegmentOrientationEvidence,
    VisualRun,
    HsvIdentityFallback,
    aggregate_histograms,
    discriminative_region_weights,
    histogram_similarity,
    pose_region_histograms,
    resolve_hsv_profiles,
    split_orientation_evidence,
    hsv_histogram,
)


def hist(index: int, second: tuple[int, float] | None = None) -> np.ndarray:
    value = np.zeros(1024, dtype=np.float32)
    value[index] = 1.0
    if second:
        value[index] -= second[1]
        value[second[0]] = second[1]
    return value


def unresolved(index: int, first: int, last: int, votes: int = 1) -> dict:
    return {
        "epoch_index": index,
        "game_index": index,
        "first_segment": first,
        "last_segment": last,
        "votes": votes,
        "top_is_a": votes,
        "top_is_b": 0,
    }


def profile(index: int, top: np.ndarray, bottom: np.ndarray) -> EpochAppearance:
    return EpochAppearance(
        index,
        tuple(range(8)),
        RegionAppearance(top, top),
        RegionAppearance(bottom, bottom),
    )


def test_visual_default_stays_stable_and_detects_a_side_swap():
    red, blue = hist(1), hist(900)
    primary = IdentityResult([], None, [unresolved(0, 0, 10), unresolved(1, 11, 22)])
    outcome = resolve_hsv_profiles(
        primary,
        {0: profile(0, red, blue), 1: profile(1, blue, red)},
    )
    assert [(row.top, row.bottom) for row in outcome.result.epochs] == [
        ("a", "b"),
        ("b", "a"),
    ]
    assert all(row.resolved_by == "hsv_default" for row in outcome.result.epochs)
    assert outcome.metadata["scoreboard_binding"] is False
    assert outcome.result.unresolved == []


def test_resolved_epoch_anchors_a_b_and_is_never_changed():
    red, blue = hist(1), hist(900)
    authoritative = PlayerIdentityEpoch(0, 0, 0, 10, "b", "a", 8, 1.0, "vote")
    primary = IdentityResult([authoritative], None, [unresolved(1, 11, 22)])
    outcome = resolve_hsv_profiles(
        primary,
        {0: profile(0, red, blue), 1: profile(1, blue, red)},
    )
    assert outcome.result.epochs[0] is authoritative
    assert outcome.result.epochs[1].top == "a"
    assert outcome.result.epochs[1].resolved_by == "hsv_fallback"
    assert outcome.metadata["anchor_source"] == "serve_vote"
    assert outcome.metadata["scoreboard_binding"] is True


def test_equal_appearance_abstains_instead_of_forcing_an_identity():
    same = hist(3)
    primary = IdentityResult([], None, [unresolved(0, 0, 10)])
    outcome = resolve_hsv_profiles(primary, {0: profile(0, same, same)})
    assert outcome.result.epochs == []
    assert outcome.result.unresolved[0]["hsv_fallback"]["reason"] == "players_too_similar"


def test_missing_epoch_profile_remains_explicitly_unresolved():
    primary = IdentityResult([], None, [unresolved(0, 0, 10)])
    outcome = resolve_hsv_profiles(primary, {})
    assert outcome.result.unresolved[0]["hsv_fallback"]["reason"] == "insufficient_visual_observations"


def test_multi_frame_aggregation_resists_one_outlier():
    red, blue = hist(1), hist(900)
    aggregate = aggregate_histograms([red, red, red, red, blue])
    assert aggregate[1] == 0.8
    assert aggregate[900] == 0.2


def skeleton(confidence=1.0):
    points = [[0.0, 0.0, 0.0] for _ in range(17)]
    for index, xy in {
        5: (30, 20), 6: (70, 20), 11: (35, 55),
        12: (65, 55), 13: (38, 90), 14: (62, 90),
    }.items():
        points[index] = [*xy, confidence]
    return points


def test_pose_guided_regions_exclude_background_and_keep_shirt_and_shorts_separate():
    config = HsvFallbackConfig(min_region_pixels=20)
    frame = np.full((110, 100, 3), [0, 255, 0], dtype=np.uint8)
    frame[18:56, 25:76] = [0, 0, 255]
    frame[55:80, 30:71] = [255, 0, 0]
    regions = pose_region_histograms(frame, skeleton(), config)
    assert regions is not None
    red = hsv_histogram(np.full((20, 20, 3), [0, 0, 255], dtype=np.uint8))
    blue = hsv_histogram(np.full((20, 20, 3), [255, 0, 0], dtype=np.uint8))
    assert histogram_similarity(regions.shirt, red) > 0.95
    assert histogram_similarity(regions.shorts, blue) > 0.95
    assert pose_region_histograms(frame, skeleton(0.2), config) is None


def test_region_weight_follows_the_region_that_separates_players():
    dark, red, blue = hist(0), hist(1), hist(900)
    weights = discriminative_region_weights(
        RegionAppearance(dark, red), RegionAppearance(dark, blue)
    )
    assert weights.shirt == 0.0
    assert weights.shorts == 1.0


def test_visual_epoch_contract_round_trips_with_compact_provenance():
    primary = IdentityResult([], None, [unresolved(0, 0, 22)])
    outcome = resolve_hsv_profiles(primary, {0: profile(0, hist(1), hist(900))})
    row = outcome.result.epochs[0]
    payload = json.loads(json.dumps(row.__dict__))
    restored = PlayerIdentityEpoch(**payload)
    assert restored == row
    diagnostic = outcome.metadata["epochs"][0]
    assert diagnostic["resolution_source"] == "hsv_fallback_v1"
    assert diagnostic["sample_count"] == 8
    assert "histogram" not in json.dumps(outcome.metadata)


def test_default_assignment_is_deterministic_for_identical_inputs():
    primary = IdentityResult([], None, [unresolved(0, 0, 22)])
    profiles = {0: profile(0, hist(12), hist(24))}
    first = resolve_hsv_profiles(primary, profiles)
    second = resolve_hsv_profiles(primary, profiles)
    assert first.result == second.result
    assert first.metadata == second.metadata


def orientation(segment, top, resolved=True):
    return SegmentOrientationEvidence(
        segment, top, resolved,
        (1.0, 0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0, 1.0),
        0.9 if resolved else 0.6,
        0.5,
        8,
    )


def test_no_internal_flip_produces_one_visual_run():
    split = split_orientation_evidence(0, 4, [orientation(i, "a") for i in range(5)])
    assert split.runs == [VisualRun(0, 4, "a", "b")]
    assert split.transitions == []


def test_two_resolved_new_observations_confirm_a_switch_with_abstain_between():
    evidence = [
        orientation(0, "a"), orientation(1, "a"), orientation(2, "b"),
        orientation(3, "b", False), orientation(4, "b"),
    ]
    split = split_orientation_evidence(0, 4, evidence)
    assert split.runs == [VisualRun(0, 1, "a", "b"), VisualRun(2, 4, "b", "a")]
    assert split.transitions[0]["first_segment"] == 2
    assert split.transitions[0]["confirmation_segment"] == 4


def test_three_stable_orientations_produce_three_nonoverlapping_runs():
    evidence = [
        orientation(0, "a"), orientation(1, "a"),
        orientation(2, "b"), orientation(3, "b"),
        orientation(4, "a"), orientation(5, "a"),
    ]
    split = split_orientation_evidence(0, 5, evidence)
    assert split.runs == [
        VisualRun(0, 1, "a", "b"),
        VisualRun(2, 3, "b", "a"),
        VisualRun(4, 5, "a", "b"),
    ]
    assert all(a.last_segment < b.first_segment for a, b in zip(split.runs, split.runs[1:]))


def test_one_contradictory_resolved_segment_does_not_split():
    evidence = [
        orientation(0, "a"), orientation(1, "a"), orientation(2, "b"),
        orientation(3, "a"), orientation(4, "a"),
    ]
    split = split_orientation_evidence(0, 4, evidence)
    assert split.runs == [VisualRun(0, 4, "a", "b")]
    assert split.transitions == []


def test_abstain_preserves_continuity_but_cannot_independently_switch():
    same = split_orientation_evidence(
        0, 2, [orientation(0, "a"), orientation(1, "a", False), orientation(2, "a")]
    )
    contrary = split_orientation_evidence(
        0, 2, [orientation(0, "a"), orientation(1, "b", False), orientation(2, "a")]
    )
    assert same.runs == contrary.runs == [VisualRun(0, 2, "a", "b")]
    assert contrary.transitions == []


def test_completely_missing_segment_is_not_silently_assigned():
    split = split_orientation_evidence(
        0, 3, [orientation(0, "a"), orientation(1, "a"), orientation(3, "a")]
    )
    assert split.runs == [VisualRun(0, 1, "a", "b"), VisualRun(3, 3, "a", "b")]
    assert split.unresolved_segments == [2]


def test_leading_opposite_abstain_is_not_backfilled_from_later_resolution():
    split = split_orientation_evidence(
        0, 2, [orientation(0, "a", False), orientation(1, "b"), orientation(2, "b")]
    )
    assert split.runs == [VisualRun(1, 2, "b", "a")]
    assert split.unresolved_segments == [0]


def test_production_fallback_splits_one_primary_hole_from_segment_profiles(monkeypatch):
    primary = IdentityResult([], None, [unresolved(0, 0, 5)])
    red, blue = hist(1), hist(900)
    profiles = {
        0: profile(0, red, blue), 1: profile(1, red, blue),
        2: profile(2, blue, red), 3: profile(3, blue, red),
        4: profile(4, red, blue), 5: profile(5, red, blue),
    }

    def extracted(_match, bounds, _config):
        assert all(first == last for first, last in bounds.values())
        return profiles

    monkeypatch.setattr("modules.player_identity.visual._extract_profiles_for_bounds", extracted)
    outcome = HsvIdentityFallback().resolve("unused", primary)
    assert [(row.first_segment, row.last_segment, row.top) for row in outcome.result.epochs] == [
        (0, 1, "a"), (2, 3, "b"), (4, 5, "a"),
    ]
    assert len({row.epoch_index for row in outcome.result.epochs}) == 3
    assert outcome.metadata["detected_transitions"] == 2
    assert outcome.metadata["visual_epoch_count"] == 3

    # The unchanged consumer contract is an inclusive epoch range lookup. Every
    # covered segment resolves exactly once across the visual boundaries.
    for segment, expected_top in enumerate(("a", "a", "b", "b", "a", "a")):
        matches = [
            row for row in outcome.result.epochs
            if row.first_segment <= segment <= row.last_segment
        ]
        assert len(matches) == 1
        assert matches[0].top == expected_top


def test_primary_vote_threshold_remains_five():
    assert MIN_VOTES == 5


def test_production_fallback_reports_a_visual_evidence_gap_as_unresolved(monkeypatch):
    primary = IdentityResult([], None, [unresolved(0, 0, 4)])
    red, blue = hist(1), hist(900)
    profiles = {
        segment: profile(segment, red, blue) for segment in (0, 1, 3, 4)
    }

    monkeypatch.setattr(
        "modules.player_identity.visual._extract_profiles_for_bounds",
        lambda _match, _bounds, _config: profiles,
    )
    outcome = HsvIdentityFallback().resolve("unused", primary)
    assert outcome.metadata["status"] == "partially_resolved"
    assert outcome.result.unresolved[0]["hsv_fallback"]["unresolved_segments"] == [2]
    assert [(row.first_segment, row.last_segment) for row in outcome.result.epochs] == [
        (0, 1), (3, 4),
    ]
