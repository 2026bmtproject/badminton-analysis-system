from __future__ import annotations

import http.client
import json
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from modules.base import BaseModule, StageResult, StageState, StageStatus, current_inputs, write_status
from modules.contracts import PIPELINE, artifact_path, stage_path
from modules.local_tasks.planning import build_plan, resolve_match
from modules.local_tasks.service import PlanChanged, TaskManager, WorkerBusy, handler_for
from modules.local_tasks.store import TaskStore
from modules.local_tasks.worker import _safe_error, execute


def match_fixture(tmp_path: Path) -> Path:
    match = tmp_path / "matches" / "Sample"
    (match / "input").mkdir(parents=True)
    (match / "input" / "match.mp4").write_bytes(b"fake-video")
    return match


class FakeModule(BaseModule):
    def __init__(self, name: str, *, fail=False, report=True):
        spec = PIPELINE[name]
        self.name = name
        self.dependencies = spec.dependencies
        self.optional_dependencies = spec.optional_dependencies
        self.fail = fail
        self.report = report

    def _run(self, match_path, *, on_progress=None, **kwargs):
        if self.fail:
            raise RuntimeError("fake stage failed")
        if self.report and on_progress:
            on_progress(.4)
        path = artifact_path(match_path, self.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({PIPELINE[self.name].record_key: []}))
        return StageResult(path)


def fake_registry(**flags):
    return {name: FakeModule(name, **flags.get(name, {})) for name in PIPELINE}


def completed(match: Path, name: str, *, unknown=False):
    output = artifact_path(match, name)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({PIPELINE[name].record_key: []}))
    dependencies = [*PIPELINE[name].dependencies, *PIPELINE[name].optional_dependencies]
    write_status(stage_path(match, name), StageState(name=name, status=StageStatus.COMPLETED,
        inputs=None if unknown else current_inputs(match, dependencies)))


def actions(plan):
    return {item["name"]: item["action"] for item in plan["stages"]}


def test_plan_closes_hard_dependencies_without_optional_gemini(tmp_path):
    match = match_fixture(tmp_path)
    plan = build_plan(match, ["event_detection"], "continue", fake_registry())
    assert {"match_segmentation", "court_detection", "pose", "shuttle_tracking", "event_detection"} <= set(plan["requiredStages"])
    assert "score_recognition" not in plan["requiredStages"]
    assert "commentary" not in plan["requiredStages"]
    assert plan["includesGemini"] is False
    assert "stroke_classification" in plan["affectedOutsideScope"]


def test_completed_stale_unknown_and_rerun_scope(tmp_path):
    match = match_fixture(tmp_path)
    completed(match, "match_segmentation")
    completed(match, "audio_highlight", unknown=True)
    unknown = build_plan(match, ["audio_highlight"], "continue", fake_registry())
    assert actions(unknown) == {"match_segmentation": "skip", "audio_highlight": "skip"}
    assert next(row for row in unknown["stages"] if row["name"] == "audio_highlight")["unknown"]
    rerun = build_plan(match, ["match_segmentation"], "rerun-selected", fake_registry())
    assert actions(rerun)["match_segmentation"] == "run"
    assert "commentary" in rerun["affectedOutsideScope"]
    assert rerun["includesGemini"] is False
    completed(match, "audio_highlight")
    artifact_path(match, "match_segmentation").write_text('{"segments": [1]}')
    stale = build_plan(match, ["audio_highlight"], "continue", fake_registry())
    assert actions(stale)["audio_highlight"] == "run"
    assert next(row for row in stale["stages"] if row["name"] == "audio_highlight")["stale"] == ["match_segmentation"]


def test_selected_rerun_propagates_inside_scope_and_gemini_is_explicit(tmp_path):
    match = match_fixture(tmp_path)
    for name in ("match_segmentation", "audio_highlight", "highlight_ranking"):
        completed(match, name)
    plan = build_plan(match, ["audio_highlight", "highlight_ranking"], "rerun-selected", fake_registry())
    assert actions(plan) == {"match_segmentation": "skip", "audio_highlight": "run", "highlight_ranking": "run"}
    assert plan["includesGemini"] is False
    identity = build_plan(match, ["player_identity"], "continue", fake_registry())
    assert identity["includesGemini"] is True  # score_recognition is a hard dependency
    assert "commentary" not in identity["requiredStages"]


