import assert from "node:assert/strict";
import test from "node:test";
import type { RallyModel } from "../src/domain/models";
import {
  exactScoreRally,
  scoreLaneRally,
} from "../src/components/timeline/timelineScoreInteraction";

function rally(
  id: number,
  start: number,
  end: number,
  score: [number, number],
): RallyModel {
  return {
    id,
    start,
    end,
    duration: end - start,
    score,
    game: 0,
    multi: false,
    subScores: [],
    splits: [],
    hits: null,
    audio: null,
    highlight: null,
    commentary: {
      status: "unavailable",
      source: null,
      summary: null,
      events: [],
    },
  };
}

const scores = [
  rally(0, 0, 10, [0, 0]),
  rally(1, 10, 20, [0, 0]),
  rally(2, 20, 30, [1, 2]),
  rally(3, 30, 40, [2, 2]),
];
const matchViewport = { startSec: 0, endSec: 40, durationSec: 40 };

test("exact visible score targets preserve canonical Rally identity and score", () => {
  assert.equal(exactScoreRally(scores, 0), scores[0]);
  assert.deepEqual(exactScoreRally(scores, 0)?.score, [0, 0]);
  assert.equal(exactScoreRally(scores, 1), scores[1]);
  assert.deepEqual(exactScoreRally(scores, 1)?.score, [0, 0]);
  assert.equal(exactScoreRally(scores, 2), scores[2]);
  assert.deepEqual(exactScoreRally(scores, 2)?.score, [1, 2]);
});

test("exact score selection never falls through to a neighboring or active Rally", () => {
  const activePlaybackRally = scores[3];
  const selected = scoreLaneRally(
    scores,
    0,
    activePlaybackRally.end,
    matchViewport,
    800,
  );
  assert.equal(selected, scores[0]);
  assert.notEqual(selected, activePlaybackRally);
  assert.equal(scoreLaneRally(scores, 99, 20, matchViewport, 800), null);
});

test("adjacent boundaries and repeated scores still resolve the requested marker", () => {
  assert.equal(scoreLaneRally(scores, 1, 20, matchViewport, 800), scores[1]);
  assert.equal(scoreLaneRally(scores, 2, 20, matchViewport, 800), scores[2]);
});

test("empty score-lane space retains intentional nearest-marker behavior", () => {
  assert.equal(
    scoreLaneRally(scores, null, 19.8, matchViewport, 800),
    scores[1],
  );
  const customViewport = { startSec: 18, endSec: 32, durationSec: 14 };
  assert.equal(
    scoreLaneRally(scores, null, 29.95, customViewport, 900),
    scores[2],
  );
  const rallyFit = { startSec: 19.5, endSec: 30.5, durationSec: 11 };
  assert.equal(scoreLaneRally(scores, null, 30, rallyFit, 700), scores[2]);
});
