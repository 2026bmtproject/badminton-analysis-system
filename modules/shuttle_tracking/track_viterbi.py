"""Tracker B — trajectory selection over multiple heatmap candidates.

Where ``track_inpaint`` commits to the single strongest blob per frame and then
repairs the gaps, this tracker keeps its options open: it pulls every plausible
blob out of the heatmap at a much lower threshold, then asks which *sequence* of
choices forms the most shuttle-like flight. A weak-but-consistent detection beats
a strong-but-isolated one, so the shuttle survives frames where a player's shoe or
a line marking wins the pixel contest.

Four steps:

1. **Candidates** — every connected component above a low threshold, scored by its
   peak and located at its intensity-weighted centroid (sub-pixel, unlike a
   bounding-box centre). Plus the baseline blob position as a low-confidence
   fallback, so this tracker can never do strictly worse than the blob it ignored.
2. **Viterbi** — the maximum-reward path through the candidate DAG: reward for
   picking confident detections, cost for implausible motion between them, a
   penalty for skipping frames. Gap-tolerant, so an occluded shuttle can be
   re-acquired.
3. **Anchor pruning** — a chosen point with no *confident* detection nearby in time
   is a hallucination (the path has to go somewhere, even through dead time), and
   is dropped.
4. **Gap filling** — short gaps are always interpolated; long ones only when the
   shuttle was moving fast on both sides (mid-flight occlusion). A gap bracketed
   by near-the-top positions is never filled: the shuttle left the frame.

**Every pixel/frame constant below was tuned at 1080p and 30 fps** and is rescaled
to the actual video by :func:`scale_params`. Left unscaled, a 720p or 60 fps source
would silently degrade — speed gates in px/frame are meaningless without knowing
both.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from scipy.interpolate import splev, splrep
from scipy.ndimage import find_objects, label

#: The resolution and frame rate every constant in ViterbiConfig was tuned at.
REF_HEIGHT = 1080
REF_FPS = 30.0


@dataclass(frozen=True)
class ViterbiConfig:
    """Knobs for tracker B. Distances are px, durations are frames, both at 1080p/30fps."""

    # Candidate extraction (heatmap space — resolution-independent, never scaled).
    threshold: int = 10           # 0-255; anything fainter is noise
    max_candidates: int = 8       # per frame, strongest first
    min_area: int = 2             # px; smaller blobs need a strong peak to count

    # Viterbi path search.
    max_speed: float = 120.0      # px/frame; edges faster than this are impossible
    speed_slack: float = 40.0     # px of headroom on that gate
    max_skip: int = 15            # frames an edge may jump over
    node_reward: float = 6.0      # reward for using any detection at all
    conf_weight: float = 8.0      # extra reward, scaled by detection confidence
    motion_weight: float = 1.0    # cost weight on implausible speed
    speed_scale: float = 25.0     # px/frame; the speed at which motion cost hits 1
    skip_penalty: float = 0.8     # cost per frame skipped

    # Baseline fallback candidate.
    baseline_conf: float = 0.15   # confidence given to the blob track's position
    baseline_dedupe: float = 20.0 # px; closer than this to a real candidate = duplicate

    # Anchor pruning.
    anchor_conf: float = 0.6      # what counts as a "confident" detection
    anchor_window: int = 4        # frames either side to look for one

    # Gap filling.
    fill: str = "linear"          # linear | kalman | spline | physics
    max_gap: int = 6              # frames; always filled
    long_gap: int = 40            # frames; filled only if fast on both sides
    min_speed: float = 4.0        # px/frame; what "fast" means there
    top_margin: float = 112.0     # px from the top; gaps bracketed here are never filled
    peak_prominence: float = 20.0 # px of vertical swing marking one shot's turning point

    # Kalman / spline internals.
    accel_sigma: float = 6.0      # px/frame^2 process noise
    meas_noise: float = 4.0       # px measurement noise at confidence 1
    gate: float = 6.0             # Mahalanobis gate (unitless)
    spline_smooth: float = 9.0    # px^2 residual budget per point


def scale_params(cfg: ViterbiConfig, img_height: int, fps: float) -> ViterbiConfig:
    """Rescale the 1080p/30fps constants to this video's resolution and frame rate.

    Two independent factors. **Space**: every px constant scales with the frame
    height. **Time**: at a higher frame rate the shuttle moves less between
    consecutive frames, so px/frame limits shrink — while frame *counts* (a gap of
    N frames, a window of N frames) grow, since a fixed span of real time is more
    frames.
    """
    space = img_height / REF_HEIGHT
    time = fps / REF_FPS  # frames per reference frame
    per_frame = space / time  # px-per-frame quantities

    def frames(n: int) -> int:
        return max(1, round(n * time))

    return replace(
        cfg,
        max_speed=cfg.max_speed * per_frame,
        speed_slack=cfg.speed_slack * space,
        speed_scale=cfg.speed_scale * per_frame,
        min_speed=cfg.min_speed * per_frame,
        top_margin=cfg.top_margin * space,
        peak_prominence=cfg.peak_prominence * space,
        accel_sigma=cfg.accel_sigma * space / time**2,
        meas_noise=cfg.meas_noise * space,
        spline_smooth=cfg.spline_smooth * space**2,
        max_skip=frames(cfg.max_skip),
        anchor_window=frames(cfg.anchor_window),
        max_gap=frames(cfg.max_gap),
        long_gap=frames(cfg.long_gap),
    )


# --------------------------------------------------------------------------- #
# 1. Candidates
# --------------------------------------------------------------------------- #

Candidate = tuple[float, float, float]  # (x, y, confidence), x/y in source px


def extract_candidates(
    heatmaps: np.ndarray,
    img_shape: tuple[int, int],
    cfg: ViterbiConfig,
) -> list[list[Candidate]]:
    """Every plausible blob per frame, strongest first, in source-video pixels."""
    num_frames, hm_h, hm_w = heatmaps.shape
    img_w, img_h = img_shape
    sx, sy = img_w / hm_w, img_h / hm_h

    out: list[list[Candidate]] = []
    for t in range(num_frames):
        frame = heatmaps[t]
        mask = frame >= cfg.threshold
        candidates: list[Candidate] = []
        if mask.any():
            labels, _ = label(mask)
            for i, box in enumerate(find_objects(labels), start=1):
                blob = frame[box] * (labels[box] == i)
                peak = float(blob.max())
                if int((blob > 0).sum()) < cfg.min_area and peak < 2 * cfg.threshold:
                    continue  # a single faint pixel: noise
                ys, xs = np.nonzero(blob)
                w = blob[ys, xs].astype(float)
                cx = (xs * w).sum() / w.sum() + box[1].start
                cy = (ys * w).sum() / w.sum() + box[0].start
                candidates.append((cx * sx, cy * sy, peak / 255.0))
            candidates.sort(key=lambda c: -c[2])
        out.append(candidates[: cfg.max_candidates])
    return out


def add_baseline_candidates(
    candidates: list[list[Candidate]],
    xy_base: np.ndarray,
    cfg: ViterbiConfig,
) -> list[list[Candidate]]:
    """Add the blob track's position as a weak fallback wherever it is new.

    Deliberately the *baseline* blob positions, never an inpainted trajectory:
    feeding interpolated points in as observations would let one tracker's guesses
    reinforce the other's.
    """
    out = []
    for t, frame_candidates in enumerate(candidates):
        cands = list(frame_candidates)
        if t < len(xy_base) and not np.isnan(xy_base[t, 0]):
            x, y = float(xy_base[t, 0]), float(xy_base[t, 1])
            duplicate = any(
                math.hypot(x - cx, y - cy) < cfg.baseline_dedupe for cx, cy, _ in cands
            )
            if not duplicate:
                cands.append((x, y, cfg.baseline_conf))
        out.append(cands)
    return out


# --------------------------------------------------------------------------- #
# 2. Viterbi
# --------------------------------------------------------------------------- #


def viterbi_select(
    candidates: list[list[Candidate]],
    cfg: ViterbiConfig,
) -> dict[int, Candidate]:
    """Highest-reward path through the candidate DAG: ``{frame: (x, y, conf)}``.

    Nodes are candidates; an edge may skip up to ``max_skip`` frames. Score is
    reward for the nodes used minus cost for the motion implied between them, so
    the path prefers many confident detections joined by plausible flight.
    """
    nodes = [(t, x, y, c) for t, frame in enumerate(candidates) for (x, y, c) in frame]
    if not nodes:
        return {}
    nodes.sort(key=lambda n: n[0])

    times = np.array([n[0] for n in nodes])
    best = np.array([cfg.node_reward + cfg.conf_weight * n[3] for n in nodes])
    prev = np.full(len(nodes), -1, dtype=int)
    # First node that is still within max_skip frames of node i — bounds the scan.
    first = np.searchsorted(times, times - cfg.max_skip, side="left")

    for i, (ti, xi, yi, ci) in enumerate(nodes):
        gain = cfg.node_reward + cfg.conf_weight * ci
        for j in range(first[i], i):
            tj, xj, yj, _ = nodes[j]
            dt = ti - tj
            if dt <= 0:
                continue  # same frame: can't pick two positions at once
            dist = math.hypot(xi - xj, yi - yj)
            if dist > cfg.max_speed * dt + cfg.speed_slack:
                continue  # no shuttle moves that fast
            cost = (
                cfg.motion_weight * (dist / dt / cfg.speed_scale) ** 2
                + cfg.skip_penalty * (dt - 1)
            )
            score = best[j] + gain - cost
            if score > best[i]:
                best[i] = score
                prev[i] = j

    track: dict[int, Candidate] = {}
    i = int(np.argmax(best))
    while i != -1:
        t, x, y, c = nodes[i]
        track[t] = (x, y, c)
        i = int(prev[i])
    return track


# --------------------------------------------------------------------------- #
# 3. Pruning
# --------------------------------------------------------------------------- #


def prune_track(
    xy: np.ndarray,
    conf: np.ndarray,
    cfg: ViterbiConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Drop points with no confident detection within ``anchor_window`` frames.

    The path search must connect *something* even across dead time, so it emits
    chains of weak candidates where no shuttle is in play. Requiring a nearby
    anchor deletes exactly those chains.
    """
    xy, conf = xy.copy(), conf.copy()
    present = np.where(~np.isnan(xy[:, 0]))[0]
    anchors = np.where(~np.isnan(xy[:, 0]) & (conf >= cfg.anchor_conf))[0]

    if len(anchors) == 0:
        xy[:] = np.nan
        conf[:] = 0.0
        return xy, conf

    for t in present:
        lo = np.searchsorted(anchors, t - cfg.anchor_window)
        hi = np.searchsorted(anchors, t + cfg.anchor_window, side="right")
        if hi <= lo:
            xy[t] = np.nan
            conf[t] = 0.0
    return xy, conf


