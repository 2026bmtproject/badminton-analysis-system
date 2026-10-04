"""Selective plans built from the runner's registry and stale policy."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from modules.base import StageStatus, artifact_fingerprint, read_status
from modules.contracts import PIPELINE, artifact_path, ordering_dependencies, stage_path, topological_order
from modules.runner import available_modules, stale_inputs
from modules.review_export.contracts import ARTIFACT_MODELS, CommentaryArtifact

MATCH_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
GEMINI_STAGES = frozenset({"score_recognition", "commentary"})


def _valid_artifact(match: Path, name: str) -> bool:
    path = artifact_path(match, name)
    if not path.is_file():
        return False
    try:
        raw = path.read_bytes()
        model = CommentaryArtifact if name == "commentary" else ARTIFACT_MODELS.get(name)
        if model is not None:
            model.model_validate_json(raw)
        elif not isinstance(json.loads(raw).get(PIPELINE[name].record_key), list):
            return False
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def resolve_match(matches_root: Path, match_id: str) -> Path:
    if not isinstance(match_id, str) or not MATCH_ID.fullmatch(match_id):
        raise ValueError("invalid matchId")
    root = matches_root.resolve()
    match = (root / match_id).resolve()
    if match.parent != root or not match.is_dir():
        raise ValueError("matchId not found")
    return match


def build_plan(match: Path, requested: list[str], mode: str,
               registry: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_artifacts = registry is None
    modules = registry if registry is not None else available_modules()
    if mode not in ("continue", "rerun-selected"):
        raise ValueError("invalid mode")
    if not isinstance(requested, list) or not requested or any(
        not isinstance(name, str) or name not in modules for name in requested
    ) or len(requested) != len(set(requested)):
        raise ValueError("invalid stages")
    requested_set = set(requested)
    scope = set(requested)
    pending = list(requested)
    while pending:
        name = pending.pop()
        for dependency in modules[name].dependencies:
            if dependency not in modules:
                raise ValueError(f"unregistered dependency: {dependency}")
            if dependency not in scope:
                scope.add(dependency)
                pending.append(dependency)
    order = topological_order({name: [dep for dep in
        [*modules[name].dependencies, *modules[name].optional_dependencies] if dep in scope]
        for name in scope})
    stages: list[dict[str, Any]] = []
    will_run: set[str] = set()
    for name in order:
        status = read_status(stage_path(match, name))
        artifact_exists = artifact_path(match, name).is_file()
        stale = stale_inputs(match, modules[name]) if status and status.status == StageStatus.COMPLETED else None
        unknown = status is not None and status.status == StageStatus.COMPLETED and stale is None
        upstream = [dep for dep in ordering_dependencies(PIPELINE[name]) if dep in will_run]
        if mode == "rerun-selected" and name in requested_set:
            reason = "selected for rerun"
        elif upstream:
            reason = "upstream will change: " + ", ".join(upstream)
        elif status is None or status.status != StageStatus.COMPLETED or not artifact_exists:
            reason = "missing or unfinished"
        elif stale:
            reason = "stale: " + ", ".join(stale)
        elif validate_artifacts and not _valid_artifact(match, name):
            reason = "invalid artifact"
        else:
            reason = "unknown inputs; retaining completed result" if unknown else "completed and current"
        action = "skip" if reason.startswith(("unknown inputs", "completed and")) else "run"
        if action == "run":
            will_run.add(name)
        stages.append({"name": name, "action": action, "reason": reason,
                       "stale": stale or [], "unknown": unknown,
                       "status": status.status.value if status else "missing"})

    affected = set(will_run)
    changed = True
    while changed:
        before = len(affected)
        affected.update(name for name, module in modules.items()
                        if name not in affected and any(dep in affected for dep in
                            [*module.dependencies, *module.optional_dependencies]))
        changed = len(affected) != before
    outside = sorted(affected - scope)
    sources = {}
    for name in modules:
        status_path = stage_path(match, name) / "status.json"
        sources[name] = {"artifact": artifact_fingerprint(match, name),
                         "status": hashlib.sha256(status_path.read_bytes()).hexdigest()[:16]
                         if status_path.is_file() else None}
    video_folder = match / "input"
    sources["video"] = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                              for p in video_folder.iterdir() if p.is_file()) if video_folder.is_dir() else []
    plan = {"matchId": match.name, "mode": mode, "requestedStages": requested,
            "requiredStages": order, "stages": stages, "affectedOutsideScope": outside,
            "includesGemini": any(name in GEMINI_STAGES for name in will_run),
            "sources": sources}
    plan["planId"] = hashlib.sha256(json.dumps(plan, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()[:24]
    return plan
