import { test } from "node:test";
import assert from "node:assert/strict";
import { parseMatchModel } from "../src/data/matchParser";
import type { MatchModel } from "../src/domain/models";

function model(): MatchModel {
  return {
    schemaVersion: "review-export-v1",
    players: { a: "A", b: "B" }, title: "test", video: "test.mp4", duration: 1,
    scenario: "test", fps: 25, capabilities: {} as MatchModel["capabilities"], states: {},
    commentaryAvailability: { coverage: "none", availableRallyCount: 0,
      unsupportedRallyCount: 0, totalRallyCount: 1 },
    rallies: [{ id: 0, start: 0, end: 1, duration: 1, score: null, game: null,
      identity: { top: "a", bottom: "b" }, multi: false, subScores: [], splits: [],
      audio: null, highlight: null,
      commentary: { status: "unavailable", source: null, summary: null, events: [] },
      hits: [{ eventIndex: 0, strokeIndex: 0, ordinal: 1, frame: 10, time: .4,
        player: "A", hitter: "a", hitterSide: "top", type: null, confidence: null,
        courtPosition: { x: .5, y: .4, coordinateSpace: "court_normalized_v1",
          source: "ankle_midpoint", sourceFrame: 10, confidence: .8 },
        positionQuality: "measured", positionSource: "ankle_midpoint" }],
    }],
  };
}

test("Python-derived court positions remain a validated view-model field", () => {
  const parsed = parseMatchModel(model());
  assert.deepEqual(parsed.rallies[0].hits?.[0].courtPosition, model().rallies[0].hits?.[0].courtPosition);
  const invalid = model();
  invalid.rallies[0].hits![0].courtPosition!.y = -0.2;
  assert.throws(() => parseMatchModel(invalid));
});

test("old catalog JSON without court positions remains parseable", () => {
  const old = model();
  delete old.schemaVersion;
  delete old.rallies[0].hits![0].courtPosition;
  delete old.rallies[0].hits![0].positionQuality;
  delete old.rallies[0].hits![0].positionSource;
  assert.ok(parseMatchModel(old).rallies.length > 0);
});
