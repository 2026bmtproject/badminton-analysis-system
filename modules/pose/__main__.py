"""CLI entry point: ``python -m modules.pose <match_path>``.

Extracts both players' skeletons through every rally segment: detections into
``cache/pose/``, then the selected players into ``stages/pose/pose.json``.

    # full stage
    uv run python -m modules.pose matches/MK_vs_CT_2019

    # just the GPU pass, so the selection margins can be tuned afterwards for free
    uv run python -m modules.pose matches/MK_vs_CT_2019 --only-detect

    # re-select against the existing cache with a wider court (no GPU pass)
    uv run python -m modules.pose matches/MK_vs_CT_2019 --y-margin 0.35

    # check the selection by eye, incl. players mid-jump
    uv run python -m modules.pose matches/MK_vs_CT_2019 --debug-overlay out/ --debug-frames 24

    # also emit the per-segment skeleton CSVs that BST reads
    uv run python -m modules.pose matches/MK_vs_CT_2019 --csv-dir out/skeletons
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from modules.artifacts import read_records
from modules.common.progress import SmoothProgress
from modules.contracts import PIPELINE, resolve_input_video, stage_path
from modules.pose.estimator import POSE_MODES
from modules.pose.module import PoseConfig, PoseModule
from modules.pose.select import SelectConfig


def parse_args() -> argparse.Namespace:
    default = SelectConfig()
    parser = argparse.ArgumentParser(description="Extract both players' skeletons with RTMPose")
    parser.add_argument("match_path", help="match path, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--pose-mode", default="balanced", choices=POSE_MODES,
                        help="RTMPose size (default balanced = rtmpose-m 256x192)")
    parser.add_argument("--device", default=None,
                        help="default: GPU if it works, else CPU with a warning. Pass "
                             '"cuda" to make a missing GPU an error, or "cpu" to force it')
    parser.add_argument("--person-min-area", type=float, default=0.0,
                        help="drop detections smaller than this fraction of the frame "
                             "(0 = keep all); a cheap way to discard the crowd")
    parser.add_argument("--x-margin", type=float, default=default.x_margin,
                        help=f"court widening across the sidelines, as a fraction of court "
                             f"width (default {default.x_margin}, ~1.5 m): players lunge "
                             f"this far clear of the court chasing a wide shot, and it is "
                             f"the margin that loses them if it is too tight")
    parser.add_argument("--y-margin", type=float, default=default.y_margin,
                        help=f"court widening past the baselines (default {default.y_margin}, "
                             f"~3.4 m): a jumping player's feet project past the baseline, so "
                             f"too small a value loses them exactly during a smash")
    parser.add_argument("--max-step-px", type=float, default=default.max_step_px,
                        help=f"how far a player may move between frames (default "
                             f"{default.max_step_px:g} px); nobody within this of where the "
                             f"player was means the player is reported missing, rather than "
                             f"the nearest line judge being reported as the player")
    parser.add_argument("--only-detect", action="store_true",
                        help="stop after filling the detection cache")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="discard the cached detections and recompute them")
    parser.add_argument("--csv-dir", default=None,
                        help="also export the per-segment skeleton CSVs that BST reads")
    parser.add_argument("--debug-overlay", default=None,
                        help="write annotated frames showing which people were picked")
    parser.add_argument("--debug-frames", type=int, default=12,
                        help="how many frames --debug-overlay samples (default 12)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    match_path = Path(args.match_path)

    config = PoseConfig(
        pose_mode=args.pose_mode,
        device=args.device,
        person_min_area=args.person_min_area,
        select=replace(
            SelectConfig(),
            x_margin=args.x_margin,
            y_margin=args.y_margin,
            max_step_px=args.max_step_px,
        ),
        refresh_cache=args.refresh_cache,
    )
    module = PoseModule(config=config)

    bar = SmoothProgress("pose", total=1000)
    output = module.run(
        match_path,
        on_progress=lambda f: bar.update(int(f * 1000), force=f >= 1.0),
        only_detect=args.only_detect,
    )
    print(f"done -> {output}")

    if args.debug_overlay:
        written = _write_overlays(module, match_path, Path(args.debug_overlay), args.debug_frames)
        print(f"overlay -> {written} frame(s) in {args.debug_overlay}")

    if args.csv_dir and not args.only_detect:
        from modules.pose import csv_export

        spec = PIPELINE["pose"]
        records = read_records(spec, module.get_output_path(match_path))
        segments = module._read_segments(match_path)
        paths = csv_export.export(
            Path(args.csv_dir), records, segments, stem=match_path.name
        )
        print(f"csv -> {len(paths)} segment file(s) in {args.csv_dir}")


def _write_overlays(module: PoseModule, match_path: Path, out_dir: Path, count: int) -> int:
    """Sample frames spread across the match and draw the selection on them.

    Reads the cache rather than re-running the models, so this is nearly free — and it
    means the frames shown are exactly the detections the artifact was built from.
    """
    import cv2

    from modules.common.video import iter_segment_frames
    from modules.pose import detection_cache, overlay
    from modules.pose.select import PlayerTracker

    video = resolve_input_video(match_path)
    segments = module._read_segments(match_path)
    image_to_court = module._read_court(match_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    # One frame from the middle of each of `count` segments spread across the match:
    # mid-rally is where players are actually moving (and lunging), unlike the edges.
    tracker = PlayerTracker(image_to_court, module.config.select)
    step = max(1, len(segments) // count)
    written = 0
    for index in range(0, len(segments), step)[:count]:
        segment = segments[index]
        detections = detection_cache.load_segment(detection_cache.segment_file(match_path, index))
        if not detections:
            continue
        offset = len(detections) // 2

        # The selection has memory, so replaying the rally up to this frame is the only
        # way to draw the decision the stage actually made on it.
        tracker.reset()
        picked = (None, None)
        for det in detections[: offset + 1]:
            picked = tracker.update(det)

        target = int(segment["start_frame"]) + offset
        for _, frame in iter_segment_frames(str(video), target, target):
            canvas = overlay.draw(
                frame, detections[offset], image_to_court, picked, module.config.select
            )
            cv2.imwrite(str(out_dir / f"seg{index:04d}_f{target}.jpg"), canvas)
            written += 1
    return written


if __name__ == "__main__":
    main()
