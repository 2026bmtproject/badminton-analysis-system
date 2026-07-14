"""CLI entry point: ``python -m modules.event_detection <match_path>``.

Finds every hit in every rally: BST's dense scan into ``cache/dense_scan/``, then the
four detection phases into ``stages/event_detection/events.json``.

    # full stage
    uv run python -m modules.event_detection matches/ASG_vs_AA_2020

    # re-tune: the scan is cached, so this is seconds and never touches the GPU
    uv run python -m modules.event_detection matches/ASG_vs_AA_2020 --debug-csv hitevents/

    # scoreboard dead-time rule off, even though scores.json exists
    uv run python -m modules.event_detection matches/ASG_vs_AA_2020 --no-scores
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.common.progress import SmoothProgress
from modules.contracts import SHUTTLE_METHODS
from modules.event_detection.config import EventDetectionConfig
from modules.event_detection.module import EventDetectionModule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect every hit in a match")
    parser.add_argument("match_path", help="match path, e.g. matches/ASG_vs_AA_2020")
    parser.add_argument("--base-method", default="inpaint", choices=SHUTTLE_METHODS,
                        help="the trajectory hits are detected on (default inpaint)")
    parser.add_argument("--aux-method", default="viterbi", choices=SHUTTLE_METHODS,
                        help="the trajectory phase 3 rescues missed hits from "
                             "(default viterbi)")
    parser.add_argument("--bst", default=None,
                        help="BST checkpoint (default: models/bst_...merged.pt)")
    parser.add_argument("--batch-size", type=int, default=256,
                        help="dense-scan inference batch size (default 256)")
    parser.add_argument("--no-scores", action="store_true",
                        help="skip the scoreboard dead-time rule even if scores.json exists")
    parser.add_argument("--debug-csv", default=None, metavar="DIR",
                        help="also write the 18-column detail CSV per segment")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="discard the cached dense scan and recompute it")
    parser.add_argument("--device", default=None, help='force a device, e.g. "cpu"')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.base_method == args.aux_method:
        raise SystemExit(
            "--base-method and --aux-method must differ: the aux stream exists to see "
            "what the base one missed, and pointing both at the same trajectory just "
            "disables phase 3's rescue rules."
        )

    config = EventDetectionConfig(
        base_method=args.base_method,
        aux_method=args.aux_method,
        bst_checkpoint=args.bst,
        batch_size=args.batch_size,
        device=args.device,
        refresh_cache=args.refresh_cache,
        use_scores=not args.no_scores,
    )

    bar = SmoothProgress("event_detection", total=1000)
    module = EventDetectionModule(config=config)
    output = module.run(
        Path(args.match_path),
        on_progress=lambda f: bar.update(int(f * 1000), force=f >= 1.0),
        debug_csv=args.debug_csv,
    )
    print(f"done -> {output}")


if __name__ == "__main__":
    main()
