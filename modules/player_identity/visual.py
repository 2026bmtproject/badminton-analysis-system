"""HSV-only fallback for court-position identity epochs.

The fallback consumes existing pose keypoints/boxes and source frames. It builds
separate shirt and shorts colour profiles so court, skin, and similarly dark shirts
do not swamp the most discriminative clothing region.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from modules.artifacts import read_records
from modules.contracts import PIPELINE, PlayerIdentityEpoch, artifact_path, resolve_input_video
from modules.player_identity.policy import IdentityResult


class VisualFallbackUnavailable(RuntimeError):
    """The optional fallback could not read its production inputs."""


@dataclass(frozen=True)
class HsvFallbackConfig:
    max_frame_pairs: int = 24
    min_frame_pairs: int = 8
    min_keypoint_confidence: float = 0.5
    min_region_pixels: int = 80
    min_separation: float = 0.12
    min_assignment_score: float = 0.65
    assignment_margin: float = 0.10


@dataclass(frozen=True)
class RegionAppearance:
    shirt: np.ndarray
    shorts: np.ndarray


@dataclass(frozen=True)
class RegionWeights:
    shirt: float
    shorts: float


@dataclass(frozen=True)
class EpochAppearance:
    epoch_index: int
    sample_frames: tuple[int, ...]
    top: RegionAppearance
    bottom: RegionAppearance


@dataclass(frozen=True)
class HsvFallbackOutcome:
    result: IdentityResult
    metadata: dict


@dataclass(frozen=True)
class SegmentOrientationEvidence:
    segment_index: int
    preferred_top: str
    resolved: bool
    shirt_scores: tuple[float, float, float, float]
    shorts_scores: tuple[float, float, float, float]
    combined_scores: tuple[float, float, float, float]
    assignment_score: float
    assignment_margin: float
    sample_count: int


@dataclass(frozen=True)
class VisualRun:
    first_segment: int
    last_segment: int
    top: str
    bottom: str


@dataclass(frozen=True)
class OrientationSplit:
    runs: list[VisualRun]
    transitions: list[dict]
    unresolved_segments: list[int]


def hsv_histogram(image: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    if image.size == 0 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("expected a nonempty BGR crop")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist(
        [hsv], [0, 1, 2], mask, [16, 8, 8], [0, 180, 0, 256, 0, 256]
    ).flatten().astype(np.float32)
    total = float(hist.sum())
    if total <= 0:
        raise ValueError("HSV crop produced an empty histogram")
    return hist / total


def aggregate_histograms(values: list[np.ndarray]) -> np.ndarray:
    """Average several observations so one crop cannot define a player."""
    if not values:
        raise ValueError("cannot aggregate an empty HSV sample set")
    shape = values[0].shape
    if any(value.shape != shape or not np.isfinite(value).all() for value in values):
        raise ValueError("HSV samples must be finite and have one shape")
    result = np.mean(np.stack(values), axis=0)
    return np.asarray(result / result.sum(), dtype=np.float32)


def histogram_similarity(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != second.shape:
        raise ValueError("HSV histogram dimensions differ")
    return float(np.clip(np.sqrt(first * second).sum(), 0.0, 1.0))


def _point(keypoints: object, index: int, config: HsvFallbackConfig) -> np.ndarray | None:
    if not isinstance(keypoints, list) or index >= len(keypoints):
        return None
    try:
        point = np.asarray(keypoints[index], dtype=float)
    except (TypeError, ValueError):
        return None
    if point.shape[0] < 3 or not np.isfinite(point[:3]).all():
        return None
    if point[2] < config.min_keypoint_confidence:
        return None
    return point[:2]


def _region_histogram(
    frame: np.ndarray, polygon: np.ndarray, config: HsvFallbackConfig
) -> np.ndarray | None:
    height, width = frame.shape[:2]
    if polygon.shape != (4, 2) or not np.isfinite(polygon).all():
        return None
    polygon = np.rint(polygon).astype(np.int32)
    polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, polygon, 255)
    if int(cv2.countNonZero(mask)) < config.min_region_pixels:
        return None
    return hsv_histogram(frame, mask)


def pose_region_polygons(
    keypoints: object, config: HsvFallbackConfig
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the exact shirt and shorts polygons used by production HSV."""
    left_shoulder = _point(keypoints, 5, config)
    right_shoulder = _point(keypoints, 6, config)
    left_hip = _point(keypoints, 11, config)
    right_hip = _point(keypoints, 12, config)
    left_knee = _point(keypoints, 13, config)
    right_knee = _point(keypoints, 14, config)
    if any(point is None for point in (
        left_shoulder, right_shoulder, left_hip, right_hip, left_knee, right_knee
    )):
        return None
    # Narrow both quadrilaterals toward their centre lines. This excludes arms,
    # hands and most court background without inventing pixels outside the pose.
    shoulders = (left_shoulder + right_shoulder) / 2
    hips = (left_hip + right_hip) / 2
    shirt = np.stack([
        shoulders + 0.72 * (left_shoulder - shoulders),
        shoulders + 0.72 * (right_shoulder - shoulders),
        hips + 0.82 * (right_hip - hips),
        hips + 0.82 * (left_hip - hips),
    ])
    # Shorts occupy the upper part of hip-to-knee. Stopping at 62% avoids bare
    # lower legs while keeping the garment even when its hem sits low.
    left_hem = left_hip + 0.62 * (left_knee - left_hip)
    right_hem = right_hip + 0.62 * (right_knee - right_hip)
    shorts = np.stack([
        hips + 0.90 * (left_hip - hips),
        hips + 0.90 * (right_hip - hips),
        right_hem,
        left_hem,
    ])
    return shirt, shorts