# --------------------------------------------------------------------------- #
# 4. Fills
# --------------------------------------------------------------------------- #


def fill_linear(xy: np.ndarray, conf: np.ndarray, cfg: ViterbiConfig) -> np.ndarray:
    """Straight-line interpolation between observed points."""
    out = xy.copy()
    present = np.where(~np.isnan(xy[:, 0]))[0]
    for a, b in zip(present[:-1], present[1:]):
        for t in range(a + 1, b):
            w = (t - a) / (b - a)
            out[t] = (1 - w) * xy[a] + w * xy[b]
    return out


def _kalman_setup(cfg: ViterbiConfig):
    """Constant-velocity model: state (x, y, vx, vy), observing position only."""
    F = np.eye(4)
    F[0, 2] = F[1, 3] = 1.0
    G = np.array([[0.5, 0], [0, 0.5], [1, 0], [0, 1]])  # accel -> state
    Q = cfg.accel_sigma**2 * (G @ G.T)
    H = np.zeros((2, 4))
    H[0, 0] = H[1, 1] = 1.0
    return F, Q, H


def kalman_gate_mask(xy: np.ndarray, conf: np.ndarray, cfg: ViterbiConfig) -> np.ndarray:
    """Which observations a Kalman filter would accept rather than reject as outliers."""
    n = len(xy)
    valid = ~np.isnan(xy[:, 0])
    present = np.where(valid)[0]
    accepted = np.zeros(n, dtype=bool)
    if len(present) < 2:
        accepted[present] = True
        return accepted

    F, Q, H = _kalman_setup(cfg)
    first, last = present[0], present[-1]
    x = np.array([xy[first, 0], xy[first, 1], 0.0, 0.0])
    P = np.diag([25.0, 25.0, 400.0, 400.0])

    for t in range(first, last + 1):
        if t > first:
            x = F @ x
            P = F @ P @ F.T + Q
        if valid[t]:
            R = np.eye(2) * (cfg.meas_noise / max(conf[t], 0.15)) ** 2
            S = H @ P @ H.T + R
            innovation = xy[t] - H @ x
            if innovation @ np.linalg.solve(S, innovation) <= cfg.gate**2:
                K = P @ H.T @ np.linalg.inv(S)
                x = x + K @ innovation
                P = (np.eye(4) - K @ H) @ P
                accepted[t] = True
    accepted[first] = True
    return accepted


