"""Pipeline runner: order the registered modules and run them in sequence.

Behaviour (as required):
  * topologically sort modules by their ``dependencies``;
  * skip any stage already marked ``completed`` (unless ``--force``);
  * before running a stage, verify it is ready (deps done, inputs present);
  * stop at the first failure — later stages are not attempted.

Usage::

    uv run python -m modules.runner matches/MK_vs_CT_2019
    uv run python -m modules.runner matches/MK_vs_CT_2019 --force
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.base import BaseModule, StageStatus, read_status
from modules.contracts import stage_dir, topological_order
from modules.match_segmentation import MatchSegmentationModule
from modules.score_recognition import ScoreRecognitionModule


def available_modules() -> dict[str, BaseModule]:
    """The runnable stages, keyed by name.

    Register a module here once its stage is implemented; the runner picks up
    dependencies and ordering automatically from each module's attributes.
    """
    modules: list[BaseModule] = [
        MatchSegmentationModule(),
        ScoreRecognitionModule(),
    ]
    return {m.name: m for m in modules}


def _status_of(project_path: Path, name: str) -> StageStatus | None:
    state = read_status(stage_dir(project_path, name))
    return state.status if state else None


def run_pipeline(
    project_path: str | Path,
    modules: dict[str, BaseModule] | None = None,
    force: bool = False,
) -> bool:
    """Run every registered stage in dependency order.

    Returns True if the whole pipeline is complete, False if it stopped early
    (a stage was not ready, or a stage failed).
    """
    project_path = Path(project_path)
    if not project_path.is_dir():
        raise FileNotFoundError(f"project path not found: {project_path}")

    modules = available_modules() if modules is None else modules
    order = topological_order({name: m.dependencies for name, m in modules.items()})

    print(f"pipeline: {project_path}")
    print(f"stages ({len(order)}): {' -> '.join(order)}\n")

    for name in order:
        module = modules[name]

        if not force and _status_of(project_path, name) == StageStatus.COMPLETED:
            print(f"[skip] {name}: already completed")
            continue

        if not module.check_ready(project_path):
            print(f"[stop] {name}: not ready (missing input or unfinished dependency)")
            return False

        print(f"[run ] {name} ...")
        try:
            output = module.run(project_path)
        except Exception as e:  # a stage failed -> stop the pipeline
            print(f"[fail] {name}: {e}")
            return False
        print(f"[done] {name} -> {output}\n")

    print("pipeline complete.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the analysis pipeline for one match.")
    parser.add_argument("project_path", help="match directory, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--force", action="store_true", help="re-run stages even if completed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ok = run_pipeline(args.project_path, force=args.force)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
