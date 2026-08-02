"""Pipeline runner: order the registered modules and run them in sequence.

Behaviour (as required):
  * topologically sort modules by their ``dependencies``;
  * skip any stage already marked ``completed`` (unless ``--force``);
  * before running a stage, verify it is ready (deps done, inputs present);
  * stop at the first failure — later stages are not attempted.

**A completed stage is re-run when its inputs have moved under it.** ``status.json``
records a fingerprint of each dependency's artifact as it stood when the stage last
succeeded (see ``modules.base.current_inputs``); if one no longer matches, the stage is
*stale* and completing it once is not enough. This is only affordable because the caches
underneath are keyed per segment: re-cutting one rally makes every downstream stage
stale, and each of them then recomputes that one rally rather than the match.

A ``status.json`` written before that field existed says nothing about its inputs. That
is reported as unknown and left alone — the alternative is re-running matches somebody
has already paid GPU hours and Gemini calls for, on no evidence. Pass ``--strict-stale``
to treat unknown as stale instead.

Usage::

    uv run python -m modules.runner matches/MK_vs_CT_2019
    uv run python -m modules.runner matches/MK_vs_CT_2019 --force
"""

from __future__ import annotations

import argparse
from pathlib import Path

from modules.base import BaseModule, StageStatus, current_inputs, read_status
from modules.contracts import stage_path, topological_order
from modules.court_detection import CourtDetectionModule
from modules.event_detection import EventDetectionModule
from modules.match_segmentation import MatchSegmentationModule
from modules.pose import PoseModule
from modules.score_recognition import ScoreRecognitionModule
from modules.shuttle_tracking import ShuttleTrackingModule
from modules.stroke_classification import StrokeClassificationModule


def available_modules() -> dict[str, BaseModule]:
    """The runnable stages, keyed by name.

    Register a module here once its stage is implemented; the runner picks up
    dependencies and ordering automatically from each module's attributes.
    """
    modules: list[BaseModule] = [
        MatchSegmentationModule(),
        ScoreRecognitionModule(),
        CourtDetectionModule(),
        ShuttleTrackingModule(),
        PoseModule(),
        EventDetectionModule(),
        StrokeClassificationModule(),
    ]
    return {m.name: m for m in modules}


def _status_of(match_path: Path, name: str) -> StageStatus | None:
    state = read_status(stage_path(match_path, name))
    return state.status if state else None


def stale_inputs(match_path: Path, module: BaseModule) -> list[str] | None:
    """Dependencies whose artifact has changed since ``module`` last succeeded.

    ``None`` means the question cannot be answered: the stage predates input tracking.
    An empty list means everything it read is still what it read.
    """
    state = read_status(stage_path(match_path, module.name))
    if state is None or state.inputs is None:
        return None
    current = current_inputs(
        match_path, [*module.dependencies, *module.optional_dependencies]
    )
    names = set(state.inputs) | set(current)
    return sorted(n for n in names if state.inputs.get(n) != current.get(n))


def run_pipeline(
    match_path: str | Path,
    modules: dict[str, BaseModule] | None = None,
    force: bool = False,
    strict_stale: bool = False,
) -> bool:
    """Run every registered stage in dependency order.

    Returns True if the whole pipeline is complete, False if it stopped early
    (a stage was not ready, or a stage failed).
    """
    match_path = Path(match_path)
    if not match_path.is_dir():
        raise FileNotFoundError(f"match path not found: {match_path}")

    modules = available_modules() if modules is None else modules
    # Optional dependencies order the run without gating it: a stage that reads
    # score_recognition's output when it exists must still be scheduled after it, or a
    # full-pipeline run would produce that output one stage too late to ever be read.
    order = topological_order(
        {name: [*m.dependencies, *m.optional_dependencies] for name, m in modules.items()}
    )

    print(f"pipeline: {match_path}")
    print(f"stages ({len(order)}): {' -> '.join(order)}\n")

    for name in order:
        module = modules[name]

        if not force and _status_of(match_path, name) == StageStatus.COMPLETED:
            stale = stale_inputs(match_path, module)
            if stale is None:
                if not strict_stale:
                    # Plain ASCII: a legacy-console code page mangles anything else, and
                    # this line is how a user finds out why nothing rebuilt.
                    print(f"[skip] {name}: already completed (inputs not tracked - run "
                          f"with --strict-stale to rebuild anyway)")
                    continue
                reason = "its inputs were never recorded"
            elif not stale:
                print(f"[skip] {name}: already completed")
                continue
            else:
                reason = f"{', '.join(stale)} changed since it last ran"
            print(f"[stale] {name}: {reason}")

        if not module.check_ready(match_path):
            print(f"[stop] {name}: not ready (missing input or unfinished dependency)")
            return False

        print(f"[run ] {name} ...")
        try:
            output = module.run(match_path)
        except Exception as e:  # a stage failed -> stop the pipeline
            print(f"[fail] {name}: {e}")
            return False
        print(f"[done] {name} -> {output}\n")

    print("pipeline complete.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the analysis pipeline for one match.")
    parser.add_argument("match_path", help="match path, e.g. matches/MK_vs_CT_2019")
    parser.add_argument("--force", action="store_true", help="re-run stages even if completed")
    parser.add_argument("--strict-stale", action="store_true",
                        help="also re-run completed stages whose status.json predates "
                             "input tracking, instead of leaving them alone")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ok = run_pipeline(args.match_path, force=args.force, strict_stale=args.strict_stale)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