def pose_region_histograms(
    frame: np.ndarray, keypoints: object, config: HsvFallbackConfig
) -> RegionAppearance | None:
    """Build masked shoulder-to-hip and hip-to-knee HSV profiles."""
    polygons = pose_region_polygons(keypoints, config)
    if polygons is None:
        return None
    shirt, shorts = polygons
    shirt_hist = _region_histogram(frame, shirt, config)
    shorts_hist = _region_histogram(frame, shorts, config)
    if shirt_hist is None or shorts_hist is None:
        return None
    return RegionAppearance(shirt_hist, shorts_hist)


def aggregate_regions(values: list[RegionAppearance]) -> RegionAppearance:
    if not values:
        raise ValueError("cannot aggregate an empty region sample set")
    return RegionAppearance(
        aggregate_histograms([value.shirt for value in values]),
        aggregate_histograms([value.shorts for value in values]),
    )


def discriminative_region_weights(
    player_a: RegionAppearance, player_b: RegionAppearance
) -> RegionWeights:
    shirt = max(0.0, 1.0 - histogram_similarity(player_a.shirt, player_b.shirt))
    shorts = max(0.0, 1.0 - histogram_similarity(player_a.shorts, player_b.shorts))
    total = shirt + shorts
    if total <= 0:
        return RegionWeights(0.5, 0.5)
    return RegionWeights(shirt / total, shorts / total)


def region_similarities(
    first: RegionAppearance, second: RegionAppearance, weights: RegionWeights
) -> tuple[float, float, float]:
    shirt = histogram_similarity(first.shirt, second.shirt)
    shorts = histogram_similarity(first.shorts, second.shorts)
    combined = weights.shirt * shirt + weights.shorts * shorts
    return shirt, shorts, combined


def _profile_separation(appearance: EpochAppearance) -> float:
    weights = discriminative_region_weights(appearance.top, appearance.bottom)
    return 1.0 - region_similarities(appearance.top, appearance.bottom, weights)[2]


