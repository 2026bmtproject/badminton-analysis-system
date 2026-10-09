from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from modules.contracts import PIPELINE
from modules.review_export import ExportOptions, export_review
from modules.review_export.exporter import ArtifactSnapshot
from modules.review_export.contracts import ReviewExport
from modules.review_export.court_positions import (
    ankle_ground_point, bbox_ground_point, derive_court_positions,
)
from modules.review_export.schema import artifact_schema_text, schema_text

FIXTURES = Path(__file__).parents[1] / "ui" / "fixtures" / "stages"
ALIASES = {
    "match_segmentation": "segments", "score_recognition": "scores",
    "event_detection": "events", "stroke_classification": "strokes",
    "audio_highlight": "audio_signals", "highlight_ranking": "highlights",
    "commentary": "commentary",
}


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def write_stage(match: Path, stage: str, payload: dict, inputs: dict[str, str] | None = None) -> Path:
    spec = PIPELINE[stage]
    directory = match / "stages" / stage
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / spec.output_filename
    output.write_text(json.dumps(payload), encoding="utf-8")
    (directory / "status.json").write_text(json.dumps({"name": stage, "status": "completed",
        "output_path": str(output.relative_to(match)), "inputs": inputs}), encoding="utf-8")
    return output


def fixture_match(tmp_path: Path, adjust=None) -> Path:
    """Write the UI fixtures as a completed match; ``adjust(stage, payload)`` edits one first."""
    match = tmp_path / "fixture_match"
    (match / "input").mkdir(parents=True)
    (match / "input" / "match.mp4").write_bytes(b"fixture")
    paths: dict[str, Path] = {}
    for stage, alias in ALIASES.items():
        payload = json.loads((FIXTURES / f"{alias}.json").read_text(encoding="utf-8"))
        if stage == "audio_highlight":
            payload["windows"] = [
                {"segment_index": 1, "start_sec": 11, "end_sec": 14, "cheer_probability": .2},
                {"segment_index": 1, "start_sec": 12, "end_sec": 15, "cheer_probability": .8},
            ]
        if adjust:
            adjust(stage, payload)
        inputs = {dep: fingerprint(paths[dep]) for dep in
                  [*PIPELINE[stage].dependencies, *PIPELINE[stage].optional_dependencies]
                  if dep in paths}
        paths[stage] = write_stage(match, stage, payload, inputs)
    identity = {"epochs": [{"epoch_index": 0, "game_index": 1, "first_segment": 0,
        "last_segment": 2, "top": "a", "bottom": "b", "votes": 2,
        "agreement": 1, "resolved_by": "vote"}]}
    paths["player_identity"] = write_stage(match, "player_identity", identity, {
        dep: fingerprint(paths[dep]) for dep in PIPELINE["player_identity"].dependencies if dep in paths})
    # The fixture loop writes commentary before identity; refresh the real dependency
    # snapshot so the full-match artifact represents a completed current run.
    commentary_status = match / "stages" / "commentary" / "status.json"
    status = json.loads(commentary_status.read_text())
    status["inputs"] = {dep: fingerprint(paths[dep]) for dep in
        [*PIPELINE["commentary"].dependencies, *PIPELINE["commentary"].optional_dependencies]
        if dep in paths}
    commentary_status.write_text(json.dumps(status))
    selected = json.loads((FIXTURES / "commentary_segments.json").read_text(encoding="utf-8"))
    directory = match / "stages" / "commentary" / "segments"
    directory.mkdir(parents=True)
    for item in selected:
        (directory / f"segment_{item['segment_index']:03d}.json").write_text(json.dumps(item), encoding="utf-8")
    (match / "review-metadata.json").write_text(json.dumps({"title": "Fixture",
        "players": {"a": "A", "b": "B"}}), encoding="utf-8")
    return match


def test_export_joins_absolute_timeline_scores_audio_and_commentary(tmp_path: Path):
    match = fixture_match(tmp_path)
    model = export_review(match, ExportOptions(duration=44, video_url="/local-video/fixture_match"))
    ReviewExport.model_validate(model)
    assert model["schemaVersion"] == "review-export-v1"
    assert [(r["id"], r["start"], r["hits"][0]["time"]) for r in model["rallies"][:2]] == [
        (0, 2.0, 2.48), (1, 11.0, 11.48)]
    assert model["rallies"][2]["score"] == [19, 19]
    assert model["rallies"][2]["subScores"] == [[19, 18], [19, 19]]
    assert model["rallies"][1]["audio"]["confidence"] == .85
    assert model["rallies"][1]["highlight"] == .875
    assert [(w["time"], w["score"]) for w in model["cheerTimeline"]] == [(12.5, .2), (13.5, .8)]
    assert model["rallies"][0]["commentary"]["source"] == "on-demand"
    assert model["rallies"][1]["commentary"]["source"] == "on-demand"
    assert model["rallies"][3]["commentary"]["status"] == "unsupported"
    assert model["source"]["matchId"] == "fixture_match"


