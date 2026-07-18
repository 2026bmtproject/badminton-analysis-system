"""Frame-accurate boundary refinement for segment start/end.

The main scan subsamples frames (``frame_step``), so a segment's coarse
boundary is only accurate to +/- ``frame_step``: in practice the start is
detected a little late and the end a little early (the 3-apart diff straddles
the scene cut, so the transition interval reads as motion and is excluded).

At full temporal resolution the scene cut that bounds a rally shows up as a
single dominant adjacent-frame MAD spike (entering/leaving the fixed match
camera): the spike towers over both the still pre-serve frames and the moving
rally itself. This module re-scans a small window around each coarse boundary
at step 1 and snaps the boundary onto that cut:

    start := frame of the cut-in spike            (first frame of the rally view)
    end   := frame of the cut-out spike minus 1   (last frame of the rally view)

When no clear spike is found (e.g. a mid-rally split with no real cut) the
coarse boundary is kept unchanged, so refinement can only help, never invent a
boundary where there is no cut.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class RefineConfig:
    """Tunables for boundary refinement."""

    window: int = 6          # search half-width around each coarse boundary
    min_spike: float = 25.0  # absolute adjacent-MAD floor for a real cut
    k_median: float = 3.0    # and the spike must exceed k_median x window median


def _window_adjacent_mad(cap: cv2.VideoCapture, center: int, window: int, total: int) -> dict[int, float]:
    """Decode [center-window, center+window] and return adjacent-frame MAD by frame."""
    start = max(center - window - 1, 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    prev = None
    out: dict[int, float] = {}
    count = 2 * window + 2
    idx = start
    for _ in range(count):
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            out[idx] = float(np.mean(cv2.absdiff(prev, gray)))
        prev = gray
        idx += 1
        if idx >= total:
            break
    return out


def _find_cut(mad: dict[int, float], lo: int, hi: int, cfg: RefineConfig) -> int | None:
    """Frame of the dominant spike within [lo, hi], or None when there is no clear cut."""
    band = {f: v for f, v in mad.items() if lo <= f <= hi}
    if not band or not mad:
        return None
    median = statistics.median(mad.values())
    frame = max(band, key=band.__getitem__)
    value = band[frame]
    if value >= cfg.min_spike and value >= cfg.k_median * max(median, 1.0):
        return frame
    return None


def bridge_continuous_gaps(
    video_path: str,
    segments: list[tuple[int, int]],
    max_gap: int,
    cfg: RefineConfig | None = None,
) -> tuple[list[tuple[int, int]], int]:
    """Merge adjacent segments split by a brief in-rally motion blip.

    The subsampled scan can break a single rally where a fast exchange or camera
    pan spikes the 3-apart diff for a few frames, even though the broadcast never
    cut away. Distinct rallies, in contrast, are always separated by a scene cut
    (a large adjacent-frame MAD spike), even when only a couple of frames apart.

    So for every gap no wider than ``max_gap`` this checks the gap at frame
    resolution and merges the two sides only when there is **no** cut inside it
    (max adjacent MAD < ``cfg.min_spike``). Across six matches the sole pair of
    distinct rallies within such a gap does contain a cut, so this never fuses
    two real rallies. Returns the merged segments and the number of bridges made.
    """
    cfg = cfg or RefineConfig()
    if len(segments) < 2 or max_gap <= 0:
        return segments, 0

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or (segments[-1][1] + 2)

    result: list[tuple[int, int]] = [segments[0]]
    bridged = 0
    for start, end in segments[1:]:
        prev_start, prev_end = result[-1]
        gap = start - prev_end - 1
        if 0 <= gap <= max_gap:
            mad = _window_adjacent_mad(cap, (prev_end + start) // 2, max_gap + 1, total)
            # transitions bridging the gap, incl. a cut-in landing on ``start``
            inside = [v for f, v in mad.items() if prev_end < f <= start]
            if not inside or max(inside) < cfg.min_spike:
                result[-1] = (prev_start, max(prev_end, end))
                bridged += 1
                continue
        result.append((start, end))

    cap.release()
    return result, bridged


def refine_boundaries(
    video_path: str,
    segments: list[tuple[int, int]],
    cfg: RefineConfig | None = None,
) -> tuple[list[tuple[int, int]], int]:
    """Snap each segment's start/end onto the nearest scene cut at frame resolution.

    Returns the refined segments plus the count of boundaries actually moved.
    Boundaries are clamped so a segment never inverts or crosses its neighbours.
    """
    cfg = cfg or RefineConfig()
    if not segments:
        return [], 0

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or (segments[-1][1] + cfg.window + 2)

    refined: list[tuple[int, int]] = []
    moved = 0
    w = cfg.window
    for i, (start, end) in enumerate(segments):
        prev_end = refined[-1][1] if refined else -1
        next_start = segments[i + 1][0] if i + 1 < len(segments) else total

        mad_s = _window_adjacent_mad(cap, start, w, total)
        cut_s = _find_cut(mad_s, start - w, start + w, cfg)
        new_start = cut_s if cut_s is not None else start

        mad_e = _window_adjacent_mad(cap, end, w, total)
        cut_e = _find_cut(mad_e, end - w, end + w, cfg)
        new_end = (cut_e - 1) if cut_e is not None else end

        # Keep the boundary inside the neighbour gap and non-inverting.
        new_start = max(new_start, prev_end + 1)
        new_end = min(new_end, next_start - 1)
        if new_end < new_start:
            new_start, new_end = start, end

        if new_start != start or new_end != end:
            moved += 1
        refined.append((new_start, new_end))

    cap.release()
    return refined, moved
