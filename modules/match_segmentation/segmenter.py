"""End-to-end match segmentation pipeline and JSON output.

Pipeline:
    1) per-frame FrameDiff(MAD)
    2) GMM auto-threshold -> low-motion mask
    3) contiguous low-motion frames -> segments, merge close ones
    4) drop too-short segments
    5) two representative frames per segment, cross-segment MAD
    6) filter by Cross_Diff_Avg threshold
    7) final merge by frame gap
    8) write a compact segments JSON

CLI usage:
    python -m modules.match_segmentation.segmenter IN.mp4 OUT.json
    python -m modules.match_segmentation.segmenter IN.mp4 OUT.json --exclude prev.json
"""

from __future__ import annotations

import argparse
import glob
import os
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from modules.common.downscale import ensure_max_height
from modules.common.segments_io import write_segments
from modules.match_segmentation.cross_compare import (
    DEFAULT_COMPARE_SIZE,
    compute_cross_segment_scores,
    load_required_gray_frames,
)
from modules.match_segmentation.frame_diff import compute_frame_diff
from modules.match_segmentation.segments import (
    build_segments_from_mask,
    clamp,
    collect_required_frames,
    compute_avg_threshold,
    filter_segments_by_cross_avg,
    filter_short_segments,
    find_threshold_gmm,
    load_excluded_frames,
    merge_close_segments,
    merge_segments_by_gap,
)

DEFAULT_MERGE_MIN_RATIO = 0.5
DEFAULT_MIN_SEGMENT_SECONDS = 0.5
DEFAULT_AVG_THR_SCALE = 0.7
DEFAULT_FRAME_STEP = 3
DEFAULT_FINAL_MERGE_GAP = 5
DEFAULT_SCAN_MAX_HEIGHT = 480

ProgressCallback = Callable[[float], None]


@dataclass
class SegmentationConfig:
    """Tunable parameters for the segmentation pipeline."""

    merge_min_ratio: float = DEFAULT_MERGE_MIN_RATIO
    min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS
    avg_thr_scale: float = DEFAULT_AVG_THR_SCALE
    avg_thr_pct: float | None = None
    frame_step: int = DEFAULT_FRAME_STEP
    final_merge_gap: int = DEFAULT_FINAL_MERGE_GAP
    compare_size: tuple[int, int] = DEFAULT_COMPARE_SIZE
    # Downscale the source to this height before scanning when it is taller;
    # 0 disables. Downscaling preserves fps and frame count, so the emitted
    # frame/second boundaries still map onto the original video.
    scan_max_height: int = DEFAULT_SCAN_MAX_HEIGHT


@dataclass
class SegmentationResult:
    """Segments plus statistics gathered while running the pipeline."""

    segments: list[tuple[int, int]]
    fps: float
    processed_frames: int
    duration_sec: float
    threshold: float
    low_frames: int
    total_scored: int
    raw_count: int
    merged_count: int
    candidate_count: int
    min_avg: int
    used_pct: float
    avg_threshold: int
    compared_segments: int
    filtered_count: int
    excluded_frame_count: int
    key_frame_cache: int
    cross_avgs: list[int] = field(default_factory=list)


def _report(on_progress: ProgressCallback | None, ratio: float) -> None:
    if on_progress is not None:
        on_progress(clamp(ratio, 0.0, 1.0))