def classify_segment_profile(
    segment_index: int,
    appearance: EpochAppearance,
    prototypes: dict[str, RegionAppearance],
    weights: RegionWeights,
    config: HsvFallbackConfig,
) -> SegmentOrientationEvidence:
    shirt_ta, shorts_ta, combined_ta = region_similarities(
        appearance.top, prototypes["a"], weights
    )
    shirt_tb, shorts_tb, combined_tb = region_similarities(
        appearance.top, prototypes["b"], weights
    )
    shirt_ba, shorts_ba, combined_ba = region_similarities(
        appearance.bottom, prototypes["a"], weights
    )
    shirt_bb, shorts_bb, combined_bb = region_similarities(
        appearance.bottom, prototypes["b"], weights
    )
    keep = (combined_ta + combined_bb) / 2.0
    swap = (combined_tb + combined_ba) / 2.0
    selected, alternative, top = (keep, swap, "a") if keep >= swap else (swap, keep, "b")
    return SegmentOrientationEvidence(
        segment_index=segment_index,
        preferred_top=top,
        resolved=(
            selected >= config.min_assignment_score
            and selected - alternative >= config.assignment_margin
        ),
        shirt_scores=(shirt_ta, shirt_tb, shirt_ba, shirt_bb),
        shorts_scores=(shorts_ta, shorts_tb, shorts_ba, shorts_bb),
        combined_scores=(combined_ta, combined_tb, combined_ba, combined_bb),
        assignment_score=selected,
        assignment_margin=selected - alternative,
        sample_count=len(appearance.sample_frames),
    )


def split_orientation_evidence(
    first_segment: int,
    last_segment: int,
    evidence: list[SegmentOrientationEvidence],
) -> OrientationSplit:
    """Confirm a switch with two resolved observations of the new orientation.

    Abstained observations may move the boundary earlier when they continuously
    prefer the ultimately confirmed orientation, but cannot confirm a switch.
    Missing observations break continuity and remain outside every emitted run.
    """
    by_segment = {row.segment_index: row for row in evidence}
    resolved = [row for row in evidence if row.resolved]
    if not resolved:
        return OrientationSplit([], [], list(range(first_segment, last_segment + 1)))

    first_resolved = min(resolved, key=lambda row: row.segment_index)
    current = first_resolved.preferred_top
    transitions: list[dict] = []
    pending_top: str | None = None
    pending_start: int | None = None
    pending_resolved: list[SegmentOrientationEvidence] = []
    for segment in range(first_resolved.segment_index + 1, last_segment + 1):
        row = by_segment.get(segment)
        if row is None:
            pending_top = None
            pending_start = None
            pending_resolved = []
            continue
        if row.preferred_top == current:
            pending_top = None
            pending_start = None
            pending_resolved = []
            continue
        if pending_top != row.preferred_top:
            pending_top = row.preferred_top
            pending_start = segment
            pending_resolved = []
        if row.resolved:
            pending_resolved.append(row)
        if len(pending_resolved) < 2:
            continue
        assert pending_start is not None and pending_top is not None
        transitions.append({
            "first_segment": pending_start,
            "confirmation_segment": segment,
            "from_top": current,
            "to_top": pending_top,
            "resolved_support": [item.segment_index for item in pending_resolved],
            "support_margins": [round(item.assignment_margin, 6) for item in pending_resolved],
        })
        current = pending_top
        pending_top = None
        pending_start = None
        pending_resolved = []

    initial_top = first_resolved.preferred_top
    # Before the first confident observation, only a contiguous run that already
    # prefers the same orientation is defensible.  An opposite abstention is not
    # silently relabelled merely because a later segment resolved.
    leading_start = first_resolved.segment_index
    for segment in range(first_resolved.segment_index - 1, first_segment - 1, -1):
        row = by_segment.get(segment)
        if row is None or row.preferred_top != initial_top:
            break
        leading_start = segment

    assigned: dict[int, str] = {}
    for segment in range(first_segment, last_segment + 1):
        if segment not in by_segment or segment < leading_start:
            continue
        top = initial_top
        for transition in transitions:
            if segment >= transition["first_segment"]:
                top = transition["to_top"]
        assigned[segment] = top

    runs: list[VisualRun] = []
    for segment in sorted(assigned):
        top = assigned[segment]
        if (
            runs
            and runs[-1].top == top
            and runs[-1].last_segment + 1 == segment
        ):
            previous = runs[-1]
            runs[-1] = VisualRun(
                previous.first_segment, segment, previous.top, previous.bottom
            )
        else:
            runs.append(VisualRun(segment, segment, top, "b" if top == "a" else "a"))
    missing = [
        segment for segment in range(first_segment, last_segment + 1)
        if segment not in assigned
    ]
    return OrientationSplit(runs, transitions, missing)


