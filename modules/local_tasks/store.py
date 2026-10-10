"""Private, atomic task records and bounded log reads."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(parents=True, exist_ok=True)

    def path(self, task_id: str) -> Path:
        if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
            raise ValueError("invalid task ID")
        return self.root / "tasks" / f"{task_id}.json"

    def read(self, task_id: str) -> dict[str, Any]:
        return json.loads(self.path(task_id).read_text(encoding="utf-8"))

    def save(self, record: dict[str, Any]) -> None:
        path = self.path(record["id"])
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def list(self) -> list[dict[str, Any]]:
        records = [json.loads(p.read_text(encoding="utf-8")) for p in (self.root / "tasks").glob("*.json")]
        return sorted(records, key=lambda item: item["createdAt"], reverse=True)

    def append_log(self, task_id: str, message: str) -> None:
        self.path(task_id)
        with (self.root / "logs" / f"{task_id}.log").open("a", encoding="utf-8") as output:
            output.write(f"{now()} {message.replace(chr(10), ' ')}\n")

    def logs(self, task_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        self.path(task_id)
        if offset < 0 or limit < 1 or limit > 200:
            raise ValueError("invalid log page")
        path = self.root / "logs" / f"{task_id}.log"
        if not path.exists():
            return {"lines": [], "nextOffset": offset}
        with path.open(encoding="utf-8") as source:
            lines = list(islice(source, offset, offset + limit))
        return {"lines": [line.rstrip("\n") for line in lines], "nextOffset": offset + len(lines)}