def segment_video(
    video_path: str,
    config: SegmentationConfig | None = None,
    exclude_path: str | None = None,
    on_progress: ProgressCallback | None = None,
    workdir: str | None = None,
) -> SegmentationResult:
    """Run the full segmentation pipeline and return segments plus stats.

    When the source is taller than ``config.scan_max_height`` it is first
    downscaled (cached under ``workdir``, or next to the source if omitted) and
    the whole scan runs on the smaller copy. fps and frame count are preserved,
    so the returned segments are valid for the original video.
    """
    config = config or SegmentationConfig()
    _report(on_progress, 0.0)

    scan_path, _ = ensure_max_height(video_path, config.scan_max_height, workdir=workdir)

    excluded_frames: set[int] = set()
    if exclude_path:
        excluded_frames = load_excluded_frames(exclude_path)

    scores, times, fps, processed_frames = compute_frame_diff(scan_path, config.frame_step)
    _report(on_progress, 0.5)

    excluded_mask = np.zeros(scores.size, dtype=bool)
    if excluded_frames:
        valid_excluded = [f for f in excluded_frames if 0 <= f < scores.size]
        excluded_mask[valid_excluded] = True

    # Fit the GMM threshold only on non-excluded frames so a rerun really
    # searches within the remaining frames.
    threshold = find_threshold_gmm(scores[~excluded_mask])
    is_low = scores < threshold
    if is_low.size > 0:
        is_low[0] = False
    # Mark excluded frames as non-low so they cannot form or bridge segments.
    is_low[excluded_mask] = False

    raw_segments = build_segments_from_mask(is_low)
    merged_segments = merge_close_segments(raw_segments, is_low, config.merge_min_ratio)
    candidate_segments = filter_short_segments(merged_segments, fps, config.min_segment_seconds)

    pairs, required_frames = collect_required_frames(candidate_segments)
    max_required = max(required_frames) if required_frames else 0
    frame_cache = load_required_gray_frames(scan_path, required_frames, max_required)
    _report(on_progress, 0.9)

    cross_sums, cross_avgs, compared_segments = compute_cross_segment_scores(
        pairs, frame_cache, config.compare_size
    )

    min_avg, avg_threshold, used_pct = compute_avg_threshold(
        cross_avgs,
        len(candidate_segments),
        config.avg_thr_scale,
        config.avg_thr_pct,
    )
    filtered_segments, _, _, _ = filter_segments_by_cross_avg(
        candidate_segments, pairs, cross_sums, cross_avgs, avg_threshold
    )

    final_segments = merge_segments_by_gap(filtered_segments, int(config.final_merge_gap))
    _report(on_progress, 1.0)

    return SegmentationResult(
        segments=final_segments,
        fps=float(fps),
        processed_frames=processed_frames,
        duration_sec=float(times[-1]) if times.size else 0.0,
        threshold=float(threshold),
        low_frames=int(np.sum(is_low)),
        total_scored=int(scores.size),
        raw_count=len(raw_segments),
        merged_count=len(merged_segments),
        candidate_count=len(candidate_segments),
        min_avg=min_avg,
        used_pct=used_pct,
        avg_threshold=avg_threshold,
        compared_segments=compared_segments,
        filtered_count=len(filtered_segments),
        excluded_frame_count=int(np.sum(excluded_mask)),
        key_frame_cache=len(frame_cache),
        cross_avgs=cross_avgs,
    )