def fill_kalman(xy: np.ndarray, conf: np.ndarray, cfg: ViterbiConfig) -> np.ndarray:
    """Kalman filter + RTS smoother. Accepted observations are kept exactly."""
    n = len(xy)
    valid = ~np.isnan(xy[:, 0])
    present = np.where(valid)[0]
    if len(present) < 2:
        return xy.copy()

    F, Q, H = _kalman_setup(cfg)
    first, last = present[0], present[-1]

    filtered = np.zeros((n, 4))
    cov = np.zeros((n, 4, 4))
    predicted = np.zeros((n, 4))
    pred_cov = np.zeros((n, 4, 4))
    used = np.zeros(n, dtype=bool)

    x = np.array([xy[first, 0], xy[first, 1], 0.0, 0.0])
    P = np.diag([25.0, 25.0, 400.0, 400.0])

    for t in range(first, last + 1):
        if t > first:
            x = F @ x
            P = F @ P @ F.T + Q
        predicted[t], pred_cov[t] = x, P
        if valid[t]:
            R = np.eye(2) * (cfg.meas_noise / max(conf[t], 0.15)) ** 2
            S = H @ P @ H.T + R
            innovation = xy[t] - H @ x
            if innovation @ np.linalg.solve(S, innovation) <= cfg.gate**2:
                K = P @ H.T @ np.linalg.inv(S)
                x = x + K @ innovation
                P = (np.eye(4) - K @ H) @ P
                used[t] = True
        filtered[t], cov[t] = x, P

    smoothed = filtered.copy()
    for t in range(last - 1, first - 1, -1):
        C = cov[t] @ F.T @ np.linalg.inv(pred_cov[t + 1])
        smoothed[t] = filtered[t] + C @ (smoothed[t + 1] - predicted[t + 1])

    out = np.full((n, 2), np.nan)
    out[first : last + 1] = smoothed[first : last + 1, :2]
    out[used] = xy[used]
    return out