def _record(row: dict, top: str, source: str) -> PlayerIdentityEpoch:
    votes = int(row["votes"])
    supporting = int(row["top_is_a"] if top == "a" else row["top_is_b"])
    return PlayerIdentityEpoch(
        epoch_index=int(row["epoch_index"]),
        game_index=int(row["game_index"]),
        first_segment=int(row["first_segment"]),
        last_segment=int(row["last_segment"]),
        top=top,
        bottom="b" if top == "a" else "a",
        votes=votes,
        agreement=supporting / votes if votes else 0.0,
        resolved_by=source,
    )


def resolve_hsv_profiles(
    primary: IdentityResult,
    profiles: dict[int, EpochAppearance],
    config: HsvFallbackConfig | None = None,
) -> HsvFallbackOutcome:
    """Fill only primary-policy holes from complete epoch HSV profiles."""
    config = config or HsvFallbackConfig()
    unresolved = {int(row["epoch_index"]): dict(row) for row in primary.unresolved}
    if not unresolved:
        return HsvFallbackOutcome(primary, {"status": "not_needed", "epochs": []})

    primary_by_epoch = {row.epoch_index: row for row in primary.epochs}
    anchor = next((index for index in sorted(primary_by_epoch) if index in profiles), None)
    default_labels = anchor is None
    if anchor is not None:
        record, appearance = primary_by_epoch[anchor], profiles[anchor]
        prototypes = {record.top: appearance.top, record.bottom: appearance.bottom}
        anchor_source = "serve_vote"
    else:
        anchor = next(
            (
                index
                for index in sorted(unresolved)
                if index in profiles
                and _profile_separation(profiles[index]) >= config.min_separation
            ),
            None,
        )
        if anchor is None:
            remaining, diagnostics = [], []
            for index in sorted(unresolved):
                row = unresolved[index]
                reason = (
                    "insufficient_visual_observations"
                    if index not in profiles
                    else "players_too_similar"
                )
                row["hsv_fallback"] = {"status": "abstained", "reason": reason}
                remaining.append(row)
                diagnostics.append({"epoch_index": index, **row["hsv_fallback"]})
            return HsvFallbackOutcome(
                IdentityResult(list(primary.epochs), primary.convention, remaining),
                {
                    "status": "abstained",
                    "policy": "hsv_fallback_v1",
                    "anchor_epoch": None,
                    "epochs": diagnostics,
                },
            )
        appearance = profiles[anchor]
        prototypes = {"a": appearance.top, "b": appearance.bottom}
        anchor_source = "hsv_default"

    weights = discriminative_region_weights(prototypes["a"], prototypes["b"])

    resolved, remaining, diagnostics = [], [], []
    for index in sorted(unresolved):
        row = unresolved[index]
        appearance = profiles.get(index)
        if appearance is None:
            row["hsv_fallback"] = {
                "status": "abstained",
                "reason": "insufficient_visual_observations",
            }
            remaining.append(row)
            diagnostics.append({"epoch_index": index, **row["hsv_fallback"]})
            continue

        if default_labels and index == anchor:
            selected = 1.0
            alternative = region_similarities(appearance.top, appearance.bottom, weights)[2]
            top = "a"
        else:
            keep = (
                region_similarities(appearance.top, prototypes["a"], weights)[2]
                + region_similarities(appearance.bottom, prototypes["b"], weights)[2]
            ) / 2.0
            swap = (
                region_similarities(appearance.top, prototypes["b"], weights)[2]
                + region_similarities(appearance.bottom, prototypes["a"], weights)[2]
            ) / 2.0
            selected, alternative, top = (
                (keep, swap, "a") if keep >= swap else (swap, keep, "b")
            )
        margin = selected - alternative
        compact = {
            "selected_similarity": round(selected, 6),
            "alternative_similarity": round(alternative, 6),
            "margin": round(margin, 6),
            "sample_count": len(appearance.sample_frames),
            "region_weights": {
                "shirt": round(weights.shirt, 6),
                "shorts": round(weights.shorts, 6),
            },
        }
        if selected < config.min_assignment_score or margin < config.assignment_margin:
            row["hsv_fallback"] = {
                "status": "abstained",
                "reason": "ambiguous_assignment",
                **compact,
            }
            remaining.append(row)
            diagnostics.append({"epoch_index": index, **row["hsv_fallback"]})
            continue

        source = "hsv_default" if default_labels else "hsv_fallback"
        resolved.append(_record(row, top, source))
        diagnostics.append(
            {
                "epoch_index": index,
                "status": "resolved",
                "resolution_source": "hsv_fallback_v1",
                "resolved_by": source,
                "fallback_reason": "insufficient_serve_votes",
                **compact,
            }
        )

    records = sorted([*primary.epochs, *resolved], key=lambda row: row.epoch_index)
    return HsvFallbackOutcome(
        IdentityResult(records, primary.convention, remaining),
        {
            "status": "resolved" if resolved else "abstained",
            "policy": "hsv_fallback_v1",
            "anchor_epoch": anchor,
            "anchor_source": anchor_source,
            "scoreboard_binding": not default_labels,
            "region_weights": {
                "shirt": round(weights.shirt, 6),
                "shorts": round(weights.shorts, 6),
            },
            "epochs": diagnostics,
        },
    )


