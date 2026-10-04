"""Loopback HTTP facade for plans and persistent single-worker tasks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from modules.base import StageStatus, read_status
from modules.contracts import PIPELINE, artifact_path, stage_path
from modules.local_tasks.locking import WorkerLock
from modules.local_tasks.planning import build_plan, resolve_match
from modules.local_tasks.store import TaskStore, now
from modules.runner import available_modules, default_modules


class PlanChanged(ValueError):
    pass


class WorkerBusy(ValueError):
    pass


class TaskManager:
    def __init__(self, matches_root: Path, tasks_root: Path,
                 spawn=None):
        self.matches_root = matches_root.resolve()
        self.store = TaskStore(tasks_root)
        self.spawn = spawn or subprocess.Popen
        self.guard = threading.Lock()
        self.active_id: str | None = None
        for record in self.store.list():
            if record["status"] in ("queued", "running"):
                record.update(status="interrupted", finishedAt=now(),
                              error="service restarted; worker completion cannot be confirmed")
                if record.get("currentStage"):
                    record["stageStates"][record["currentStage"]]["status"] = "interrupted"
                self.store.save(record)

    def plan(self, request: dict) -> dict:
        match = resolve_match(self.matches_root, request.get("matchId"))
        return build_plan(match, request.get("stages"), request.get("mode"))

    def stages(self) -> list[dict]:
        modules = available_modules()
        return [{"name": name, "description": PIPELINE[name].description,
                 "dependencies": module.dependencies,
                 "optionalDependencies": module.optional_dependencies,
                 "usesGemini": name in ("score_recognition", "commentary")}
                for name, module in modules.items()]

    def matches(self) -> list[dict]:
        latest = {}
        for task in self.store.list():
            latest.setdefault(task["matchId"], task)
        if not self.matches_root.is_dir():
            return []
        rows = []
        for folder in sorted(self.matches_root.iterdir()):
            if not folder.is_dir():
                continue
            try:
                match = resolve_match(self.matches_root, folder.name)
            except ValueError:
                continue
            video = match / "input" / "match.mp4"
            if not video.is_file():
                continue
            completed = []
            for name in available_modules():
                try:
                    status = read_status(stage_path(match, name))
                    if status is not None and status.status == StageStatus.COMPLETED and artifact_path(match, name).is_file():
                        completed.append(name)
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
            rows.append({"id": folder.name, "completedStages": completed,
                         "analysisStatus": "completed" if set(default_modules()) <= set(completed)
                         else "partial" if completed else "unanalysed",
                         "hasSegments": "match_segmentation" in completed,
                         "latestTask": latest.get(folder.name)})
        return rows

    def start(self, request: dict) -> dict:
        with self.guard:
            if self.active_id is not None:
                raise WorkerBusy("an analysis task is already active")
            probe = WorkerLock(self.store.root / "worker.lock")
            if not probe.acquire():
                raise WorkerBusy("a worker from another service process is active")
            probe.release()
            plan = self.plan(request)
            if request.get("planId") != plan["planId"]:
                raise PlanChanged("source or plan changed; preview again before starting")
            if not any(row["action"] == "run" for row in plan["stages"]):
                raise ValueError("plan has no work to run")
            task_id = uuid.uuid4().hex
            record = {"id": task_id, "matchId": plan["matchId"],
                      "options": {"mode": plan["mode"], "stages": plan["requestedStages"]},
                      "plan": plan, "status": "queued", "stageStates": {
                          row["name"]: {"status": "queued" if row["action"] == "run" else "skipped",
                                        "progress": None, "reason": row["reason"]}
                          for row in plan["stages"]},
                      "currentStage": None, "createdAt": now(), "startedAt": None,
                      "finishedAt": None, "error": None, "exitCode": None}
            self.store.save(record)
            argv = [sys.executable, "-m", "modules.local_tasks.worker",
                    "--tasks-dir", str(self.store.root), "--matches-dir", str(self.matches_root),
                    "--task-id", task_id]
            try:
                process = self.spawn(argv, cwd=Path(__file__).resolve().parents[2],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=True)
            except OSError as error:
                record.update(status="failed", error=str(error), finishedAt=now(), exitCode=-1)
                self.store.save(record)
                raise
            self.active_id = task_id
            threading.Thread(target=self._monitor, args=(task_id, process), daemon=True).start()
            return record

    def _monitor(self, task_id, process) -> None:
        code = process.wait()
        with self.guard:
            record = self.store.read(task_id)
            if record["status"] in ("queued", "running"):
                record.update(status="failed" if code else "interrupted", exitCode=code,
                              finishedAt=now(), error=f"worker exited with code {code} without final state")
                self.store.save(record)
                self.store.append_log(task_id, record["error"])
            elif record.get("exitCode") is None:
                record["exitCode"] = code
                self.store.save(record)
            if self.active_id == task_id:
                self.active_id = None


def handler_for(manager: TaskManager, ui_origin: str, bind_host: str, bind_port: int):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code: int, value: object):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, mutation=False):
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in (f"{bind_host}:{bind_port}", f"localhost:{bind_port}"):
                self.respond(403, {"error": "invalid host"})
                return False
            if origin != ui_origin and (origin is not None or mutation):
                self.respond(403, {"error": "origin is not allowed"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/pipeline/stages":
                    return self.respond(200, {"stages": manager.stages()})
                if url.path == "/api/pipeline/matches":
                    return self.respond(200, {"matches": manager.matches()})
                if url.path == "/api/pipeline/tasks":
                    return self.respond(200, {"tasks": manager.store.list()})
                parts = url.path.strip("/").split("/")
                if len(parts) >= 4 and parts[:3] == ["api", "pipeline", "tasks"]:
                    task = manager.store.read(parts[3])
                    if len(parts) == 4:
                        return self.respond(200, task)
                    if len(parts) == 5 and parts[4] == "logs":
                        query = parse_qs(url.query)
                        return self.respond(200, manager.store.logs(parts[3],
                            int(query.get("offset", ["0"])[0]), int(query.get("limit", ["100"])[0])))
                self.respond(404, {"error": "not found"})
            except FileNotFoundError:
                self.respond(404, {"error": "task not found"})
            except (ValueError, OSError, KeyError) as error:
                self.respond(400, {"error": str(error)})

        def do_POST(self):
            if not self.allowed(mutation=True):
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 8192:
                    return self.respond(413, {"error": "invalid request size"})
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("JSON object required")
                if self.path == "/api/pipeline/plan":
                    return self.respond(200, manager.plan(request))
                if self.path == "/api/pipeline/tasks":
                    return self.respond(201, manager.start(request))
                self.respond(404, {"error": "not found"})
            except PlanChanged as error:
                self.respond(409, {"error": str(error)})
            except WorkerBusy as error:
                self.respond(409, {"error": str(error)})
            except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
                self.respond(400, {"error": str(error)})

        def log_message(self, format, *args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local pipeline task service")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ui-origin", default="http://127.0.0.1:5173")
    parser.add_argument("--matches-dir", type=Path, default=Path(os.environ.get(
        "BADMINTON_MATCHES_DIR", Path(__file__).resolve().parents[2] / "matches")))
    parser.add_argument("--tasks-dir", type=Path, default=Path(os.environ.get(
        "BADMINTON_TASKS_DIR", Path(__file__).resolve().parents[2] / ".local" / "pipeline-tasks")))
    args = parser.parse_args()
    service_lock = WorkerLock(args.tasks_dir.resolve() / "service.lock")
    if not service_lock.acquire():
        raise SystemExit("another pipeline task service is already using this task directory")
    manager = TaskManager(args.matches_dir, args.tasks_dir)
    server = ThreadingHTTPServer((args.host, args.port),
        handler_for(manager, args.ui_origin, args.host, args.port))
    print(f"Pipeline task service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service_lock.release()


if __name__ == "__main__":
    main()
