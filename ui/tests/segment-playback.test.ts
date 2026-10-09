import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { playerShortcutAction } from "../src/interaction/playerShortcuts";
import { segmentSkipTarget } from "../src/temporal/segmentPlayback";
import { defaultWorkspaceLayout, parseWorkspaceLayout } from "../src/state/workspaceLayout";

const segments = [
  { start: 10, end: 20 },
  { start: 20, end: 25 },
  { start: 40, end: 50 },
];

test("segments-only playback keeps playing inside a Segment, including a shared boundary", () => {
  assert.equal(segmentSkipTarget(segments, 10), null);
  assert.equal(segmentSkipTarget(segments, 19.99), null);
  assert.equal(segmentSkipTarget(segments, 20), null);
  assert.equal(segmentSkipTarget(segments, 45), null);
});

test("a gap jumps to the next Segment start and past the last one ends", () => {
  assert.equal(segmentSkipTarget(segments, 0), 10);
  assert.equal(segmentSkipTarget(segments, 25), 40);
  assert.equal(segmentSkipTarget(segments, 30), 40);
  assert.equal(segmentSkipTarget(segments, 50), "end");
  assert.equal(segmentSkipTarget(segments, 70), "end");
});

test("a seek that lands a hair before a Segment start is not re-sought", () => {
  assert.equal(segmentSkipTarget(segments, 39.98), null);
  assert.equal(segmentSkipTarget(segments, 39.9), 40);
});

test("no Segments or no clock never skips", () => {
  assert.equal(segmentSkipTarget([], 5), null);
  assert.equal(segmentSkipTarget(segments, Number.NaN), null);
});

test("the segments-only switch persists, defaults off and has an S shortcut", () => {
  assert.equal(defaultWorkspaceLayout().segmentsOnly, false);
  const saved = { ...defaultWorkspaceLayout(), segmentsOnly: true };
  assert.equal(parseWorkspaceLayout(JSON.stringify(saved)).segmentsOnly, true);
  assert.equal(parseWorkspaceLayout(JSON.stringify({ ...saved, segmentsOnly: "yes" })).segmentsOnly, false);
  assert.equal(playerShortcutAction({ code: "KeyS", key: "s", shiftKey: false }), "toggle-segments-only");
  const review = readFileSync("src/pages/ReviewPage.vue", "utf8");
  assert.match(review, /v-model:segments-only="layout\.segmentsOnly"/);
  const player = readFileSync("src/components/ReviewPlayer.vue", "utf8");
  assert.match(player, /:aria-pressed="segmentsOnly"/);
  assert.match(player, /requestAnimationFrame\(skipGaps\)/);
});