def _pose_quality(row: dict) -> float:
    keypoints = row.get("keypoints")
    if not isinstance(keypoints, list):
        return 0.0
    confidence = []
    for point in keypoints:
        if not isinstance(point, list) or len(point) < 3:
            continue
        try:
            value = float(point[2])
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            confidence.append(value)
    return float(np.mean(confidence)) if confidence else 0.0


def _select_samples(
    rows: list[dict], bounds: dict[int, tuple[int, int]], maximum: int
) -> dict[int, list[tuple[int, dict, dict]]]:
    paired: dict[int, dict[int, dict[str, dict]]] = {index: {} for index in bounds}
    for row in rows:
        try:
            segment = int(row["segment_index"])
            frame = int(row["frame"])
            player = row["player"]
        except (KeyError, TypeError, ValueError):
            continue
        epoch = next(
            (
                index
                for index, (first, last) in sorted(bounds.items())
                if first <= segment <= last
            ),
            None,
        )
        if (
            epoch is None
            or player not in ("top", "bottom")
            or row.get("bbox") is None
            or row.get("keypoints") is None
        ):
            continue
        paired[epoch].setdefault(frame, {})[player] = row

    selected = {}
    for epoch, by_frame in paired.items():
        candidates = []
        for frame, positions in sorted(by_frame.items()):
            if set(positions) != {"top", "bottom"}:
                continue
            quality = min(_pose_quality(positions["top"]), _pose_quality(positions["bottom"]))
            candidates.append(
                (frame, quality, positions["top"], positions["bottom"])
            )
        if len(candidates) > maximum:
            choices = []
            edges = np.linspace(0, len(candidates), maximum + 1, dtype=int)
            for start, end in zip(edges[:-1], edges[1:], strict=True):
                choices.append(max(candidates[start:end], key=lambda item: (item[1], -item[0])))
        else:
            choices = candidates
        selected[epoch] = [(frame, top, bottom) for frame, _, top, bottom in choices]
    return selected


