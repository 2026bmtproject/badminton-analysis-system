import assert from "node:assert/strict";
import test from "node:test";
import type { RallyModel } from "../src/domain/models";
import {
  exactScoreRally,
  scoreLaneRally,
} from "../src/components/timeline/timelineScoreInteraction";
import { scoreLeadModel } from "../src/temporal/scoreLead";

function rally(
  id: number,
  start: number,
  end: number,
  score: [number, number] | null,
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

const rallies = [
  rally(0, 0, 8, [0, 0]),
  rally(1, 10, 18, [0, 1]),
  rally(2, 20, 28, null),
  rally(3, 30, 38, [2, 1]),
];
const model = scoreLeadModel(rallies);
const matchViewport = { startSec: 0, endSec: 40, durationSec: 40 };

test("exact score targets preserve canonical Rally identity", () => {
  assert.equal(exactScoreRally(model, 0), rallies[0]);
  assert.equal(exactScoreRally(model, 3), rallies[3]);
  assert.equal(exactScoreRally(model, 2), null, "an unobserved Rally has no score mark");
  assert.equal(exactScoreRally(model, 99), null);
});

test("exact score selection never falls through to the Rally under the pointer", () => {
  assert.equal(scoreLaneRally(model, 0, 35, matchViewport, 800), rallies[0]);
  assert.equal(scoreLaneRally(model, 99, 20, matchViewport, 800), null);
});

test("a time resolves to the observation held until the next Rally starts", () => {
  assert.equal(scoreLaneRally(model, null, 4, matchViewport, 800), rallies[0]);
  // Between Rallies the pre-Rally score of the next Rally is not yet shown.
  assert.equal(scoreLaneRally(model, null, 9.5, matchViewport, 800), rallies[0]);
  assert.equal(scoreLaneRally(model, null, 10, matchViewport, 800), rallies[1]);
  assert.equal(scoreLaneRally(model, null, 24, matchViewport, 800), rallies[2]);
  assert.equal(scoreLaneRally(model, null, 38, matchViewport, 800), rallies[3]);
});

test("outside every game only a nearby observation is hit", () => {
  const late = scoreLeadModel([rally(0, 10, 18, [0, 0])]);
  assert.equal(scoreLaneRally(late, null, 9.8, matchViewport, 800), late.games[0]!.steps[0]!.rally);
  assert.equal(scoreLaneRally(late, null, 2, matchViewport, 800), null);
});
