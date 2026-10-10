import assert from "node:assert/strict";
import test from "node:test";
import type { RallyModel } from "../src/domain/models";
import {
  gamePointSides,
  leadChartMarks,
  leadDomain,
  leadFraction,
  leadGamePaths,
  leadGridlines,
  leadY,
  scoreLeadModel,
} from "../src/temporal/scoreLead";
import { timelineHoverPreview } from "../src/components/timeline/timelinePreview";

function rally(
  id: number,
  score: [number, number] | null,
  game: number | null = 0,
  extra: Partial<RallyModel> = {},
): RallyModel {
  const start = id * 10;
  return {
    id,
    start,
    end: start + 8,
    duration: 8,
    score,
    game,
    multi: false,
    subScores: [],
    splits: [],
    hits: null,
    audio: null,
    highlight: null,
    commentary: { status: "unavailable", source: null, summary: null, events: [] },
    ...extra,
  };
}

test("each game restarts at a tie and holds every observation until the next Rally", () => {
  const model = scoreLeadModel([
    rally(0, [0, 0], 0),
    rally(1, [1, 0], 0),
    rally(2, [0, 0], 1),
    rally(3, [0, 1], 1),
  ]);
  assert.equal(model.games.length, 2);
  assert.deepEqual(model.games.map((game) => game.steps.map((step) => step.lead)), [[0, 1], [0, -1]]);
  assert.equal(model.games[0]!.steps[0]!.end, 10, "held until the next Rally starts");
  assert.equal(model.games[0]!.steps[1]!.end, 18, "the last Rally of a game ends at its own end");
  assert.equal(model.maxAbsLead, 1);
});

test("a missing game index splits games only where the score resets", () => {
  const model = scoreLeadModel([
    rally(0, [19, 18], null),
    rally(1, [20, 18], null),
    rally(2, [0, 0], null),
  ]);
  assert.deepEqual(model.games.map((game) => game.steps.length), [2, 1]);
});

test("unobserved Rallies become gaps at the last observed lead, never a new score", () => {
  const model = scoreLeadModel([rally(0, null), rally(1, [2, 0]), rally(2, null), rally(3, [3, 1])]);
  const game = model.games[0]!;
  assert.deepEqual(game.steps.map((step) => step.rally.id), [1, 3]);
  assert.deepEqual(game.gaps.map((gap) => [gap.rally.id, gap.lead]), [[0, 0], [2, 2]]);
});

test("lead changes mark only a reversal of the previous leader, not leaving a tie", () => {
  const model = scoreLeadModel([
    rally(0, [0, 0]),
    rally(1, [1, 0]),
    rally(2, [1, 1]),
    rally(3, [1, 2]),
    rally(4, [2, 2]),
    rally(5, [2, 3]),
  ]);
  assert.deepEqual(
    model.games[0]!.steps.map((step) => step.leadChange),
    [null, null, null, "b", null, null],
  );
});

test("game point follows the 21-point, two-clear, 30-cap rule", () => {
  assert.deepEqual(gamePointSides([20, 18]), ["a"]);
  assert.deepEqual(gamePointSides([20, 19]), ["a"]);
  assert.deepEqual(gamePointSides([20, 20]), []);
  assert.deepEqual(gamePointSides([21, 20]), ["a"]);
  assert.deepEqual(gamePointSides([28, 29]), ["b"]);
  assert.deepEqual(gamePointSides([29, 29]), ["a", "b"]);
  assert.deepEqual(gamePointSides([5, 20]), ["b"]);
});

test("match point needs a known earlier game win, and every game point in game 3 is one", () => {
  const model = scoreLeadModel([
    rally(0, [17, 20], 0),
    rally(1, [20, 15], 1),
    rally(2, [20, 10], 2),
  ]);
  assert.equal(model.games[0]!.winner, "b");
  assert.deepEqual(model.games[0]!.steps[0]!.points, [{ side: "b", match: false }]);
  assert.deepEqual(model.games[1]!.steps[0]!.points, [{ side: "a", match: false }]);
  assert.deepEqual(model.games[2]!.steps[0]!.points, [{ side: "a", match: true }]);
  const won = scoreLeadModel([rally(0, [20, 17], 0), rally(1, [20, 15], 1)]);
  assert.deepEqual(won.games[1]!.steps[0]!.points, [{ side: "a", match: true }]);
  const unknown = scoreLeadModel([rally(0, [20, 20], 0), rally(1, [20, 15], 1)]);
  assert.equal(unknown.games[0]!.winner, null);
  assert.deepEqual(unknown.games[1]!.steps[0]!.points, [{ side: "a", match: false }]);
});