def _extract_profiles_for_bounds(
    match_path: str | Path,
    bounds: dict[int, tuple[int, int]],
    config: HsvFallbackConfig,
) -> dict[int, EpochAppearance]:
    pose_rows = read_records(PIPELINE["pose"], artifact_path(match_path, "pose"))
    samples = _select_samples(pose_rows, bounds, config.max_frame_pairs)
    requested = sorted({frame for values in samples.values() for frame, _, _ in values})

    video = resolve_input_video(match_path)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise VisualFallbackUnavailable(f"cannot open input video for HSV identity: {video}")
    frames = {}
    try:
        for frame in requested:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, image = cap.read()
            if ok:
                frames[frame] = image
    finally:
        cap.release()

    observations: dict[tuple[int, str], list[tuple[int, RegionAppearance]]] = {}
    for epoch in sorted(samples):
        for frame, top_pose, bottom_pose in samples[epoch]:
            image = frames.get(frame)
            if image is None:
                continue
            top = pose_region_histograms(image, top_pose.get("keypoints"), config)
            bottom = pose_region_histograms(image, bottom_pose.get("keypoints"), config)
            if top is None or bottom is None:
                continue
            observations.setdefault((epoch, "top"), []).append((frame, top))
            observations.setdefault((epoch, "bottom"), []).append((frame, bottom))

    profiles = {}
    for epoch in sorted(bounds):
        top_by_frame = dict(observations.get((epoch, "top"), []))
        bottom_by_frame = dict(observations.get((epoch, "bottom"), []))
        common = sorted(set(top_by_frame) & set(bottom_by_frame))
        if len(common) < config.min_frame_pairs:
            continue
        profiles[epoch] = EpochAppearance(
            epoch,
            tuple(common),
            aggregate_regions([top_by_frame[frame] for frame in common]),
            aggregate_regions([bottom_by_frame[frame] for frame in common]),
        )
    return profiles


def extract_epoch_profiles(
    match_path: str | Path,
    primary: IdentityResult,
    config: HsvFallbackConfig | None = None,
) -> dict[int, EpochAppearance]:
    """Read bounded pose/video samples and build one top/bottom profile per epoch."""
    config = config or HsvFallbackConfig()
    rows = [
        *(
            {
                "epoch_index": record.epoch_index,
                "first_segment": record.first_segment,
                "last_segment": record.last_segment,
            }
            for record in primary.epochs
        ),
        *primary.unresolved,
    ]
    bounds = {
        int(row["epoch_index"]): (int(row["first_segment"]), int(row["last_segment"]))
        for row in rows
    }
    return _extract_profiles_for_bounds(match_path, bounds, config)


