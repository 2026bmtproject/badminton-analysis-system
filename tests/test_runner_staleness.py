"""A completed stage is not the same as an up-to-date one.

The runner used to skip anything marked ``completed``, so re-cutting the segments left
every downstream artifact silently describing a match that no longer existed, and the
only way out was ``--force`` — which cost the whole pipeline. Now the inputs are
fingerprinted, and re-running a stale stage is cheap because the caches under it are
keyed per segment.
"""

from __future__ import annotations

from pathlib import Path

from modules.artifacts import write_artifact
from modules.base import (
    BaseModule,
    StageResult,
    StageState,
    StageStatus,
    read_status,
    write_status,
)
from modules.contracts import PIPELINE, artifact_path, stage_path
from modules.runner import run_pipeline, stale_inputs

SEGMENTS = [{"start_frame": 0, "end_frame": 99, "start_sec": 0.0,
             "end_sec": 4.0, "duration_sec": 4.0}]


def write_segments(match_path: Path, end_frame: int = 99) -> None:
    write_artifact(
        PIPELINE["match_segmentation"],
        [{**SEGMENTS[0], "end_frame": end_frame}],
        artifact_path(match_path, "match_segmentation"),
        extra={"fps": 25.0},
    )
    write_status(
        stage_path(match_path, "match_segmentation"),
        StageState(name="match_segmentation", status=StageStatus.COMPLETED),
    )


class FakeSegmentation(BaseModule):
    name = "match_segmentation"

    def check_ready(self, match_path) -> bool:
        return True

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path, *, on_progress=None, **kwargs) -> StageResult:
        return StageResult(self.get_output_path(match_path))


class CountingStage(BaseModule):
    """A downstream stage that records how many times it actually ran."""

    name = "shuttle_tracking"
    dependencies = ["match_segmentation"]

    def __init__(self) -> None:
        self.runs = 0

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _run(self, match_path, *, on_progress=None, **kwargs) -> StageResult:
        self.runs += 1
        output = self.get_output_path(match_path)
        write_artifact(PIPELINE["shuttle_tracking"], [], output)
        return StageResult(output)


def pipeline(match_path: Path, **kwargs) -> CountingStage:
    stage = CountingStage()
    run_pipeline(match_path, {"match_segmentation": FakeSegmentation(), stage.name: stage}, **kwargs)
    return stage


def test_a_stage_records_the_inputs_it_read(tmp_path):
    write_segments(tmp_path)
    pipeline(tmp_path)

    state = read_status(stage_path(tmp_path, "shuttle_tracking"))
    assert set(state.inputs) == {"match_segmentation"}
    assert state.inputs["match_segmentation"]


def test_an_unchanged_upstream_is_skipped(tmp_path):
    write_segments(tmp_path)
    stage = CountingStage()
    modules = {"match_segmentation": FakeSegmentation(), stage.name: stage}

    run_pipeline(tmp_path, modules)
    run_pipeline(tmp_path, modules)

    assert stage.runs == 1


def test_re_cutting_one_frame_makes_the_downstream_stage_stale(tmp_path, capsys):
    write_segments(tmp_path)
    stage = CountingStage()
    modules = {"match_segmentation": FakeSegmentation(), stage.name: stage}
    run_pipeline(tmp_path, modules)

    write_segments(tmp_path, end_frame=101)     # the two-frame edit
    run_pipeline(tmp_path, modules)

    assert stage.runs == 2
    assert "match_segmentation" in capsys.readouterr().out


def test_re_running_upstream_to_the_same_answer_is_not_a_change(tmp_path):
    """Fingerprints are content, not mtime — an identical rebuild must cost nothing."""
    write_segments(tmp_path)
    stage = CountingStage()
    modules = {"match_segmentation": FakeSegmentation(), stage.name: stage}
    run_pipeline(tmp_path, modules)

    write_segments(tmp_path)                    # same bytes, new mtime
    run_pipeline(tmp_path, modules)

    assert stage.runs == 1