def test_hit_on_a_rounded_rally_boundary_stays_inside_its_rally(tmp_path: Path):
    # Segmentation stores seconds rounded to the millisecond (1.9605 here) while
    # the frame is exact: frame 49 / 25 fps = 1.96 s lies just before the rally.
    def adjust(stage: str, payload: dict) -> None:
        if stage == "match_segmentation":
            payload["segments"][0].update(start_frame=49, start_sec=1.9605, duration_sec=9 - 1.9605)
        if stage == "event_detection":
            payload["events"][0]["frame"] = 49

    model = export_review(fixture_match(tmp_path, adjust),
                          ExportOptions(duration=44, video_url="/local-video/fixture_match"))
    rally = model["rallies"][0]
    assert rally["hits"][0]["frame"] == 49
    assert rally["start"] <= rally["hits"][0]["time"] <= rally["end"]


def test_optional_missing_invalid_stale_and_unknown_are_distinct(tmp_path: Path):
    match = fixture_match(tmp_path)
    # Missing pose has no effect on basic review. Old identity explicitly has unknown provenance.
    identity_status = match / "stages" / "player_identity" / "status.json"
    status = json.loads(identity_status.read_text())
    status["inputs"] = None
    identity_status.write_text(json.dumps(status))
    (match / "stages" / "score_recognition" / "scores.json").write_text("{broken")
    # Whitespace changes the required segments fingerprint without changing its semantics,
    # making downstream audio/highlight stale according to the runner's exact rule.
    segments = match / "stages" / "match_segmentation" / "segments.json"
    segments.write_text(segments.read_text() + "\n")
    model = export_review(match, ExportOptions(duration=44))
    assert model["states"]["scores"]["status"] == "error"
    assert model["states"]["identity"]["status"] == "unknown"
    assert model["states"]["audio_signals"]["status"] == "stale"
    assert model["states"]["pose"]["status"] == "missing"
    assert model["capabilities"]["score"] is False
    assert model["rallies"][0]["start"] == 2.0

    fresh = fixture_match(tmp_path / "fresh")
    status_path = fresh / "stages" / "player_identity" / "status.json"
    status = json.loads(status_path.read_text()); status["inputs"] = None
    status_path.write_text(json.dumps(status))
    unknown = export_review(fresh, ExportOptions(duration=44))
    assert unknown["states"]["identity"]["status"] == "unknown"
    assert unknown["states"]["identity"]["usable"] is True


def test_python_court_policy_preserves_fallback_order_and_provenance():
    keypoints = [[0, 0, 0] for _ in range(17)]
    keypoints[15] = [12, 23, .8]; keypoints[16] = [14, 25, .6]
    assert ankle_ground_point(keypoints)["source"] == "ankle_midpoint"
    assert bbox_ground_point([14, 10, 18, 23]) == [16, 23]
    rallies = [{"id": 0, "identity": {"top": "a", "bottom": "b"}, "hits": [{
        "frame": 10, "hitter": "a", "hitterSide": "top"}]}]
    court = {"courts": [{"homography": [[2, 0, 10], [0, 3, 20], [0, 0, 1]]}]}
    bad = {"frame": 10, "segment_index": 0, "player": "top",
           "keypoints": [[0, 0, 0] for _ in range(17)], "bbox": None}
    nearby = {"frame": 11, "segment_index": 0, "player": "top", "keypoints": keypoints, "bbox": None}
    derive_court_positions(rallies, court, {"frames": [bad, nearby]}, 25)
    position = rallies[0]["hits"][0]["courtPosition"]
    assert position["source"] == "nearby_frame_ankle_midpoint"
    assert position["sourceFrame"] == 11
    assert position["x"] == pytest.approx(1.5 / 6.1)
    assert position["y"] == pytest.approx((4 / 3) / 13.41)
    assert rallies[0]["hits"][0]["positionQuality"] == "estimated"


def test_checked_in_schema_is_reproducible():
    directory = Path(__file__).parents[1] / "ui" / "src" / "data"
    assert (directory / "review-export.schema.json").read_text(encoding="utf-8") == schema_text()
    assert (directory / "artifact-contracts.schema.json").read_text(encoding="utf-8") == artifact_schema_text()


def test_snapshot_detects_an_artifact_changed_during_export(tmp_path: Path):
    match = fixture_match(tmp_path)
    snapshot = ArtifactSnapshot(match)
    snapshot.load()
    path = match / "stages" / "audio_highlight" / "audio_signals.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(RuntimeError, match="source changed during export"):
        snapshot.verify_unchanged()


def test_snapshot_detects_status_change_and_new_optional_artifact(tmp_path: Path):
    match = fixture_match(tmp_path)
    snapshot = ArtifactSnapshot(match)
    snapshot.load()
    status = match / "stages" / "audio_highlight" / "status.json"
    status.write_text(status.read_text() + "\n")
    with pytest.raises(RuntimeError, match="status.json"):
        snapshot.verify_unchanged()

    match = fixture_match(tmp_path / "new-artifact")
    missing = match / "stages" / "highlight_ranking" / "highlights.json"
    missing.unlink()
    snapshot = ArtifactSnapshot(match)
    snapshot.load()
    missing.write_text('{"highlights": []}')
    with pytest.raises(RuntimeError, match="highlights.json"):
        snapshot.verify_unchanged()