class HsvIdentityFallback:
    def __init__(self, config: HsvFallbackConfig | None = None) -> None:
        self.config = config or HsvFallbackConfig()

    def resolve(self, match_path: str | Path, primary: IdentityResult) -> HsvFallbackOutcome:
        try:
            unresolved_segments = {
                segment: (segment, segment)
                for row in primary.unresolved
                for segment in range(int(row["first_segment"]), int(row["last_segment"]) + 1)
            }
            profiles = _extract_profiles_for_bounds(
                match_path, unresolved_segments, self.config
            )
            anchor_profiles = _extract_profiles_for_bounds(
                match_path,
                {
                    row.epoch_index: (row.first_segment, row.last_segment)
                    for row in primary.epochs
                },
                self.config,
            ) if primary.epochs else {}
        except FileNotFoundError as exc:
            raise VisualFallbackUnavailable(str(exc)) from exc

        anchor_epoch = next(
            (index for index in sorted(anchor_profiles) if index in {
                row.epoch_index for row in primary.epochs
            }),
            None,
        )
        default_labels = anchor_epoch is None
        if anchor_epoch is not None:
            record = next(row for row in primary.epochs if row.epoch_index == anchor_epoch)
            appearance = anchor_profiles[anchor_epoch]
            prototypes = {record.top: appearance.top, record.bottom: appearance.bottom}
            anchor_source = "serve_vote"
            anchor_segment = None
        else:
            anchor_segment = next(
                (
                    segment for segment in sorted(profiles)
                    if _profile_separation(profiles[segment]) >= self.config.min_separation
                ),
                None,
            )
            if anchor_segment is None:
                rows = []
                for row in primary.unresolved:
                    item = dict(row)
                    item["hsv_fallback"] = {
                        "status": "abstained",
                        "reason": "no_separable_visual_anchor",
                    }
                    rows.append(item)
                return HsvFallbackOutcome(
                    IdentityResult(list(primary.epochs), primary.convention, rows),
                    {
                        "status": "abstained",
                        "policy": "hsv_fallback_v1",
                        "anchor_source": None,
                        "segment_observations": len(profiles),
                        "epochs": [],
                    },
                )
            appearance = profiles[anchor_segment]
            prototypes = {"a": appearance.top, "b": appearance.bottom}
            anchor_source = "hsv_default"

        weights = discriminative_region_weights(prototypes["a"], prototypes["b"])
        evidence = {
            segment: classify_segment_profile(
                segment, appearance, prototypes, weights, self.config
            )
            for segment, appearance in profiles.items()
        }
        reserved_ids = {
            row.epoch_index for row in primary.epochs
        } | {int(row["epoch_index"]) for row in primary.unresolved}
        next_epoch_id = max(reserved_ids, default=-1) + 1
        visual_records: list[PlayerIdentityEpoch] = []
        remaining: list[dict] = []
        epoch_diagnostics = []
        transition_count = 0
        source = "hsv_default" if default_labels else "hsv_fallback"
        for parent in sorted(primary.unresolved, key=lambda row: int(row["first_segment"])):
            first, last = int(parent["first_segment"]), int(parent["last_segment"])
            observations = [evidence[segment] for segment in range(first, last + 1)
                            if segment in evidence]
            split = split_orientation_evidence(first, last, observations)
            transition_count += len(split.transitions)
            for position, run in enumerate(split.runs):
                epoch_id = int(parent["epoch_index"]) if position == 0 else next_epoch_id
                if position:
                    next_epoch_id += 1
                visual_records.append(PlayerIdentityEpoch(
                    epoch_index=epoch_id,
                    game_index=int(parent["game_index"]),
                    first_segment=run.first_segment,
                    last_segment=run.last_segment,
                    top=run.top,
                    bottom=run.bottom,
                    votes=0,
                    agreement=0.0,
                    resolved_by=source,
                ))
            if split.unresolved_segments:
                row = dict(parent)
                row["hsv_fallback"] = {
                    "status": "partially_resolved" if split.runs else "abstained",
                    "reason": "unconfirmed_or_missing_segment_orientation",
                    "unresolved_segments": split.unresolved_segments,
                }
                remaining.append(row)
            epoch_diagnostics.append({
                "primary_epoch_index": int(parent["epoch_index"]),
                "first_segment": first,
                "last_segment": last,
                "primary_votes": int(parent["votes"]),
                "segment_observations": len(observations),
                "resolved_observations": sum(row.resolved for row in observations),
                "abstained_observations": sum(not row.resolved for row in observations),
                "transitions": split.transitions,
                "visual_epochs": [
                    {
                        "first_segment": run.first_segment,
                        "last_segment": run.last_segment,
                        "top": run.top,
                        "bottom": run.bottom,
                    }
                    for run in split.runs
                ],
                "unresolved_segments": split.unresolved_segments,
            })

        all_records = sorted(
            [*primary.epochs, *visual_records], key=lambda row: row.first_segment
        )
        segment_diagnostics = []
        for segment in sorted(evidence):
            row = evidence[segment]
            segment_diagnostics.append({
                "segment_index": segment,
                "preferred_top": row.preferred_top,
                "decision": "resolved" if row.resolved else "abstain",
                "assignment_score": round(row.assignment_score, 6),
                "margin": round(row.assignment_margin, 6),
                "sample_count": row.sample_count,
            })
        return HsvFallbackOutcome(
            IdentityResult(all_records, primary.convention, remaining),
            {
                "status": (
                    "partially_resolved" if visual_records and remaining
                    else "resolved" if visual_records
                    else "abstained"
                ),
                "policy": "hsv_fallback_v1",
                "anchor_source": anchor_source,
                "anchor_epoch": anchor_epoch,
                "anchor_segment": anchor_segment,
                "scoreboard_binding": not default_labels,
                "region_weights": {
                    "shirt": round(weights.shirt, 6),
                    "shorts": round(weights.shorts, 6),
                },
                "segment_observations": len(evidence),
                "resolved_observations": sum(row.resolved for row in evidence.values()),
                "abstained_observations": sum(not row.resolved for row in evidence.values()),
                "detected_transitions": transition_count,
                "visual_epoch_count": len(visual_records),
                "segments": segment_diagnostics,
                "epochs": epoch_diagnostics,
            },
        )
