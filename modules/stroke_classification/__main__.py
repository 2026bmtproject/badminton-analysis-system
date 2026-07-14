"""CLI entry point: ``python -m modules.stroke_classification <match_path>``.

Classifies every hit ``event_detection`` found, into
``stages/stroke_classification/strokes.json``.

    # the stage
    uv run python -m modules.stroke_classification matches/ASG_vs_AA_2020

    # with the per-hit detail CSV (窗口、前三名、p_top/p_bottom)
    uv run python -m modules.stroke_classification matches/ASG_vs_AA_2020 \
        --debug-csv strokes.csv

    # feed BST the conservative trajectory instead of the default inpaint one
    uv run python -m modules.stroke_classification matches/ASG_vs_AA_2020 \
        --shuttle-method viterbi
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.common.progress import SmoothProgress
from modules.contracts import SHUTTLE_METHODS
from modules.stroke_classification.config import StrokeClassificationConfig
from modules.stroke_classification.module import StrokeClassificationModule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify every hit in a match")
    parser.add_argument("match_path", help="match path, e.g. matches/ASG_vs_AA_2020")
    parser.add_argument("--shuttle-method", default="inpaint", choices=SHUTTLE_METHODS,
                        help="which trajectory BST reads (default inpaint, the one closest "
                             "to what it was trained on)")
    parser.add_argument("--bst", default=None,
                        help="BST checkpoint (default: models/bst_...merged.pt)")
    parser.add_argument("--batch-size", type=int, default=256,
                        help="inference batch size (default 256)")
    parser.add_argument("--topk", type=int, default=3,
                        help="how many classes the debug CSV shows per hit (default 3)")
    parser.add_argument("--debug-csv", default=None, metavar="FILE",
                        help="also write the per-hit detail CSV")
    parser.add_argument("--device", default=None, help='force a device, e.g. "cpu"')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = StrokeClassificationConfig(
        shuttle_method=args.shuttle_method,
        bst_checkpoint=args.bst,
        batch_size=args.batch_size,
        device=args.device,
        topk=args.topk,
    )

    bar = SmoothProgress("stroke_classification", total=1000)
    module = StrokeClassificationModule(config=config)
    output = module.run(
        Path(args.match_path),
        on_progress=lambda f: bar.update(int(f * 1000), force=f >= 1.0),
        debug_csv=args.debug_csv,
    )
    print(f"done -> {output}")


if __name__ == "__main__":
    main()
