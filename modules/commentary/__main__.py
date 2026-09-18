"""CLI: python -m modules.commentary matches/<match>."""

import argparse

from modules.commentary import CommentaryModule


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate grounded match commentary")
    parser.add_argument("match_path")
    args = parser.parse_args()
    CommentaryModule().run(args.match_path)


if __name__ == "__main__":
    main()
