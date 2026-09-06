"""CLI: python -m modules.audio_highlight matches/<match>."""
import argparse
from pathlib import Path

from modules.audio_highlight import AudioHighlightModule


def main() -> None:
    parser = argparse.ArgumentParser(description="Produce frozen per-segment crowd-cheer measurements")
    parser.add_argument("match_path", type=Path)
    args = parser.parse_args()
    AudioHighlightModule().run(args.match_path)


if __name__ == "__main__":
    main()
