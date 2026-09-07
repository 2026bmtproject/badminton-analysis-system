"""CLI: python -m modules.highlight_ranking matches/<match>."""
import argparse
from pathlib import Path

from modules.highlight_ranking import HighlightRankingModule


def main() -> None:
    parser = argparse.ArgumentParser(description="Score every audio signal with audio_additive_v1")
    parser.add_argument("match_path", type=Path)
    args = parser.parse_args()
    HighlightRankingModule().run(args.match_path)


if __name__ == "__main__":
    main()
