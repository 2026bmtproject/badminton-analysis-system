"""CLI: python -m modules.player_identity matches/<match>."""
import argparse
from pathlib import Path

from modules.player_identity import PlayerIdentityModule


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map scoreboard rows to court sides with serve_vote_v1")
    parser.add_argument("match_path", type=Path)
    parser.add_argument("--no-dense", action="store_true",
                        help="ignore cache/dense_scan even when it is present")
    parser.add_argument("--no-visual", action="store_true",
                        help="leave sparse epochs unresolved instead of using HSV appearance")
    args = parser.parse_args()
    PlayerIdentityModule(
        use_dense=not args.no_dense,
        use_visual=not args.no_visual,
    ).run(args.match_path)


if __name__ == "__main__":
    main()