def test_a_status_without_input_tracking_is_left_alone(tmp_path, capsys):
    """Not re-running matches somebody already paid GPU hours for, on no evidence."""
    write_segments(tmp_path)
    stage = CountingStage()
    run_pipeline(tmp_path, {"match_segmentation": FakeSegmentation(), stage.name: stage})

    state = read_status(stage_path(tmp_path, "shuttle_tracking"))
    state.inputs = None                          # a status.json from before the field
    write_status(stage_path(tmp_path, "shuttle_tracking"), state)

    run_pipeline(tmp_path, {"match_segmentation": FakeSegmentation(), stage.name: stage})
    assert stage.runs == 1
    assert "inputs not tracked" in capsys.readouterr().out


def test_strict_stale_rebuilds_an_untracked_status(tmp_path):
    write_segments(tmp_path)
    stage = CountingStage()
    modules = {"match_segmentation": FakeSegmentation(), stage.name: stage}
    run_pipeline(tmp_path, modules)

    state = read_status(stage_path(tmp_path, "shuttle_tracking"))
    state.inputs = None
    write_status(stage_path(tmp_path, "shuttle_tracking"), state)

    run_pipeline(tmp_path, modules, strict_stale=True)
    assert stage.runs == 2


def test_stale_inputs_reports_unknown_separately_from_unchanged(tmp_path):
    write_segments(tmp_path)
    stage = CountingStage()
    run_pipeline(tmp_path, {"match_segmentation": FakeSegmentation(), stage.name: stage})

    assert stale_inputs(tmp_path, stage) == []          # tracked and unchanged

    state = read_status(stage_path(tmp_path, "shuttle_tracking"))
    state.inputs = None
    write_status(stage_path(tmp_path, "shuttle_tracking"), state)

    assert stale_inputs(tmp_path, stage) is None        # cannot say — not the same thing


def test_staleness_chains_down_the_graph(tmp_path):
    """Each stage only fingerprints its *direct* inputs, so propagation has to be earned.

    Re-cutting the segments makes shuttle_tracking stale; re-running it changes
    shuttle.json, which is what makes event_detection stale in turn. Nothing declares
    the transitive edge — it falls out of each stage actually producing new output.
    """

    class Downstream(CountingStage):
        name = "event_detection"
        dependencies = ["shuttle_tracking"]

        def _run(self, match_path, *, on_progress=None, **kwargs) -> StageResult:
            self.runs += 1
            output = self.get_output_path(match_path)
            write_artifact(PIPELINE["event_detection"], [], output)
            return StageResult(output)

    class Varying(CountingStage):
        """Writes something different each run, as a real re-cut would."""

        def _run(self, match_path, *, on_progress=None, **kwargs) -> StageResult:
            self.runs += 1
            output = self.get_output_path(match_path)
            write_artifact(
                PIPELINE["shuttle_tracking"],
                [{"frame": self.runs, "segment_index": 0, "method": "inpaint",
                  "x": None, "y": None, "visible": False}],
                output,
            )
            return StageResult(output)

    write_segments(tmp_path)
    middle, last = Varying(), Downstream()
    modules = {
        "match_segmentation": FakeSegmentation(),
        middle.name: middle,
        last.name: last,
    }
    run_pipeline(tmp_path, modules)
    assert (middle.runs, last.runs) == (1, 1)

    write_segments(tmp_path, end_frame=101)
    run_pipeline(tmp_path, modules)

    assert (middle.runs, last.runs) == (2, 2), "the edit must reach the far end"


def test_a_disappearing_optional_input_counts_as_a_change(tmp_path):
    write_segments(tmp_path)
    stage = CountingStage()
    stage.optional_dependencies = ["score_recognition"]
    write_artifact(
        PIPELINE["score_recognition"], [], artifact_path(tmp_path, "score_recognition")
    )
    modules = {"match_segmentation": FakeSegmentation(), stage.name: stage}
    run_pipeline(tmp_path, modules)

    artifact_path(tmp_path, "score_recognition").unlink()
    assert stale_inputs(tmp_path, stage) == ["score_recognition"]