def pick_default_video() -> str:
    """Return the first *.mp4 in the current directory (CLI convenience)."""
    videos = sorted(glob.glob("*.mp4"))
    if not videos:
        raise FileNotFoundError("no .mp4 found; please pass a video path")
    return videos[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FrameDiff(MAD) + GMM + per-segment 2-frame cross comparison",
    )
    parser.add_argument("video_path", nargs="?", default=None, help="input video path (default: first *.mp4 here)")
    parser.add_argument("output_json", nargs="?", default=None, help="output JSON path (default <name>_segments.json)")
    parser.add_argument("--avg-thr-scale", dest="avg_thr_scale", type=float, default=DEFAULT_AVG_THR_SCALE,
                        help="Cross_Diff_Avg threshold strength (0-1, smaller = stricter)")
    parser.add_argument("--merge-min-ratio", dest="merge_min_ratio", type=float, default=DEFAULT_MERGE_MIN_RATIO,
                        help="low-diff ratio inside a gap to merge (0-1, ratio >= threshold merges)")
    parser.add_argument("--avg-thr-pct", dest="avg_thr_pct", type=float, default=None,
                        help="fixed threshold percentage (e.g. 0.05 = +5%%), overrides the dynamic strategy")
    parser.add_argument("--exclude", dest="exclude_path", default=None,
                        help="exclude & rerun: skip all frames in this previously-written segments JSON")
    parser.add_argument("--frame-step", dest="frame_step", type=int, default=DEFAULT_FRAME_STEP,
                        help="FrameDiff(MAD) subsample step (default 3; decode 1 every N frames, 1 = every frame)")
    parser.add_argument("--final-merge-gap", dest="final_merge_gap", type=int, default=DEFAULT_FINAL_MERGE_GAP,
                        help="final merge: join adjacent segments with frame gap < this (default 5; <=0 disables)")
    parser.add_argument("--scan-max-height", dest="scan_max_height", type=int, default=DEFAULT_SCAN_MAX_HEIGHT,
                        help="downscale the source to this height before scanning if taller (default 480; 0 disables)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    video_path = args.video_path if args.video_path else pick_default_video()
    output_json = (
        args.output_json
        if args.output_json
        else f"{os.path.splitext(os.path.basename(video_path))[0]}_segments.json"
    )

    config = SegmentationConfig(
        merge_min_ratio=args.merge_min_ratio,
        avg_thr_scale=args.avg_thr_scale,
        avg_thr_pct=args.avg_thr_pct,
        frame_step=max(int(args.frame_step), 1),
        final_merge_gap=int(args.final_merge_gap),
        scan_max_height=int(args.scan_max_height),
    )

    print("=" * 72)
    print("match_segmentation: FrameDiff(MAD) + GMM + cross-segment filtering")
    print("=" * 72)
    print(f"video:  {video_path}")
    print(f"output: {output_json}")
    if config.scan_max_height > 0:
        print(f"scan max height: {config.scan_max_height}p (downscale source if taller)")
    else:
        print("scan max height: disabled (scan at source resolution)")
    if config.frame_step > 1:
        print(f"subsample: decode 1 every {config.frame_step} frames")
    else:
        print("subsample: every frame")
    if args.exclude_path:
        print(f"exclude & rerun: {args.exclude_path}")

    result = segment_video(video_path, config, exclude_path=args.exclude_path)
    write_segments(output_json, result.segments, result.fps)

    print("-" * 72)
    print(f"FPS: {result.fps:.3f}")
    print(f"total frames: {result.processed_frames}")
    print(f"duration: {result.duration_sec / 60:.1f} min")
    print(f"GMM threshold: {result.threshold:.2f}")
    if result.excluded_frame_count:
        print(f"excluded frames: {result.excluded_frame_count}")
    print(f"below threshold: {result.low_frames} ({result.low_frames / max(result.total_scored, 1) * 100:.1f}%)")
    print(f"raw segments: {result.raw_count}")
    print(f"merged segments: {result.merged_count}")
    print(f"merge threshold (gap low-diff ratio): {clamp(config.merge_min_ratio, 0.0, 1.0):.2f}")
    print(f"candidate segments (min {config.min_segment_seconds:.1f}s): {result.candidate_count}")
    if result.cross_avgs:
        print(f"Cross_Diff_Avg min: {result.min_avg}")
        print(f"Cross_Diff_Avg pct: {result.used_pct * 100:.2f}%")
        print(f"Cross_Diff_Avg threshold: {result.avg_threshold}")
    else:
        print("Cross_Diff_Avg: no candidate segments")
    print(f"reference segments: {result.compared_segments}")
    print(f"segments after Cross_Diff_Avg filter: {result.filtered_count}")
    if config.final_merge_gap > 0:
        print(f"segments after final merge (gap < {config.final_merge_gap}): {len(result.segments)}")
    else:
        print("final merge: disabled")
    print(f"final kept segments: {len(result.segments)}")
    print(f"key-frame cache: {result.key_frame_cache}")
    print("done")


if __name__ == "__main__":
    main()
