"""CLI entry point: ``python -m modules.court_detection <match_path>``.

Runs the court_detection stage on a match: reads the segments produced by
match_segmentation, builds a clean composite court image, detects the boundary,
and (by default) opens an OpenCV window so you can drag the four corners to
confirm/adjust before writing ``stages/court_detection/court.json``.

The interactive confirmation is on by default; pass ``--no-confirm`` to write the
automatic result headlessly (e.g. on a machine with no display). The pipeline
runner always runs this stage headlessly regardless of this flag.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.court_detection.interactive import fine_tune
from modules.court_detection.module import CourtDetectionConfig, CourtDetectionModule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect the badminton court boundary with interactive fine-tuning",
    )
    parser.add_argument("match_path", help="match path, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--num-segments", type=int, default=3,
                        help="How many of the longest segments to composite (default 3)")
    parser.add_argument("--frames-per-segment", type=int, default=20,
                        help="Frames sampled from each picked segment (default 20)")
    parser.add_argument("--resize", type=int, default=None,
                        help="Resize sampled frames to this width (default: source resolution)")
    parser.add_argument("--no-confirm", action="store_true",
                        help="Skip the interactive fine-tuning window; write the automatic result")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = CourtDetectionConfig(
        num_segments=args.num_segments,
        frames_per_segment=args.frames_per_segment,
        resize_width=args.resize,
    )

    module = CourtDetectionModule(config=config)
    output = module.run(
        Path(args.match_path),
        confirm=None if args.no_confirm else fine_tune,
    )
    print(f"done -> {output}")


if __name__ == "__main__":
    main()