test("the scale is symmetric, linear in a close match and compressed past six points", () => {
  assert.equal(leadDomain(2), 4);
  assert.equal(leadFraction(2, 4), 0.5);
  assert.equal(leadFraction(-4, 4), -1);
  const domain = leadDomain(21);
  assert.ok(Math.abs(leadFraction(6, domain) - 0.7) < 1e-9);
  assert.equal(leadFraction(-21, domain), -1);
  assert.ok(leadFraction(3, domain) - leadFraction(2, domain) > leadFraction(15, domain) - leadFraction(14, domain));
  assert.equal(leadFraction(40, domain), 1, "never drawn past the lane");
  assert.equal(leadY(0, domain), 50);
  assert.ok(leadY(1, domain) < 50, "A leads upward");
  assert.deepEqual(leadGridlines(4), []);
  assert.deepEqual(leadGridlines(9), [5]);
  assert.deepEqual(leadGridlines(21), [5, 10]);
});

test("paths are step lines with split fills, dashed gaps and viewport culling", () => {
  const model = scoreLeadModel([rally(0, [0, 0]), rally(1, [1, 0]), rally(2, null), rally(3, [1, 2])]);
  const view = { startSec: 0, endSec: 40, durationSec: 40 };
  const paths = leadGamePaths(model.games[0]!, view, 4);
  assert.doesNotMatch(paths.line, /L /, "no sloped segments");
  assert.match(paths.line, /^M 0\.000 50\.000 V 50\.000 H 25\.000 V 40\.000 H 50\.000 M 75\.000 40\.000 V 60\.000 H 95\.000$/);
  assert.equal(paths.dashed, "M 50.000 40.000 H 75.000");
  assert.match(paths.areaA, /V 40\.000/);
  assert.doesNotMatch(paths.areaA, /V 60\.000/, "B's lead never fills A's half");
  assert.match(paths.areaB, /V 60\.000/);
  const zoomed = leadGamePaths(model.games[0]!, { startSec: 31, endSec: 36, durationSec: 5 }, 4);
  assert.doesNotMatch(zoomed.line, /-?\d{4,}\./, "off-screen x is clamped");
  assert.equal(zoomed.dashed, "");
});

test("marks label peaks and chips only where they fit", () => {
  const rallies = [rally(0, [0, 0]), rally(1, [5, 0]), rally(2, [20, 5]), rally(3, [20, 6])];
  const model = scoreLeadModel(rallies);
  const domain = leadDomain(model.maxAbsLead);
  const wide = leadChartMarks(model, { startSec: 0, endSec: 40, durationSec: 40 }, domain, 800);
  assert.deepEqual(wide.labels.map((label) => label.text), ["G1"]);
  assert.deepEqual(wide.peaks.map((peak) => [peak.rallyId, peak.text, peak.direction, peak.align]), [[2, "+15", "down", "start"]]);
  assert.equal(wide.chips.length, 4, "each 200px step has room for its score");
  const narrow = leadChartMarks(model, { startSec: 0, endSec: 40, durationSec: 40 }, domain, 120);
  assert.equal(narrow.chips.length, 0);
  assert.equal(narrow.peaks.length, 1);
  const tight = leadChartMarks(model, { startSec: 0, endSec: 40, durationSec: 40 }, domain, 60);
  assert.equal(tight.peaks.length, 0);
});

test("a chip scrolls locked to its step and is pinned only once the step centre leaves the view", () => {
  const model = scoreLeadModel([rally(0, [3, 1])]);
  const at = (startSec: number) =>
    leadChartMarks(model, { startSec, endSec: startSec + 4, durationSec: 4 }, 4, 400).chips[0]!.x;
  // Centre at 4s; 30px of 400px is 0.3s of the 4s span.
  assert.equal(at(1), 75);
  assert.equal(at(2), 50, "moves at the content's speed, 100px per second");
  assert.equal(at(3), 25);
  assert.ok(Math.abs(at(4.5) - 7.5) < 1e-9, "pinned 30px in from the left edge");
  assert.ok(Math.abs(at(4.9) - 7.5) < 1e-9, "stays put while pinned");
});

test("score hover reads the lead, game point and reversal from one observation", () => {
  const rallies = [rally(0, [0, 0]), rally(1, [0, 1]), rally(2, [20, 18]), rally(3, null)];
  const model = scoreLeadModel([rallies[0]!, rallies[1]!, { ...rallies[2]!, score: [2, 1] }, rallies[3]!]);
  const players = { a: "選手 A（記分板列）", b: "Lee" };
  const reversal = timelineHoverPreview({ kind: "score", id: 2 }, [rallies[0]!, rallies[1]!, { ...rallies[2]!, score: [2, 1] }], [], { model, players });
  assert.equal(reversal?.title, "比分 2:1");
  assert.deepEqual(reversal?.lines.slice(0, 2), ["選手 A 領先 1", "領先易主：選手 A 反超"]);
  const point = scoreLeadModel(rallies);
  const gamePoint = timelineHoverPreview({ kind: "score", id: 2 }, rallies, [], { model: point, players });
  assert.ok(gamePoint?.lines.includes("選手 A 局點"));
  const gap = timelineHoverPreview({ kind: "score", id: 3 }, rallies, [], { model: point, players });
  assert.equal(gap?.title, "比分未觀測");
});
