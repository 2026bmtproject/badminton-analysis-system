"""CLI entry point: ``python -m modules.shuttle_tracking <match_path>``.

Tracks the shuttle through every rally segment of a match: TrackNet heatmaps into
``cache/heatmaps/``, then both trackers into
``stages/shuttle_tracking/shuttle.json``.

    # full stage
    uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019

    # just the GPU pass, so the trackers can be iterated on afterwards for free
    uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019 --only-heatmap

    # re-run one tracker against the existing heatmap cache (no GPU pass)
    uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019 --method viterbi --fill kalman
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from modules.common.progress import SmoothProgress
from modules.contracts import SHUTTLE_METHODS
from modules.shuttle_tracking.inference import EVAL_MODES
from modules.shuttle_tracking.module import (
    DEFAULT_INPAINTNET,
    DEFAULT_TRACKNET,
    MAX_CHUNK_FRAMES,
    ShuttleTrackingConfig,
    ShuttleTrackingModule,
)
from modules.shuttle_tracking.track_viterbi import FILLS, ViterbiConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track the shuttle with TrackNetV3")
    parser.add_argument("match_path", help="match path, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--tracknet", default=DEFAULT_TRACKNET,
                        help=f"TrackNet checkpoint (default {DEFAULT_TRACKNET})")
    parser.add_argument("--inpaintnet", default=DEFAULT_INPAINTNET,
                        help=f"InpaintNet checkpoint (default {DEFAULT_INPAINTNET})")
    parser.add_argument("--eval-mode", default="nonoverlap", choices=EVAL_MODES,
                        help="TrackNet temporal ensembling (default nonoverlap: one "
                             "prediction per frame; the others cost seq_len times more)")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="TrackNet inference batch size (default: sized from free "
                             "VRAM; a batch that still runs out is halved and retried)")
    parser.add_argument("--chunk-frames", type=int, default=MAX_CHUNK_FRAMES,
                        help=f"frames of one segment held in memory at once "
                             f"(default {MAX_CHUNK_FRAMES}, ~530 MB); lower it on a "
                             f"machine short of RAM")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="confidence for binarizing the heatmap (default 0.5)")
    parser.add_argument("--method", action="append", choices=SHUTTLE_METHODS, default=None,
                        help="tracker to run; repeatable (default: both)")
    parser.add_argument("--fill", default="linear", choices=sorted(FILLS),
                        help="gap-filling method for the viterbi tracker (default linear)")
    parser.add_argument("--only-heatmap", action="store_true",
                        help="stop after filling the heatmap cache")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="discard the cached heatmaps and recompute them")
    parser.add_argument("--device", default=None, help='force a device, e.g. "cpu"')
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = ShuttleTrackingConfig(
        tracknet_checkpoint=args.tracknet,
        inpaintnet_checkpoint=args.inpaintnet,
        eval_mode=args.eval_mode,
        batch_size=args.batch_size,
        chunk_frames=args.chunk_frames,
        threshold=args.threshold,
        viterbi=replace(ViterbiConfig(), fill=args.fill),
        methods=tuple(args.method) if args.method else SHUTTLE_METHODS,
        device=args.device,
        refresh_cache=args.refresh_cache,
    )

    bar = SmoothProgress("shuttle_tracking", total=1000)
    module = ShuttleTrackingModule(config=config)
    output = module.run(
        Path(args.match_path),
        on_progress=lambda f: bar.update(int(f * 1000), force=f >= 1.0),
        only_heatmap=args.only_heatmap,
    )
    print(f"done -> {output}")


if __name__ == "__main__":
    main()
