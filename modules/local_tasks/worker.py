"""One worker subprocess; modules retain ownership of status and progress."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from modules.base import StageStatus, read_status
from modules.contracts import stage_path
from modules.local_tasks.locking import WorkerLock
from modules.local_tasks.store import TaskStore, now
from modules.runner import _reject_gapped_input_video, available_modules


def _safe_error(error: BaseException) -> str:
    result = str(error)
    secrets = []
    for key, value in os.environ.items():
        if value and len(value) >= 8 and any(word in key.upper() for word in
            ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            secrets.append(value)
    try:
        from modules.common.config import load_config
        configured = load_config().get("gemini_api_key")
        if isinstance(configured, str) and configured:
            secrets.append(configured)
    except Exception:
        pass
    for secret in secrets:
        result = result.replace(secret, "[redacted]")
        result = result.replace(quote(secret, safe=""), "[redacted]")
    return result[:1000]


def execute_segment_commentary(store: TaskStore, task_id: str, match: Path,
                               generate=None) -> bool:
    """Runs on-demand commentary for the task's one segment; the commentary stage status is untouched."""
    if generate is None:
        from modules.commentary import generate_commentary_segments as generate
    record = store.read(task_id)
    if record["status"] != "queued":
        return False
    segment = record["segmentIndex"]
    stage = record["stageStates"]["commentary"]
    record.update(status="running", startedAt=now(), currentStage="commentary")
    stage.update(status="running", progress=None, startedAt=now())
    store.save(record)
    store.append_log(task_id, f"commentary: segment {segment} started")
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            old_stdout, old_stderr = sys.stdout, sys.stderr
            try:
                sys.stdout = sink
                sys.stderr = sink
                result = generate(match, [segment])
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
        if store.read(task_id)["status"] == "interrupted":
            return False
        if result.unsupported_segments:
            raise RuntimeError(f"segment {segment} is unsupported: "
                               f"{result.unsupported_segments[0]['reason']}")
        stage.update(status="succeeded", progress=1.0, finishedAt=now())
        record.update(status="succeeded", currentStage=None, finishedAt=now(), exitCode=0)
        store.save(record)
        store.append_log(task_id, f"commentary: segment {segment} succeeded")
        return True
    except BaseException as error:
        message = _safe_error(error)
        stage.update(status="failed", finishedAt=now(), error=message)
        if store.read(task_id)["status"] != "interrupted":
            record.update(status="failed", finishedAt=now(), error=message, exitCode=1)
            store.save(record)
        store.append_log(task_id, f"Commentary failed: {message}")
        return False


def execute(store: TaskStore, task_id: str, match: Path,
            modules: dict[str, Any] | None = None) -> bool:
    production_registry = modules is None
    modules = modules if modules is not None else available_modules()
    record = store.read(task_id)
    if record["status"] != "queued":
        return False
    if record.get("segmentIndex") is not None:
        return execute_segment_commentary(store, task_id, match)
    from modules.local_tasks.planning import build_plan
    current_plan = build_plan(match, record["options"]["stages"], record["options"]["mode"],
                              None if production_registry else modules)
    if current_plan["planId"] != record["plan"]["planId"]:
        record.update(status="failed", finishedAt=now(), exitCode=3,
                      error="source changed after task started; preview again")
        store.save(record)
        store.append_log(task_id, record["error"])
        return False
    record["status"] = "running"
    record["startedAt"] = now()
    store.save(record)
    store.append_log(task_id, "Analysis started")
    current = None
    try:
        _reject_gapped_input_video(match)
        for item in record["plan"]["stages"]:
            if item["action"] == "skip":
                continue
            if store.read(task_id)["status"] == "interrupted":
                return False
            current = item["name"]
            stage = record["stageStates"][current]
            stage.update(status="running", progress=None, startedAt=now())
            record["currentStage"] = current
            store.save(record)
            store.append_log(task_id, f"{current}: started")
            last_progress_at = 0.0
            progress_guard = threading.Lock()

            def progress(value: float) -> None:
                nonlocal last_progress_at
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    return
                with progress_guard:
                    if store.read(task_id)["status"] == "interrupted":
                        return
                    stamp = time.monotonic()
                    if value < 1 and stamp - last_progress_at < .5:
                        return
                    last_progress_at = stamp
                    record["stageStates"][current]["progress"] = round(float(value), 4)
                    store.save(record)

            # Model terminal output is intentionally not captured: it can contain
            # configuration or arbitrary upstream text. Structured log is private.
            with open(os.devnull, "w", encoding="utf-8") as sink:
                old_stdout, old_stderr = sys.stdout, sys.stderr
                try:
                    sys.stdout = sink
                    sys.stderr = sink
                    modules[current].run(match, on_progress=progress)
                finally:
                    sys.stdout, sys.stderr = old_stdout, old_stderr
            result = read_status(stage_path(match, current))
            if store.read(task_id)["status"] == "interrupted":
                return False
            if result is None or result.status != StageStatus.COMPLETED:
                raise RuntimeError(f"{current} did not complete")
            stage.update(status="succeeded", progress=1.0, finishedAt=now())
            store.save(record)
            store.append_log(task_id, f"{current}: succeeded")
        if store.read(task_id)["status"] == "interrupted":
            return False
        record.update(status="succeeded", currentStage=None, finishedAt=now(), exitCode=0)
        store.save(record)
        store.append_log(task_id, "Analysis succeeded")
        return True
    except BaseException as error:
        message = _safe_error(error)
        if current:
            record["stageStates"][current].update(status="failed", finishedAt=now(), error=message)
        if store.read(task_id)["status"] != "interrupted":
            record.update(status="failed", finishedAt=now(), error=message, exitCode=1)
            store.save(record)
        store.append_log(task_id, f"Analysis failed: {message}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-dir", type=Path, required=True)
    parser.add_argument("--matches-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    store = TaskStore(args.tasks_dir)
    record = store.read(args.task_id)
    from modules.local_tasks.planning import resolve_match
    match = resolve_match(args.matches_dir, record["matchId"])
    lock = WorkerLock(store.root / "worker.lock")
    if not lock.acquire():
        record.update(status="failed", finishedAt=now(), error="another worker is active", exitCode=2)
        store.save(record)
        raise SystemExit(2)
    try:
        raise SystemExit(0 if execute(store, args.task_id, match) else 1)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
