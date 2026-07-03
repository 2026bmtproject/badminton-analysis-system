"""Representative-frame extraction and cross-segment MAD comparison."""

from __future__ import annotations

import cv2
import numpy as np

from modules.common.progress import SmoothProgress
from modules.match_segmentation.segments import round_to_int

DEFAULT_COMPARE_SIZE = (128, 72)


def load_required_gray_frames(
    video_path: str,
    required_frames: set[int],
    max_frame_index: int,
) -> dict[int, np.ndarray]:
    """Decode only the representative frames and return them as grayscale."""
    if not required_frames:
        return {}

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    total_to_scan = max_frame_index + 1
    bar = SmoothProgress("step 2: extract key frames", total_to_scan)
    bar.update(0, force=True)

    frame_cache: dict[int, np.ndarray] = {}
    frame_idx = 0

    while frame_idx <= max_frame_index:
        grabbed = cap.grab()
        if not grabbed:
            break

        if frame_idx in required_frames:
            ok, frame = cap.retrieve()
            if not ok:
                cap.release()
                raise RuntimeError(f"cannot retrieve frame: {frame_idx}")
            frame_cache[frame_idx] = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        frame_idx += 1
        bar.update(min(frame_idx, total_to_scan))

    cap.release()
    bar.update(min(frame_idx, total_to_scan), force=True)

    missing = required_frames.difference(frame_cache.keys())
    if missing:
        raise RuntimeError(f"failed to read key frames, missing {len(missing)}")

    return frame_cache


def build_segment_vectors(
    pairs: list[tuple[int, int]],
    frame_cache: dict[int, np.ndarray],
    compare_size: tuple[int, int] = DEFAULT_COMPARE_SIZE,
) -> list[np.ndarray]:
    """Resize each representative frame pair into flat comparison vectors."""
    vectors: list[np.ndarray] = []

    for m1, m2 in pairs:
        f1 = cv2.resize(frame_cache[m1], compare_size, interpolation=cv2.INTER_AREA)
        f2 = cv2.resize(frame_cache[m2], compare_size, interpolation=cv2.INTER_AREA)

        seg_vec = np.stack([f1.reshape(-1), f2.reshape(-1)]).astype(np.int16)
        vectors.append(seg_vec)

    return vectors


def compute_cross_segment_scores(
    pairs: list[tuple[int, int]],
    frame_cache: dict[int, np.ndarray],
    compare_size: tuple[int, int] = DEFAULT_COMPARE_SIZE,
) -> tuple[list[int], list[int], int]:
    """Compute pairwise cross-segment MAD sums and averages."""
    seg_count = len(pairs)
    if seg_count == 0:
        return [], [], 0
    if seg_count == 1:
        return [0], [0], 0

    vectors = build_segment_vectors(pairs, frame_cache, compare_size)

    sums = np.zeros(seg_count, dtype=np.float64)
    compared_segments = seg_count - 1

    total_pairs = seg_count * (seg_count - 1) // 2
    done_pairs = 0
    bar = SmoothProgress("step 3: cross-segment MAD", total_pairs)
    bar.update(0, force=True)

    for i in range(seg_count):
        vi = vectors[i]
        for j in range(i + 1, seg_count):
            vj = vectors[j]
            mad_matrix = np.mean(np.abs(vi[:, None, :] - vj[None, :, :]), axis=2)
            pair_sum = float(np.sum(mad_matrix))
            sums[i] += pair_sum
            sums[j] += pair_sum

            done_pairs += 1
            bar.update(done_pairs)

    bar.update(total_pairs, force=True)

    avgs = sums / (compared_segments * 4)
    sums_int = [round_to_int(x) for x in sums.tolist()]
    avgs_int = [round_to_int(x) for x in avgs.tolist()]
    return sums_int, avgs_int, compared_segments
