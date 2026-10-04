"""CLI for exporting one match without running any analysis stage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from modules.review_export import ExportOptions, export_review


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export validated artifacts for the Vue Review workspace")
    parser.add_argument("match_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title")
    parser.add_argument("--player-a")
    parser.add_argument("--player-b")
    parser.add_argument("--video-url")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--scenario")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    players = ({"a": args.player_a, "b": args.player_b}
               if args.player_a is not None and args.player_b is not None else None)
    result = export_review(args.match_path, ExportOptions(title=args.title, players=players,
        video_url=args.video_url, duration=args.duration, scenario=args.scenario))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