def fill_spline(xy: np.ndarray, conf: np.ndarray, cfg: ViterbiConfig) -> np.ndarray:
    """Confidence-weighted cubic smoothing spline. Falls back to linear if too sparse."""
    n = len(xy)
    present = np.where(~np.isnan(xy[:, 0]))[0]
    if len(present) < 8:
        return fill_linear(xy, conf, cfg)

    weights = np.maximum(conf[present], 0.15)
    out = np.full((n, 2), np.nan)
    span = np.arange(present[0], present[-1] + 1)
    for d in range(2):
        tck = splrep(
            present.astype(float), xy[present, d], w=weights,
            s=cfg.spline_smooth * len(present), k=3,
        )
        out[span, d] = splev(span, tck)

    accepted = kalman_gate_mask(xy, conf, cfg)
    out[accepted] = xy[accepted]
    return out


def fill_physics(xy: np.ndarray, conf: np.ndarray, cfg: ViterbiConfig) -> np.ndarray:
    """Per-shot constant-acceleration fit between trajectory turning points.

    A shuttle between two hits follows a smooth arc, so the sequence is split at the
    peaks/valleys of its vertical motion — each piece is one flight — and a robust
    quadratic is fitted to each. Closer to the physics than an interpolation, at the
    cost of depending on the turning points being found correctly.
    """
    from scipy.signal import find_peaks

    n = len(xy)
    valid = ~np.isnan(xy[:, 0])
    present = np.where(valid)[0]
    if len(present) < 8:
        return fill_linear(xy, conf, cfg)

    interim = fill_kalman(xy, conf, cfg)
    have = ~np.isnan(interim[:, 0])
    ys = interim[:, 1].copy()
    ys[~have] = np.nanmean(interim[have, 1]) if have.any() else 0.0

    peaks, _ = find_peaks(ys, prominence=cfg.peak_prominence)
    valleys, _ = find_peaks(-ys, prominence=cfg.peak_prominence)
    breakpoints = sorted({present[0], *peaks, *valleys, present[-1]})

    out = interim.copy()
    min_seg = 6
    for a, b in zip(breakpoints[:-1], breakpoints[1:]):
        seg = [t for t in range(a, b + 1) if valid[t]]
        if len(seg) < min_seg or b - a < min_seg:
            continue
        ts = np.array(seg, dtype=float)
        centre = ts.mean()
        A = np.vstack([np.ones_like(ts), ts - centre, (ts - centre) ** 2]).T

        for d in range(2):
            target = xy[seg, d]
            base_w = np.maximum(conf[seg], 0.15)
            w = base_w
            for _ in range(4):  # Huber IRLS: down-weight whatever the fit misses
                W = A * w[:, None]
                coef, *_ = np.linalg.lstsq(W, target * w, rcond=None)
                residual = np.abs(A @ coef - target)
                scale = max(np.median(residual) * 1.4826, 2.0)
                w = base_w / np.maximum(residual / (2 * scale), 1.0)
            span = np.arange(a, b + 1, dtype=float)
            A_span = np.vstack(
                [np.ones_like(span), span - centre, (span - centre) ** 2]
            ).T
            fit = A_span @ coef
            for k, t in enumerate(range(a, b + 1)):
                if not valid[t]:
                    out[t, d] = fit[k]

    accepted = kalman_gate_mask(xy, conf, cfg)
    out[accepted] = xy[accepted]
    return out


