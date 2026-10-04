import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { adapt, manifestSchema, type Input } from "../scripts/adapter";
import { parseMatchModel } from "../src/data/matchParser";
import { cheerCurvePaths, cheerCurvePoint } from "../src/temporal/cheerCurve";
import { timelineHoverPreview } from "../src/components/timeline/timelinePreview";
import { matchViewport, rallyViewport, timeToPercent } from "../src/temporal/timeline";
import { rankRallies } from "../src/review";

const manifest = manifestSchema.parse(JSON.parse(readFileSync("fixtures/manifest.json", "utf8")));
function fixtures(): Input {
  return Object.fromEntries(manifest.scenarios[0].stages.map((stage) => [
    stage, JSON.parse(readFileSync(`fixtures/stages/${stage}.json`, "utf8")),
  ]));
}

test("window probabilities pass through import while segment scores and highlight ranking stay unchanged", () => {
  const raw = fixtures();
  raw.audio_signals = {
    ...(raw.audio_signals as object),
    windows: [
      { segment_index: 1, start_sec: 11, end_sec: 14, cheer_probability: 0.2 },
      { segment_index: 1, start_sec: 12, end_sec: 15, cheer_probability: 0.8 },
      { segment_index: 1, start_sec: 13, end_sec: 16, cheer_probability: 0.3 },
      { segment_index: 2, start_sec: 22, end_sec: 25, cheer_probability: 0.6 },
    ],
  };
  const before = adapt(fixtures(), manifest, "before");
  const after = parseMatchModel(adapt(raw, manifest, "after"));
  assert.deepEqual(after.cheerTimeline?.map((window) => [window.time, window.score]), [
    [12.5, 0.2], [13.5, 0.8], [14.5, 0.3], [23.5, 0.6],
  ]);
  assert.deepEqual(after.rallies.map((rally) => rally.audio), before.rallies.map((rally) => rally.audio));
  assert.deepEqual(rankRallies(after.rallies, "highlight").map((rally) => rally.id),
    rankRallies(before.rallies, "highlight").map((rally) => rally.id));
  assert.deepEqual(timelineHoverPreview({ kind: "cheer-window", id: 1 }, after.rallies, after.cheerTimeline), {
    title: "00:13",
    lines: ["Cheer probability: 0.80"],
  });
});

test("curve shares absolute viewport coordinates with playhead, stays in 0–1, and leaves gaps", () => {
  const raw = fixtures();
  raw.audio_signals = { ...(raw.audio_signals as object), windows: [
    { segment_index: 1, start_sec: 11, end_sec: 14, cheer_probability: 0 },
    { segment_index: 1, start_sec: 12, end_sec: 15, cheer_probability: 1 },
    { segment_index: 1, start_sec: 13, end_sec: 16, cheer_probability: 0.5 },
    { segment_index: 2, start_sec: 22, end_sec: 25, cheer_probability: 0.6 },
  ] };
  const model = adapt(raw, manifest, "curve");
  const windows = model.cheerTimeline!;
  const full = matchViewport(model.duration);
  const focus = rallyViewport(model.rallies[1]);
  assert.equal(cheerCurvePoint(windows[0], full).x, timeToPercent(12.5, full));
  assert.equal(cheerCurvePoint(windows[0], focus).x, timeToPercent(12.5, focus));
  assert.deepEqual(windows.slice(0, 3).map((window) => cheerCurvePoint(window, full).y), [96, 4, 50]);
  const paths = cheerCurvePaths(windows, full);
  assert.equal(paths.length, 2);
  assert.match(paths[0].d, /M .* L .* L /);
  assert.equal(paths[1].segmentIndex, 2);
  assert.equal(cheerCurvePaths([windows[0]!, windows[2]!], full).length, 1);
  assert.equal(cheerCurvePaths([windows[0]!, { ...windows[2]!, start: 16, end: 19, time: 17.5 }], full).length, 2);
});

test("old artifacts show no curve and malformed window data is rejected", () => {
  const old = parseMatchModel(adapt(fixtures(), manifest, "old"));
  assert.equal(old.cheerTimeline, undefined);
  assert.deepEqual(cheerCurvePaths(old.cheerTimeline ?? [], matchViewport(old.duration)), []);
  assert.throws(() => parseMatchModel({ ...old, cheerTimeline: [
    { segmentIndex: 0, start: 2, end: 5, time: 3.5, score: 1.1 },
  ] }));
  assert.throws(() => parseMatchModel({ ...old, cheerTimeline: [
    { segmentIndex: 0, start: 2, end: 5, time: 2, score: 0.5 },
  ] }));
});
