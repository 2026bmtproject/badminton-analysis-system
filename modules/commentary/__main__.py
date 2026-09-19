"""Cost-safe CLI for selected or explicit full-match commentary."""

import argparse

from modules.commentary import CommentaryModule
from modules.commentary.module import CommentaryProgress, CommentarySelectionError


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate grounded match commentary")
    parser.add_argument("match_path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--segment", type=int, action="append", metavar="N",
        help="generate one segment; repeat to select more than one",
    )
    mode.add_argument(
        "--all", action="store_true",
        help="explicitly generate canonical full-match commentary",
    )
    args = parser.parse_args(argv)
    if not args.segment and not args.all:
        parser.error(
            "Choose at least one --segment N, or use --all for full-match commentary."
        )
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    module = CommentaryModule()
    if args.all:
        module.run(args.match_path)
        return
    try:
        result = module.generate_segments(
            args.match_path,
            args.segment,
            progress=CommentaryProgress(full_match=False),
        )
    except CommentarySelectionError as exc:
        raise SystemExit(str(exc)) from exc
    if result.unsupported_segments:
        details = ", ".join(
            f"{item['segment_index']} ({item['reason']})"
            for item in result.unsupported_segments
        )
        raise SystemExit(f"Unsupported commentary segments: {details}")


if __name__ == "__main__":
    main()