FILLS = {
    "linear": fill_linear,
    "kalman": fill_kalman,
    "spline": fill_spline,
    "physics": fill_physics,
}


def apply_gap_policy(
    xy: np.ndarray,
    filled: np.ndarray,
    cfg: ViterbiConfig,
) -> np.ndarray:
    """Erase the fills we do not believe in.

    Short gaps stay filled. A long gap is only believable as a mid-flight occlusion,
    which requires the shuttle to have been moving fast on both sides of it. And a
    gap whose two ends both sit near the top of the frame is the shuttle leaving the
    camera on a clear — filling it would invent a flight that never happened.
    """
    out = filled.copy()
    present = np.where(~np.isnan(xy[:, 0]))[0]
    if len(present) < 2:
        return out

    def speed_at(t: int, direction: int) -> float:
        """Mean px/frame over the few observed points before (or after) ``t``."""
        k = int(np.searchsorted(present, t))
        window = present[max(0, k - 3) : k + 1] if direction < 0 else present[k : k + 4]
        if len(window) < 2:
            return 0.0
        travelled = np.hypot(
            np.diff(xy[window, 0]), np.diff(xy[window, 1])
        ).sum()
        return travelled / (window[-1] - window[0])

    for a, b in zip(present[:-1], present[1:]):
        gap = b - a - 1
        if gap == 0:
            continue
        if xy[a, 1] < cfg.top_margin and xy[b, 1] < cfg.top_margin:
            out[a + 1 : b] = np.nan  # left the frame at the top
            continue
        if gap <= cfg.max_gap:
            continue
        if (
            gap <= cfg.long_gap
            and speed_at(a, -1) >= cfg.min_speed
            and speed_at(b, 1) >= cfg.min_speed
        ):
            continue  # fast on both sides: a real flight we simply lost sight of
        out[a + 1 : b] = np.nan
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def track(
    heatmaps: np.ndarray,
    xy_base: np.ndarray,
    img_shape: tuple[int, int],
    fps: float,
    cfg: ViterbiConfig | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Run the whole tracker. Returns ``(xy (T, 2) with NaN, conf (T,))``.

    ``xy_base`` is the blob track (``blob.baseline_track``), used only as a fallback
    candidate. Filled positions carry confidence 0 — they are interpolated, not
    observed.
    """
    cfg = scale_params(cfg or ViterbiConfig(), img_shape[1], fps)
    if cfg.fill not in FILLS:
        raise ValueError(f"unknown fill {cfg.fill!r}; expected one of {sorted(FILLS)}")

    num_frames = len(heatmaps)
    candidates = extract_candidates(heatmaps, img_shape, cfg)
    candidates = add_baseline_candidates(candidates, xy_base, cfg)

    selected = viterbi_select(candidates, cfg)
    xy = np.full((num_frames, 2), np.nan)
    conf = np.zeros(num_frames)
    for t, (x, y, c) in selected.items():
        if 0 <= t < num_frames:
            xy[t] = (x, y)
            conf[t] = c

    xy, conf = prune_track(xy, conf, cfg)
    filled = FILLS[cfg.fill](xy, conf, cfg)
    final = apply_gap_policy(xy, filled, cfg)

    out_conf = np.where(np.isnan(xy[:, 0]), 0.0, conf)
    out_conf[np.isnan(final[:, 0])] = 0.0
    return final, out_conf
