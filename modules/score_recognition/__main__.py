"""CLI entry point: ``python -m modules.score_recognition <project_path>``.

Runs the score_recognition stage on a match project, reading the segments
produced by match_segmentation and writing ``stages/score_recognition/scores.json``.
Requires a Gemini API key: set ``$GEMINI_API_KEY`` or put ``gemini_api_key`` in
``config.yaml`` at the repo root.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.common.progress import SmoothProgress
from modules.score_recognition.module import ScoreRecognitionModule
from modules.score_recognition.recognizer import ScoreRecognitionConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read badminton scoreboards per rally segment via Gemini",
    )
    parser.add_argument("project_path", help="match directory, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--rpm", type=float, default=8.0,
                        help="Max Gemini requests per minute, shared across workers (default 8)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Segments processed in parallel (default 2)")
    parser.add_argument("--resize", type=int, default=None,
                        help="Max image width sent to Gemini (default: source resolution)")
    parser.add_argument("--n-frames", type=int, default=30,
                        help="Frames sampled per segment (default 30)")
    parser.add_argument("--max-frames", type=int, default=120,
                        help="Cap on sampled frames per segment (default 120)")
    parser.add_argument("--sigma-clip-k", type=float, default=2.0,
                        help="Sigma multiplier for sigma_clip (default 2.0)")
    parser.add_argument("--sigma-clip-iter", type=int, default=3,
                        help="Iterations for sigma_clip (default 3)")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="Max retries on transient errors (default 3)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = ScoreRecognitionConfig(
        model=args.model,
        rpm=args.rpm,
        concurrency=max(1, args.concurrency),
        resize_width=args.resize,
        n_frames=args.n_frames,
        max_frames=args.max_frames,
        sigma_clip_k=args.sigma_clip_k,
        sigma_clip_iter=args.sigma_clip_iter,
        max_retries=args.max_retries,
    )

    bar = SmoothProgress("score_recognition", total=100)
    module = ScoreRecognitionModule(config=config)
    output = module.run(
        Path(args.project_path),
        on_progress=lambda ratio: bar.update(int(ratio * 100)),
    )
    bar.update(100, force=True)
    print(f"done -> {output}")


if __name__ == "__main__":
    main()
