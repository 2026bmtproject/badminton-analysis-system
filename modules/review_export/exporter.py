"""Read pipeline artifacts once and export a stable Review model.

No stage is run from here.  Optional failures are represented in ``states`` and do
not prevent the usable subset of a match from being reviewed.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from modules.base import StageStatus, read_status
from modules.common.ffmpeg_utils import get_video_duration
from modules.contracts import PIPELINE, artifact_path, resolve_input_video, stage_path
from modules.review_export.contracts import (
    ARTIFACT_MODELS,
    REVIEW_EXPORT_SCHEMA_VERSION,
    CommentaryArtifact,
    ReviewExport,
    ReviewStageState,
    SegmentCommentaryArtifact,
)
from modules.review_export.court_positions import derive_court_positions


ALIASES = {
    "match_segmentation": "segments", "score_recognition": "scores",
    "event_detection": "events", "stroke_classification": "strokes",
    "audio_highlight": "audio_signals", "highlight_ranking": "highlights",
    "player_identity": "identity", "commentary": "commentary",
    "court_detection": "court", "pose": "pose", "shuttle_tracking": "shuttle",
}


class OptionalArtifactError(ValueError):
    def __init__(self, alias: str, message: str):
        super().__init__(message)
        self.alias = alias


@dataclass(frozen=True)
class ExportOptions:
    title: str | None = None
    players: dict[str, str] | None = None
    video_url: str | None = None
    duration: float | None = None
    scenario: str | None = None
    backend_base_commit: str | None = None


def _sha256_16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _read_json_snapshot(path: Path) -> tuple[Any, str]:
    data = path.read_bytes()
    return json.loads(data), _sha256_16(data)


def _unique_segments(rows: list[Any], count: int, alias: str) -> None:
    ids = [int(row.segment_index) for row in rows]
    if len(ids) != len(set(ids)):
        raise OptionalArtifactError(alias, "duplicate segment index")
    if any(index < 0 or index >= count for index in ids):
        raise OptionalArtifactError(alias, "unknown segment index")


class ArtifactSnapshot:
    def __init__(self, match_path: Path):
        self.match_path = match_path
        self.raw: dict[str, dict[str, Any]] = {}
        self.fingerprints: dict[str, str] = {}
        self.paths: dict[str, Path] = {}
        self.observed_files: dict[Path, str | None] = {}
        self.commentary_segment_names: tuple[str, ...] | None = None
        self.states: dict[str, ReviewStageState] = {}

    def observe(self, path: Path) -> str | None:
        """Record file content or absence for the final consistency check."""
        fingerprint = _sha256_16(path.read_bytes()) if path.is_file() else None
        self.observed_files[path] = fingerprint
        return fingerprint

    def load(self) -> None:
        # Fingerprint every primary artifact first, so stale checks share the runner's
        # content-based semantics even if another optional stage is invalid.
        for stage, spec in PIPELINE.items():
            path = artifact_path(self.match_path, stage)
            fingerprint = self.observe(path)
            self.observe(stage_path(self.match_path, stage) / "status.json")
            if fingerprint is not None:
                self.fingerprints[stage] = fingerprint
                self.paths[stage] = path

        for stage, alias in ALIASES.items():
            spec = PIPELINE[stage]
            path = artifact_path(self.match_path, stage)
            if not path.is_file():
                self.states[alias] = ReviewStageState(status="missing", usable=False,
                    message="artifact is missing")
                continue
            try:
                status = read_status(stage_path(self.match_path, stage))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                self.states[alias] = ReviewStageState(status="error", usable=False,
                    message=f"invalid status.json: {exc}", fingerprint=self.fingerprints[stage])
                continue
            if status is None:
                self.states[alias] = ReviewStageState(status="error", usable=False,
                    message="status.json is missing", fingerprint=self.fingerprints[stage])
                continue
            if status.status != StageStatus.COMPLETED:
                self.states[alias] = ReviewStageState(status="error", usable=False,
                    message=f"stage status is {status.status.value}", fingerprint=self.fingerprints[stage])
                continue
            try:
                raw, fingerprint = _read_json_snapshot(path)
                model = ARTIFACT_MODELS.get(stage)
                if model is not None:
                    raw = model.model_validate(raw).model_dump(mode="json")
                elif not isinstance(raw, dict) or not isinstance(raw.get(spec.record_key), list):
                    raise ValueError(f"expected {spec.record_key!r} list")
            except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                self.states[alias] = ReviewStageState(status="error", usable=False,
                    message=f"invalid artifact: {exc}", fingerprint=self.fingerprints[stage])
                continue
            if status.inputs is None:
                state = ReviewStageState(status="unknown", usable=True,
                    message="dependency fingerprints were not recorded", fingerprint=fingerprint)
            else:
                dependencies = [*spec.dependencies, *spec.optional_dependencies]
                current = {name: self.fingerprints[name] for name in dependencies
                           if name in self.fingerprints}
                names = set(status.inputs) | set(current)
                changed = sorted(name for name in names if status.inputs.get(name) != current.get(name))
                if changed:
                    self.states[alias] = ReviewStageState(status="stale", usable=False,
                        message=f"dependency changed: {', '.join(changed)}", fingerprint=fingerprint)
                    continue
                state = ReviewStageState(status="available", usable=True, fingerprint=fingerprint)
            self.states[alias] = state
            self.raw[alias] = raw

    def verify_unchanged(self) -> None:
        for path, fingerprint in self.observed_files.items():
            current = _sha256_16(path.read_bytes()) if path.is_file() else None
            if current != fingerprint:
                raise RuntimeError(f"source changed during export: {path.relative_to(self.match_path)}")
        if self.commentary_segment_names is not None:
            directory = self.match_path / "stages" / "commentary" / "segments"
            current_names = tuple(sorted(path.name for path in directory.iterdir()
                if path.is_file() and re.fullmatch(r"segment_\d{3,}\.json", path.name)
            )) if directory.is_dir() else ()
            if current_names != self.commentary_segment_names:
                raise RuntimeError("source changed during export: commentary segment manifest")


def _check_artifacts(snapshot: ArtifactSnapshot, duration: float) -> tuple[list[dict[str, Any]], float]:
    segments_artifact = snapshot.raw.get("segments")
    if segments_artifact is None:
        state = snapshot.states.get("segments")
        raise ValueError(f"required match_segmentation is unavailable: {state.message if state else 'missing'}")
    segments = segments_artifact["segments"]
    fps = float(segments_artifact["fps"])
    for index, segment in enumerate(segments):
        if segment["end_frame"] < segment["start_frame"] or segment["end_sec"] > duration + 0.001:
            raise ValueError("segment order or bounds are invalid")
        if index and segment["start_frame"] <= segments[index - 1]["end_frame"]:
            raise ValueError("segments overlap or are out of order")
        if (abs(segment["start_sec"] - segment["start_frame"] / fps) >= 0.0011
                or abs(segment["end_sec"] - segment["end_frame"] / fps) >= 0.0011
                or abs(segment["duration_sec"] - (segment["end_sec"] - segment["start_sec"])) >= 0.0011):
            raise ValueError("segment frames and absolute time disagree")

    def locate(frame: int) -> int:
        return next((i for i, item in enumerate(segments)
                     if item["start_frame"] <= frame <= item["end_frame"]), -1)

    def disable(alias: str, message: str, *, stale: bool = False) -> None:
        previous = snapshot.states.get(alias)
        snapshot.states[alias] = ReviewStageState(
            status="stale" if stale else "error", usable=False, message=message,
            fingerprint=previous.fingerprint if previous else None,
        )
        snapshot.raw.pop(alias, None)

    scores = snapshot.raw.get("scores")
    if scores:
        _unique_segments([type("R", (), row) for row in scores["rallies"]], len(segments), "scores")
        for score in scores["rallies"]:
            if (score["score_a"] is None) != (score["score_b"] is None):
                raise OptionalArtifactError("scores", "score values must be missing together")
            subs, splits = score.get("sub_scores"), score.get("split_secs")
            if subs is not None or splits is not None:
                if not subs or len(subs) < 2 or splits is None or len(splits) != len(subs) - 1:
                    raise OptionalArtifactError("scores", "multi-observation score data is incomplete")
                segment = segments[score["segment_index"]]
                if any(t < segment["start_sec"] or t > segment["end_sec"]
                       or (i and t <= splits[i - 1]) for i, t in enumerate(splits)):
                    raise OptionalArtifactError("scores", "score observation time is invalid")

    identity = snapshot.raw.get("identity")
    if identity:
        occupied: set[int] = set()
        for epoch in identity["epochs"]:
            if epoch["first_segment"] > epoch["last_segment"] or epoch["last_segment"] >= len(segments):
                raise OptionalArtifactError("identity", "identity epoch is outside segments")
            if epoch["top"] == epoch["bottom"]:
                raise OptionalArtifactError("identity", "identity sides must differ")
            for index in range(epoch["first_segment"], epoch["last_segment"] + 1):
                if index in occupied:
                    raise OptionalArtifactError("identity", "identity epochs overlap")
                occupied.add(index)

    events = snapshot.raw.get("events")
    if events:
        previous = -1
        for event in events["events"]:
            if locate(event["frame"]) < 0 or event["frame"] <= previous:
                raise OptionalArtifactError("events", "events are outside segments or unordered")
            previous = event["frame"]
    strokes = snapshot.raw.get("strokes")
    if strokes:
        if not events or len(strokes["strokes"]) != len(events["events"]):
            disable("strokes", "source events are unavailable", stale=not events)
            strokes = None
        else:
            for index, stroke in enumerate(strokes["strokes"]):
                if (stroke["event_index"] != index or stroke["frame"] != events["events"][index]["frame"]
                        or locate(stroke["frame"]) != stroke["segment_index"]):
                    disable("strokes", "stroke identity, frame, or segment is invalid")
                    strokes = None
                    break

    audio = snapshot.raw.get("audio_signals")
    if audio:
        _unique_segments([type("R", (), row) for row in audio["signals"]], len(segments), "audio_signals")
        for signal in audio["signals"]:
            if (signal["n_cheer_windows"] == 0) != (signal["cheer_intensity"] is None):
                raise OptionalArtifactError("audio_signals", "audio cheer support count is inconsistent")
        for window in audio.get("windows") or []:
            segment_index = window["segment_index"]
            if (segment_index >= len(segments)
                    or window["end_sec"] <= window["start_sec"]
                    or window["end_sec"] > duration + 0.001
                    or window["start_sec"] < segments[segment_index]["start_sec"] - 0.001):
                raise OptionalArtifactError("audio_signals", "audio window timestamp or segment is invalid")
    highlights = snapshot.raw.get("highlights")
    if highlights:
        _unique_segments([type("R", (), row) for row in highlights["highlights"]], len(segments), "highlights")
        audio_ids = {row["segment_index"] for row in (audio or {}).get("signals", [])}
        if not audio or any(row["segment_index"] not in audio_ids for row in highlights["highlights"]):
            disable("highlights", "source audio observations are unavailable", stale=not audio)
    return segments, fps


def _commentary(snapshot: ArtifactSnapshot, segments: list[dict[str, Any]], fps: float,
                strokes: dict[str, Any] | None, events: dict[str, Any] | None) -> tuple[dict[int, Any], dict[int, str]]:
    by_segment: dict[int, Any] = {}
    unsupported: dict[int, str] = {}

    def validate_rally(rally: dict[str, Any]) -> None:
        sid = rally["segment_index"]
        if sid >= len(segments) or (rally.get("summary") and rally["summary"]["segment_index"] != sid):
            raise ValueError("commentary segment mismatch")
        seen: set[int] = set()
        previous: tuple[float, int, int] | None = None
        for event in rally["events"]:
            index = event["stroke_index"]
            source = next((s for s in (strokes or {}).get("strokes", []) if s["event_index"] == index), None)
            order = (event["time_sec"], event["frame"], index)
            if (event["segment_index"] != sid or source is None or events is None
                    or source["segment_index"] != sid or source["frame"] != event["frame"]
                    or index >= len(events["events"]) or events["events"][index]["frame"] != event["frame"]
                    or abs(event["time_sec"] - event["frame"] / fps) >= 0.0011
                    or index in seen or (previous is not None and order <= previous)):
                raise ValueError("commentary source identity, segment, or time mismatch")
            seen.add(index)
            previous = order

    raw = snapshot.raw.get("commentary")
    if raw:
        try:
            full = CommentaryArtifact.model_validate(raw).model_dump(mode="json")
            ids: set[int] = set()
            for rally in full["rallies"]:
                validate_rally(rally)
                if rally["segment_index"] in ids:
                    raise ValueError("duplicate full commentary segment")
                ids.add(rally["segment_index"])
                by_segment[rally["segment_index"]] = ("full-match", rally)
            for item in full["unsupported_segments"]:
                if item["segment_index"] in ids:
                    raise ValueError("commentary cannot be available and unsupported")
                unsupported[item["segment_index"]] = item["reason"]
        except (ValidationError, ValueError) as exc:
            snapshot.states["commentary"] = ReviewStageState(status="error", usable=False,
                message=f"invalid commentary: {exc}", fingerprint=snapshot.states["commentary"].fingerprint)
            snapshot.raw.pop("commentary", None)
            by_segment.clear(); unsupported.clear()

    directory = snapshot.match_path / "stages" / "commentary" / "segments"
    selected_paths = (sorted(path for path in directory.iterdir()
        if path.is_file() and re.fullmatch(r"segment_\d{3,}\.json", path.name))
        if directory.is_dir() else [])
    snapshot.commentary_segment_names = tuple(path.name for path in selected_paths)
    if not selected_paths:
        snapshot.states["commentary_segments"] = ReviewStageState(status="missing", usable=False,
            message="no on-demand commentary artifacts")
        return by_segment, unsupported
    selected: list[tuple[Path, dict[str, Any], str]] = []
    try:
        for path in selected_paths:
            raw_item, fingerprint = _read_json_snapshot(path)
            item = SegmentCommentaryArtifact.model_validate(raw_item).model_dump(mode="json")
            validate_rally(item["rally"])
            if item["segment_index"] != item["rally"]["segment_index"]:
                raise ValueError("on-demand commentary segment mismatch")
            selected.append((path, item, fingerprint))
        ids = [item["segment_index"] for _, item, _ in selected]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate on-demand commentary segment")
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        snapshot.states["commentary_segments"] = ReviewStageState(status="error", usable=False,
            message=f"invalid on-demand commentary: {exc}")
        return by_segment, unsupported
    for path, item, fingerprint in selected:
        key = f"commentary/segments/{path.stem}"
        snapshot.paths[key] = path
        snapshot.fingerprints[key] = fingerprint
        snapshot.observed_files[path] = fingerprint
        by_segment[item["segment_index"]] = ("on-demand", item["rally"])
        unsupported.pop(item["segment_index"], None)
    snapshot.states["commentary_segments"] = ReviewStageState(status="available", usable=True,
        fingerprint=_sha256_16("".join(item[2] for item in selected).encode()))
    return by_segment, unsupported


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def export_review(match_path: str | Path, options: ExportOptions | None = None) -> dict[str, Any]:
    options = options or ExportOptions()
    match_path = Path(match_path).resolve()
    video = resolve_input_video(match_path)
    duration = options.duration if options.duration is not None else get_video_duration(str(video))
    metadata_path = match_path / "review-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    players = options.players or metadata.get("players") or {"a": "選手 A（記分板列）", "b": "選手 B（記分板列）"}
    snapshot = ArtifactSnapshot(match_path)
    snapshot.observe(metadata_path)
    snapshot.load()
    while True:
        try:
            segments, fps = _check_artifacts(snapshot, duration)
            break
        except OptionalArtifactError as exc:
            previous = snapshot.states.get(exc.alias)
            snapshot.states[exc.alias] = ReviewStageState(status="error", usable=False,
                message=str(exc), fingerprint=previous.fingerprint if previous else None)
            snapshot.raw.pop(exc.alias, None)
    scores, identity = snapshot.raw.get("scores"), snapshot.raw.get("identity")
    events, strokes = snapshot.raw.get("events"), snapshot.raw.get("strokes")
    audio, highlights = snapshot.raw.get("audio_signals"), snapshot.raw.get("highlights")
    commentary, unsupported = _commentary(snapshot, segments, fps, strokes, events)

    def row(data: dict[str, Any] | None, key: str, sid: int) -> dict[str, Any] | None:
        return next((item for item in (data or {}).get(key, []) if item["segment_index"] == sid), None)

    rallies: list[dict[str, Any]] = []
    for sid, segment in enumerate(segments):
        score = row(scores, "rallies", sid)
        epoch = next((item for item in (identity or {}).get("epochs", [])
                      if item["first_segment"] <= sid <= item["last_segment"]), None)
        mapping = epoch
        segment_score = ([score["score_a"], score["score_b"]]
                         if score and score["score_a"] is not None and score["score_b"] is not None else None)
        hits = None
        if events:
            hits = []
            for event_index, event in enumerate(events["events"]):
                if not (segment["start_frame"] <= event["frame"] <= segment["end_frame"]):
                    continue
                stroke = strokes["strokes"][event_index] if strokes else None
                identity_row = mapping.get(stroke["player"]) if stroke and stroke.get("player") and mapping else None
                player = players[identity_row] if identity_row else (
                    "畫面上方" if stroke and stroke.get("player") == "top" else
                    "畫面下方" if stroke and stroke.get("player") == "bottom" else "未知球員")
                # Segment seconds are rounded to the millisecond while the frame is
                # exact, so a hit on a boundary frame can sit a fraction of a
                # millisecond outside its rally. The frame already decided which
                # rally it belongs to; keep its time inside that rally.
                time = min(max(event["frame"] / fps, segment["start_sec"]), segment["end_sec"])
                hits.append({"eventIndex": event_index, "strokeIndex": event_index,
                    "frame": event["frame"], "time": time,
                    "ordinal": len(hits) + 1, "player": player,
                    "type": stroke["stroke_type"] if stroke else None,
                    "confidence": stroke["confidence"] if stroke else None,
                    "hitter": identity_row, "hitterSide": stroke.get("player") if stroke else None})

        def evidence(ref: str) -> dict[str, Any]:
            hit = next((item for item in hits or [] if ref == f"rally:{sid}:stroke:{item['eventIndex']}"), None)
            if hit is None:
                return {"id": ref, "text": "無法取得依據", "eventIndex": None, "time": None}
            return {"id": ref, "text": f"第 {hit['ordinal']} 拍 · {hit['player']} · {hit['type'] or '未提供球種'}（來源擊球觀察）",
                    "eventIndex": hit["eventIndex"], "time": hit["time"]}

        comment = commentary.get(sid)
        if comment:
            source, value = comment
            summary = value.get("summary")
            commentary_model = {"status": "available", "source": source,
                "summary": ({"segmentIndex": summary["segment_index"], "text": summary["text"],
                    "sourceFactIds": summary["source_fact_ids"],
                    "evidence": [evidence(ref) for ref in summary["source_fact_ids"]]} if summary else None),
                "events": [{"segmentIndex": item["segment_index"], "strokeIndex": item["stroke_index"],
                    "frame": item["frame"], "timeSec": item["time_sec"], "player": item["player"],
                    "text": item["text"], "sourceFactIds": item["source_fact_ids"],
                    "evidence": [evidence(ref) for ref in item["source_fact_ids"]]} for item in value["events"]]}
        else:
            reason = unsupported.get(sid)
            commentary_model = {"status": "unsupported" if reason else "unavailable", "source": None,
                "summary": None, "events": [], **({"unsupportedReason": reason} if reason else {})}
        signal, highlight = row(audio, "signals", sid), row(highlights, "highlights", sid)
        attempt = next((item for item in (scores or {}).get("attempts", []) if item.get("segment_index") == sid), None)
        conflict = (f"局數衝突：scores 第 {score['game_index'] + 1} 局；identity 推導第 {epoch['game_index'] + 1} 局"
                    if score and score.get("game_index") is not None and epoch
                    and score["game_index"] != epoch["game_index"] else None)
        rally: dict[str, Any] = {"id": sid, "start": segment["start_sec"], "end": segment["end_sec"],
            "duration": segment["duration_sec"], "score": segment_score,
            "game": score.get("game_index") if score and score.get("game_index") is not None
                    else epoch.get("game_index") if epoch else None,
            "multi": bool(score and score.get("sub_scores")),
            "subScores": score.get("sub_scores") or [] if score else [],
            "splits": score.get("split_secs") or [] if score else [], "hits": hits,
            "audio": ({"segmentIndex": signal["segment_index"], "confidence": signal["cheer_confidence"],
                "intensity": signal["cheer_intensity"], "windowCount": signal["n_cheer_windows"]}
                if signal else None), "highlight": highlight["score"] if highlight else None,
            "commentary": commentary_model}
        if score and score.get("game_index") is not None: rally["gameSource"] = "scores"
        elif epoch: rally["gameSource"] = "identity"
        if conflict: rally["gameConflict"] = conflict
        if mapping: rally["identity"] = {"top": mapping["top"], "bottom": mapping["bottom"]}
        if segment_score is None and attempt and attempt.get("note"): rally["scoreIssue"] = attempt["note"]
        rallies.append(rally)

    derive_court_positions(rallies, snapshot.raw.get("court"), snapshot.raw.get("pose"), fps)
    available_count = len(commentary)
    states = {name: state.model_dump(exclude_none=True) for name, state in snapshot.states.items()}
    capabilities = {
        "score": bool(snapshot.states.get("scores") and snapshot.states["scores"].usable),
        "stroke": bool(snapshot.states.get("events") and snapshot.states["events"].usable),
        "identity": bool(snapshot.states.get("identity") and snapshot.states["identity"].usable),
        "cheer": bool(snapshot.states.get("audio_signals") and snapshot.states["audio_signals"].usable),
        "highlight": bool(snapshot.states.get("highlights") and snapshot.states["highlights"].usable),
        "commentary": available_count > 0,
        "court": bool(snapshot.states.get("court") and snapshot.states["court"].usable),
        "pose": bool(snapshot.states.get("pose") and snapshot.states["pose"].usable),
        "shuttle": bool(snapshot.states.get("shuttle") and snapshot.states["shuttle"].usable),
    }
    cheer_timeline = ([{"segmentIndex": item["segment_index"], "start": item["start_sec"],
        "end": item["end_sec"], "time": (item["start_sec"] + item["end_sec"]) / 2,
        "score": item["cheer_probability"]} for item in audio.get("windows") or []]
        if audio and audio.get("windows") is not None else None)
    fingerprints = {ALIASES.get(stage, stage): value for stage, value in snapshot.fingerprints.items()}
    result = ReviewExport(schemaVersion=REVIEW_EXPORT_SCHEMA_VERSION,
        title=options.title or metadata.get("title") or match_path.name,
        video=options.video_url or f"/local-video/{match_path.name}", duration=duration,
        scenario=options.scenario or f"match:{match_path.name}", players=players,
        capabilities=capabilities, states=states, rallies=rallies, cheerTimeline=cheer_timeline,
        commentaryAvailability={"coverage": "none" if available_count == 0 else
            "complete" if available_count == len(rallies) else "partial",
            "availableRallyCount": available_count, "unsupportedRallyCount": len(unsupported),
            "totalRallyCount": len(rallies)}, fps=fps,
        source={"matchId": match_path.name, "importedAt": datetime.now(timezone.utc).isoformat(),
            "backendBaseCommit": options.backend_base_commit or _git_head(Path(__file__).resolve().parents[2]),
            "fingerprints": fingerprints,
            "limitations": ["分析片段不等於完整得分回合；資料一致性不代表辨識準確率。",
                "比分是 segment 內觀察結果；缺值保留，未推導得分前後或勝者。",
                "局數與場上身分依已驗證 artifact 推導；未知舊指紋會明確標示。",
                "歡呼與精彩分數為片段彙總指標，不代表戰術品質。"]}).model_dump(mode="json")
    snapshot.verify_unchanged()
    return result