def test_continue_repairs_structurally_invalid_completed_artifact(tmp_path):
    match = match_fixture(tmp_path)
    completed(match, "match_segmentation")
    assert actions(build_plan(match, ["match_segmentation"], "continue"))["match_segmentation"] == "run"
    artifact_path(match, "match_segmentation").write_text(json.dumps({
        "fps": 25, "segments": [{"start_frame": 0, "end_frame": 24,
            "start_sec": 0, "end_sec": .96, "duration_sec": .96}],
    }))
    assert actions(build_plan(match, ["match_segmentation"], "continue"))["match_segmentation"] == "skip"


def test_match_and_stage_validation(tmp_path):
    match = match_fixture(tmp_path)
    for value in ("../Sample", "C:\\secret", "other/path", ".."):
        with pytest.raises(ValueError):
            resolve_match(match.parent, value)
    with pytest.raises(ValueError, match="invalid stages"):
        build_plan(match, ["os.system"], "continue", fake_registry())
    with pytest.raises(ValueError, match="invalid mode"):
        build_plan(match, ["match_segmentation"], "force-all", fake_registry())


def test_match_symlink_cannot_escape_matches_root(tmp_path):
    match = match_fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (match.parent / "Link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege is unavailable")
    with pytest.raises(ValueError):
        resolve_match(match.parent, "Link")


class DeferredProcess:
    def __init__(self):
        self.done = threading.Event()
        self.code = 1

    def wait(self):
        assert self.done.wait(3)
        return self.code


def test_single_worker_plan_revalidation_and_restart(tmp_path):
    match = match_fixture(tmp_path)
    process = DeferredProcess()
    launched = []
    def spawn(argv, **options):
        launched.append((argv, options))
        return process
    manager = TaskManager(match.parent, tmp_path / "tasks", spawn=spawn)
    request = {"matchId": "Sample", "stages": ["match_segmentation"], "mode": "continue"}
    plan = manager.plan(request)
    with pytest.raises(PlanChanged):
        manager.start({**request, "planId": "old"})
    task = manager.start({**request, "planId": plan["planId"]})
    assert task["status"] == "queued"
    assert launched[0][0][1:3] == ["-m", "modules.local_tasks.worker"]
    assert launched[0][1]["stdin"] is not None
    with pytest.raises(WorkerBusy):
        manager.start({**request, "planId": plan["planId"]})
    restored = TaskManager(match.parent, tmp_path / "tasks", spawn=lambda *a, **kw: process)
    assert restored.store.read(task["id"])["status"] == "interrupted"
    process.done.set()


def test_start_rejects_source_changed_since_preview(tmp_path):
    match = match_fixture(tmp_path)
    manager = TaskManager(match.parent, tmp_path / "tasks", spawn=lambda *a, **kw: DeferredProcess())
    request = {"matchId": "Sample", "stages": ["match_segmentation"], "mode": "continue"}
    plan = manager.plan(request)
    (match / "input" / "match.mp4").write_bytes(b"changed")
    with pytest.raises(PlanChanged):
        manager.start({**request, "planId": plan["planId"]})


def test_worker_exit_is_recorded_and_retry_has_new_id(tmp_path):
    match = match_fixture(tmp_path)
    processes = []
    def spawn(*args, **kwargs):
        process = DeferredProcess()
        processes.append(process)
        return process
    manager = TaskManager(match.parent, tmp_path / "tasks", spawn=spawn)
    request = {"matchId": "Sample", "stages": ["match_segmentation"], "mode": "continue"}
    plan = manager.plan(request)
    first = manager.start({**request, "planId": plan["planId"]})
    processes[0].done.set()
    deadline = time.monotonic() + 2
    while manager.active_id is not None and time.monotonic() < deadline:
        time.sleep(.01)
    failed = manager.store.read(first["id"])
    assert failed["status"] == "failed"
    assert failed["exitCode"] == 1
    assert "worker exited" in failed["error"]
    second = manager.start({**request, "planId": plan["planId"]})
    assert second["id"] != first["id"]
    processes[1].done.set()


def queued_task(store: TaskStore, match: Path, stages: list[str], registry):
    plan = build_plan(match, stages, "continue", registry)
    task_id = uuid.uuid4().hex
    record = {"id": task_id, "matchId": match.name,
              "options": {"stages": stages, "mode": "continue"}, "plan": plan,
              "status": "queued", "currentStage": None, "startedAt": None,
              "finishedAt": None, "error": None, "exitCode": None,
              "stageStates": {row["name"]: {"status": "queued", "progress": None}
                              for row in plan["stages"]}}
    store.save(record)
    return task_id


