"""Frame-by-frame MAD (mean absolute difference) scan of a video."""

from __future__ import annotations

import cv2
import numpy as np

from modules.common.progress import SmoothProgress
from modules.match_segmentation.segments import round_to_int


def compute_frame_diff(
    video_path: str,
    frame_step: int = 1,
) -> tuple[np.ndarray, np.ndarray, float, int]:
    """Compute per-frame FrameDiff(MAD), optionally subsampling frames.

    Frame subsampling: only every ``frame_step`` frames is actually decoded
    (retrieve) and compared; skipped frames are grabbed quickly (no color
    conversion/copy) and back-filled with the MAD computed for the interval.
    Output arrays are still indexed by real frame number, so downstream logic
    (segments, seconds, JSON, exclude/rerun) needs no change; only segment
    boundary precision is roughly +/- frame_step frames.
    """
    step = max(int(frame_step), 1)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 30.0

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_frames = max(total_frames, 1)

    ok, prev_frame = cap.read()
    if not ok:
        cap.release()
        raise RuntimeError("cannot read the first frame")

    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    diffs = [0]
    times = [0.0]

    bar = SmoothProgress("step 1: scan FrameDiff(MAD)", total_frames)
    bar.update(1, force=True)

    frame_no = 0
    last_decode_idx = 0
    while True:
        grabbed = cap.grab()
        if not grabbed:
            break

        frame_no += 1
        diffs.append(0)  # placeholder, back-filled after this interval decodes
        times.append(frame_no / fps)

        if frame_no % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(prev_gray, gray)
            score = round_to_int(float(np.mean(diff)))
            prev_gray = gray

            # Fill every skipped frame in this interval with the same score.
            for k in range(last_decode_idx + 1, frame_no + 1):
                diffs[k] = score
            last_decode_idx = frame_no

        bar.update(frame_no + 1)

    cap.release()

    # Trailing frames that did not complete a full step reuse the last score.
    if last_decode_idx < frame_no:
        tail_score = diffs[last_decode_idx]
        for k in range(last_decode_idx + 1, frame_no + 1):
            diffs[k] = tail_score

    processed = frame_no + 1
    bar.update(processed, force=True)

    return np.array(diffs, dtype=float), np.array(times, dtype=float), float(fps), processed
