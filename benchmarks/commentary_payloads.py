"""Experiment B: offline generator view sizing; never invokes a provider."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from benchmarks.commentary_inputs import BatchReads, digest, selection, source_snapshot, provenance
from modules.commentary.adapters.upstream import read_commentary_inputs, build_rally_fact_from_stages
from modules.commentary.analysis.rally_analyzer import analyze_rally
from modules.commentary.analysis.observation_grounding import CAPABILITIES
from modules.commentary.analysis.tactical_analyzer import _validated_inputs, _evidence_catalog
from modules.commentary.facts.builder import build_compact_rally_facts
from modules.commentary.identity import CourtPositionToPlayer


# D: no v2 resolver authorizes these geometry/direction fields as evidence.
# Quality, sample counts, source frames, IDs and ALL limitations stay visible.
REMOVED_POSE = frozenset({"body_center_image", "left_arm_extension", "right_arm_extension",
    "body_extension", "stance_width_ratio", "shoulder_angle_deg", "hitting_arm_candidate"})
REMOVED_SHUTTLE = frozenset({"incoming_unit_vector", "outgoing_unit_vector",
    "incoming_direction", "outgoing_direction"})


def wire_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def baseline_payload(compact, analysis):
    compact, analysis = _validated_inputs(compact, analysis)
    catalog = _evidence_catalog(compact)
    # Mirrors merged v2's payload expression; parity is tested against its actual
    # serialization using a local interception before any provider call.
    return dict(compact_rally_facts=compact.model_dump(mode="json", exclude={"score", "server"}),
        optional_pattern_hints=[p.model_dump(mode="json") for p in analysis.patterns],
        source_catalog=[dict(fact_id=ref, position=item.position,
            allowed_fields=list(CAPABILITIES.get(item.kind, ())) if item.eligible else [])
            for ref, item in catalog.items()])


def candidate_payload(baseline):
    """A benchmark view, NOT a new CompactRallyFacts schema or serializer."""
    view = copy.deepcopy(baseline["compact_rally_facts"])
    del view["schema_version"]
    for event in view["events"]:
        for key, removed in (("pose", REMOVED_POSE), ("shuttle_path", REMOVED_SHUTTLE)):
            if event[key] is not None:
                event[key] = {k: v for k, v in event[key].items() if k not in removed}
    return dict(generator_view_version="benchmark-generator-view-b1", rally_view=view,
        optional_pattern_hints=copy.deepcopy(baseline["optional_pattern_hints"]),
        source_catalog=copy.deepcopy(baseline["source_catalog"]))


def rally_view(payload):
    return payload["compact_rally_facts"] if "compact_rally_facts" in payload else payload["rally_view"]


def coverage(compact, payload):
    """Independent catalog-to-visible-field/value check, not a count heuristic."""
    authoritative = _evidence_catalog(compact)
    visible = {}
    for position, event in enumerate(rally_view(payload)["events"]):
        for fact in (event, event.get("pose"), event.get("court_position"), event.get("shuttle_path")):
            if fact is not None:
                if fact["fact_id"] in visible:
                    raise AssertionError("duplicate visible source ID")
                visible[fact["fact_id"]] = (fact, event, position)
    advertised = {}
    for row in payload["source_catalog"]:
        if row["fact_id"] in advertised:
            raise AssertionError("duplicate advertised source ID")
        advertised[row["fact_id"]] = row
    expected, missing = [], []
    for ref, source in authoritative.items():
        if not source.eligible:
            continue
        for field in CAPABILITIES.get(source.kind, ()):
            owner = source.event if source.kind == "stroke" else source.event.court_position
            value = getattr(owner, field)
            expected.append((ref, field))
            found = visible.get(ref)
            row = advertised.get(ref, {})
            valid = found is not None
            if found is not None:
                fact, event, position = found
                valid = (field in fact and fact[field] == value and position == source.position
                    and event["event_index"] == source.event.event_index
                    and event["player"] == source.event.player
                    and event["stroke_confidence"] == source.event.stroke_confidence
                    and row.get("position") == source.position
                    and field in row.get("allowed_fields", []))
                if source.kind == "court":
                    valid = valid and fact.get("quality") == "reliable" and fact.get("source_frame") == owner.source_frame
            if not valid:
                missing.append(dict(fact_id=ref, field=field))
    expected_ids = {ref for ref, _ in expected}
    missing_ids = {item["fact_id"] for item in missing}
    return dict(eligible_fact_count=len(expected_ids), eligible_field_count=len(expected),
        fully_visible_eligible_fact_count=len(expected_ids - missing_ids),
        visible_eligible_field_count=len(expected)-len(missing), missing=missing,
        coverage_percent=100.0 if not expected else 100*(len(expected)-len(missing))/len(expected))


def verify_context(baseline, candidate):
    before, after = baseline["compact_rally_facts"], candidate["rally_view"]
    assert {k:v for k,v in before.items() if k not in ("schema_version", "events")} == {
        k:v for k,v in after.items() if k != "events"}
    assert baseline["optional_pattern_hints"] == candidate["optional_pattern_hints"]
    assert baseline["source_catalog"] == candidate["source_catalog"]
    assert len(before["events"]) == len(after["events"])
    for a, b in zip(before["events"], after["events"], strict=True):
        assert {k:v for k,v in a.items() if k not in ("pose", "shuttle_path")} == {
            k:v for k,v in b.items() if k not in ("pose", "shuttle_path")}
        for key, removed in (("pose", REMOVED_POSE), ("shuttle_path", REMOVED_SHUTTLE)):
            expected = None if a[key] is None else {k:v for k,v in a[key].items() if k not in removed}
            assert b[key] == expected


def measurements(payload):
    view = rally_view(payload)
    parts = {key: [] for key in ("strokes", "pose", "shuttle", "court")}
    for event in view["events"]:
        parts["strokes"].append({k:v for k,v in event.items() if k not in ("pose", "shuttle_path", "court_position")})
        for key, name in (("pose", "pose"), ("shuttle_path", "shuttle"), ("court_position", "court")):
            if event[key] is not None:
                parts[name].append(event[key])
    attribution = {k: sum(len(wire_bytes(row)) for row in rows) for k,rows in parts.items()}
    attribution["patterns"] = len(wire_bytes(payload["optional_pattern_hints"]))
    attribution["source_catalog"] = len(wire_bytes(payload["source_catalog"]))
    attribution["rally_metadata"] = len(wire_bytes({k:v for k,v in view.items() if k != "events"}))
    encoded = wire_bytes(payload)
    return dict(utf8_bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(),
        top_level_fields=list(payload), serialized_counts={k:len(v) for k,v in parts.items()} | {
            "patterns":len(payload["optional_pattern_hints"]), "catalog_entries":len(payload["source_catalog"])},
        approximate_section_bytes=attribution)


def canonical_hashes(fact, compact, analysis):
    return {name:digest(obj.model_dump(mode="json")) for name,obj in (
        ("rally",fact), ("compact",compact), ("analysis",analysis))}


def measure_rally(fact, compact, analysis):
    before = canonical_hashes(fact, compact, analysis)
    baseline = baseline_payload(compact, analysis)
    baseline_hash = digest(baseline)
    candidate = candidate_payload(baseline)
    assert baseline_hash == digest(baseline), "candidate mutated baseline"
    assert wire_bytes(candidate) == wire_bytes(candidate_payload(baseline)), "non-deterministic candidate"
    verify_context(baseline, candidate)
    baseline_coverage, candidate_coverage = coverage(compact, baseline), coverage(compact, candidate)
    assert not baseline_coverage["missing"] and not candidate_coverage["missing"], "missing eligible evidence"
    after = canonical_hashes(fact, compact, analysis)
    assert before == after, "canonical input mutation"
    a, b = measurements(baseline), measurements(candidate)
    return dict(segment_index=compact.segment_index, canonical_hashes_before=before, canonical_hashes_after=after,
        canonical_inputs_unchanged=before==after, raw_context_unchanged=True, serialization_deterministic=True,
        baseline_coverage=baseline_coverage, candidate_coverage=candidate_coverage, baseline=a, candidate=b,
        byte_reduction=a["utf8_bytes"]-b["utf8_bytes"],
        percent_reduction=100*(a["utf8_bytes"]-b["utf8_bytes"])/a["utf8_bytes"])


def run(match, selections):
    before = source_snapshot(match)
    rows = []
    # Reuse the already tested benchmark-only scanner. Neither serializer changes
    # upstream computation; both consume the very same validated canonical inputs.
    with BatchReads([s for s,_ in selections], True):
        for segment, top in selections:
            mapping = CourtPositionToPlayer(top=top,bottom="b" if top=="a" else "a")
            stages = read_commentary_inputs(match,segment,mapping)
            fact = build_rally_fact_from_stages(stages=stages,segment_index=segment,court_position_to_player=mapping)
            compact = build_compact_rally_facts(stages=stages,segment_index=segment,court_position_to_player=mapping)
            rows.append(measure_rally(fact,compact,analyze_rally(fact)))
    assert before == source_snapshot(match), "source artifacts changed"
    baseline = sum(r["baseline"]["utf8_bytes"] for r in rows)
    candidate = sum(r["candidate"]["utf8_bytes"] for r in rows)
    return dict(rows=rows, baseline_total_bytes=baseline, candidate_total_bytes=candidate,
        total_byte_reduction=baseline-candidate, total_percent_reduction=100*(baseline-candidate)/baseline,
        source_fingerprints=before, court_policy="strict", provider_calls=0,
        note="Bytes are not tokens. Grounding completeness does not prove unchanged model interpretation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", type=Path, required=True)
    parser.add_argument("--selection", nargs="+", type=selection, required=True)
    args = parser.parse_args()
    result = run(args.match,args.selection)
    result["provenance"] = provenance(args.match, args.selection, result["source_fingerprints"])
    print(json.dumps(result,ensure_ascii=True,indent=2))