def test_worker_success_failure_and_no_progress(tmp_path, monkeypatch):
    match = match_fixture(tmp_path)
    store = TaskStore(tmp_path / "tasks")
    monkeypatch.setattr("modules.local_tasks.worker._reject_gapped_input_video", lambda _: None)
    modules = fake_registry(match_segmentation={"report": False})
    task_id = queued_task(store, match, ["match_segmentation"], modules)
    class InspectNoProgress(FakeModule):
        def _run(self, match_path, *, on_progress=None, **kwargs):
            assert store.read(task_id)["stageStates"]["match_segmentation"]["progress"] is None
            return super()._run(match_path, on_progress=on_progress, **kwargs)
    modules["match_segmentation"] = InspectNoProgress("match_segmentation", report=False)
    assert execute(store, task_id, match, modules)
    result = store.read(task_id)
    assert result["status"] == "succeeded"
    assert result["stageStates"]["match_segmentation"]["progress"] == 1.0
    assert store.logs(task_id, 0, 1)["nextOffset"] == 1
    assert len(store.logs(task_id, 1, 1)["lines"]) == 1
    modules = fake_registry(audio_highlight={"fail": True})
    task_id = queued_task(store, match, ["audio_highlight"], modules)
    assert not execute(store, task_id, match, modules)
    result = store.read(task_id)
    assert result["status"] == "failed"
    assert "fake stage failed" in result["error"]
    assert result["exitCode"] == 1


def test_interrupted_worker_keeps_interrupted_task_state(tmp_path, monkeypatch):
    match = match_fixture(tmp_path)
    store = TaskStore(tmp_path / "tasks")
    monkeypatch.setattr("modules.local_tasks.worker._reject_gapped_input_video", lambda _: None)
    modules = fake_registry()
    task_id = queued_task(store, match, ["match_segmentation"], modules)
    class Interrupted(FakeModule):
        def _run(self, match_path, *, on_progress=None, **kwargs):
            task = store.read(task_id)
            task["status"] = "interrupted"
            store.save(task)
            return super()._run(match_path, on_progress=on_progress, **kwargs)
    modules["match_segmentation"] = Interrupted("match_segmentation")
    assert not execute(store, task_id, match, modules)
    assert store.read(task_id)["status"] == "interrupted"


def test_worker_error_redacts_environment_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-secret-value")
    assert "fake-secret-value" not in _safe_error(RuntimeError("failed with fake-secret-value"))


def test_http_origin_policy_and_reconnect(tmp_path):
    match = match_fixture(tmp_path)
    process = DeferredProcess()
    manager = TaskManager(match.parent, tmp_path / "tasks", spawn=lambda *a, **kw: process)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(manager,
        "http://127.0.0.1:5173", "127.0.0.1", 0))
    # Bind port is set after ephemeral allocation for the Host check.
    server.RequestHandlerClass = handler_for(manager, "http://127.0.0.1:5173",
        "127.0.0.1", server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def call(method, path, body=None, origin=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            conn.request(method, path, json.dumps(body) if body else None, headers)
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result

        assert call("GET", "/api/pipeline/matches")[1]["matches"][0]["hasSegments"] is False
        request = {"matchId": "Sample", "stages": ["match_segmentation"], "mode": "continue"}
        assert call("POST", "/api/pipeline/plan", request, "http://evil.example")[0] == 403
        assert call("POST", "/api/pipeline/plan", {**request, "matchId": "../Sample"},
                    "http://127.0.0.1:5173")[0] == 400
        assert call("POST", "/api/pipeline/plan", {**request, "stages": ["os.system"]},
                    "http://127.0.0.1:5173")[0] == 400
        status, plan = call("POST", "/api/pipeline/plan", request, "http://127.0.0.1:5173")
        assert status == 200
        assert call("POST", "/api/pipeline/tasks", {**request, "planId": "old"},
                    "http://127.0.0.1:5173")[0] == 409
        status, task = call("POST", "/api/pipeline/tasks", {**request, "planId": plan["planId"]},
                            "http://127.0.0.1:5173")
        assert status == 201
        assert call("GET", f"/api/pipeline/tasks/{task['id']}")[1]["id"] == task["id"]
        assert call("GET", f"/api/pipeline/tasks/{task['id']}/logs?offset=0&limit=10")[0] == 200
    finally:
        process.done.set()
        server.shutdown()
        server.server_close()
