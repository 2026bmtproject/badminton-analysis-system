from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class StageStatus(str, Enum):
    """Execution status of a single module (stage).

    Subclassing str lets it serialize straight to a JSON string and
    compare against plain strings.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


STATUS_FILENAME = "status.json"


@dataclass
class StageState:
    """Contents of status.json, one per stage.

    Stored at stages/{name}/status.json.
    Paths are always saved as strings relative to match_path for
    portability; resolve them back to absolute paths when reading.
    """

    name: str
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None      # ISO 8601, when execution started
    finished_at: str | None = None     # ISO 8601, when it ended (success or failure)
    output_path: str | None = None     # main output file, relative to match_path
    error: str | None = None           # error message when status == FAILED
    updated_at: str | None = None      # when this file was last written

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StageState":
        return cls(
            name=d["name"],
            status=StageStatus(d.get("status", StageStatus.PENDING.value)),
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            output_path=d.get("output_path"),
            error=d.get("error"),
            updated_at=d.get("updated_at"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_completed(match_path: Path, name: str) -> bool:
    """True if stage ``name`` under ``match_path`` finished successfully."""
    from modules.contracts import stage_path  # local import avoids a cycle

    state = read_status(stage_path(match_path, name))
    return state is not None and state.status == StageStatus.COMPLETED


def read_status(stage_path: Path) -> StageState | None:
    """Read stage_path/status.json; return None if it does not exist."""
    path = Path(stage_path) / STATUS_FILENAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return StageState.from_dict(json.load(f))


def write_status(stage_path: Path, state: StageState) -> None:
    """Write state into stage_path/status.json, refreshing updated_at."""
    state.updated_at = _now_iso()
    path = Path(stage_path) / STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)


class BaseModule:
    name: str
    dependencies: list[str] = []  # which modules must finish first
    #: Stages this one reads *if they have run*, and works without otherwise. They order
    #: the pipeline (see modules.contracts.ordering_dependencies) but never gate it.
    optional_dependencies: list[str] = []

    def check_ready(self, match_path) -> bool:
        """Return True only when every dependency's status is completed.

        Default gate for a stage whose only precondition is that its upstream
        stages finished. Stages with extra preconditions (e.g. the first stage
        needing a raw video) override this. ``optional_dependencies`` are deliberately
        not checked — an absent one is a normal, supported way to run.
        """
        return all(stage_completed(Path(match_path), d) for d in self.dependencies)
    def run(self, match_path, on_progress=None):
        """Run processing, write results to stages/{name}/, update status.json."""
        raise NotImplementedError

    def get_output_path(self, match_path) -> Path:
        """Return the path to the result file."""
        raise NotImplementedError