import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { adapt, manifestSchema, type Input } from "../scripts/adapter";
import { parseMatchModel } from "../src/data/matchParser";
import type { CheerWindowModel, RallyModel } from "../src/domain/models";
import {
  CHEER_THRESHOLD,
  cheerPeakMarks,
  cheerPeaks,
  cheerRuns,
  cheerScoreMoments,
  cheerWavePaths,
} from "../src/temporal/cheerCurve";
import { scoreLeadModel } from "../src/temporal/scoreLead";
import { timelineHoverPreview } from "../src/components/timeline/timelinePreview";
import { matchViewport, rallyViewport, timeToPercent } from "../src/temporal/timeline";

const manifest = manifestSchema.parse(JSON.parse(readFileSync("fixtures/manifest.json", "utf8")));
function fixtures(): Input {
  return Object.fromEntries(manifest.scenarios[0].stages.map((stage) => [
    stage, JSON.parse(readFileSync(`fixtures/stages/${stage}.json`, "utf8")),
  ]));
}

test("window probabilities pass through import while segment scores and highlight scores stay unchanged", () => {
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
  assert.deepEqual(after.rallies.map((rally) => rally.highlight), before.rallies.map((rally) => rally.highlight));
  assert.deepEqual(timelineHoverPreview({ kind: "cheer-window", id: 1 }, after.rallies, after.cheerTimeline), {
    title: "00:13",
    lines: ["歡呼機率 0.80", "片段 002"],
  });
});

test("wave shares absolute viewport coordinates with playhead, mirrors about the mid line, and leaves gaps", () => {
  const raw = fixtures();
  raw.audio_signals = { ...(raw.audio_signals as object), windows: [
    { segment_index: 1, start_sec: 11, end_sec: 14, cheer_probability: 0 },
    { segment_index: 1, start_sec: 12, end_sec: 15, cheer_probability: 1 },
    { segment_index: 1, start_sec: 13, end_sec: 16, cheer_probability: 0.5 },
    { segment_index: 2, start_sec: 22, end_sec: 25, cheer_probability: 0.6 },
  ] };
  const model = adapt(raw, manifest, "curve");
  const windows = model.cheerTimeline!;
  const runs = cheerRuns(windows);
  assert.equal(runs.length, 2);
  assert.equal(runs[1]!.segmentIndex, 2);
  // Each window averaged with its neighbours; the raw values stay on the windows for the tooltip.
  assert.deepEqual(runs[0]!.smoothed, [0.5, 0.5, 0.75]);
  const geometry = { mid: 38, amp: 22 };
  for (const view of [matchViewport(model.duration), rallyViewport(model.rallies[1])]) {
    const [first] = cheerWavePaths(runs, view, geometry);
    const x = Math.round(timeToPercent(12.5, view) * 1e4) / 1e4;
    assert.ok(first!.d.startsWith(`M ${x} 27 `), first!.d);
    assert.ok(first!.d.endsWith(`L ${x} 49 Z`), first!.d);
  }
  // A lone window spans its own source interval.
  const lone = cheerWavePaths(runs, matchViewport(model.duration), geometry)[1]!.d;
  assert.match(lone, new RegExp(`^M ${Math.round(timeToPercent(22, matchViewport(model.duration)) * 1e4) / 1e4} `));
  assert.equal(cheerRuns([windows[0]!, windows[2]!]).length, 1);
  assert.equal(cheerRuns([windows[0]!, { ...windows[2]!, start: 16, end: 19, time: 17.5 }]).length, 2);
  assert.deepEqual(cheerWavePaths(runs, matchViewport(model.duration), geometry).map((path) => path.strong), [true, true]);
  // Runs wholly outside the view are not drawn.
  assert.equal(cheerWavePaths(runs, { startSec: 30, endSec: 40, durationSec: 10 }, geometry).length, 0);
});

/** Windows every second over [from, from + scores.length), centred 1.5 s in. */
function windowsAt(segmentIndex: number, from: number, scores: number[]): CheerWindowModel[] {
  return scores.map((score, index) => ({
    segmentIndex, start: from + index, end: from + index + 3, time: from + index + 1.5, score,
  }));
}

test("the wave turns solid exactly where it crosses the threshold", () => {
  // Smoothed to 0, 1/3, 2/3, 1: the crossing sits halfway between the second and third centres.
  const view = { startSec: 0, endSec: 100, durationSec: 100 };
  const paths = cheerWavePaths(cheerRuns(windowsAt(0, 0, [0, 0, 1, 1])), view, { mid: 38, amp: 22 });
  assert.deepEqual(paths.map((path) => path.strong), [false, true]);
  // The weak piece ends and the strong one starts on the threshold line at t = 3.
  assert.match(paths[0]!.d, / L 3 27 L 3 49 /);
  assert.match(paths[1]!.d, /^M 3 27 .* L 3 49 Z$/);
  // Down again and back up: three pieces.
  const twice = cheerWavePaths(cheerRuns(windowsAt(0, 0, [1, 1, 0, 0, 0, 1, 1])), view, { mid: 38, amp: 22 });
  assert.deepEqual(twice.map((path) => path.strong), [true, false, true]);
});

test("peaks rank Rallies by how long the crowd stays above the threshold, one per Rally", () => {
  const runs = cheerRuns([
    // Saturated but brief: the height alone would rank it first.
    ...windowsAt(0, 0, [0, 1, 1, 0, 0]),
    // Long and loud, then a second, shorter stretch in the same Rally.
    ...windowsAt(1, 20, [0, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 0]),
    // A lone noisy window smooths away under the threshold.
    ...windowsAt(2, 40, [0, 0, 1, 0, 0]),
    // As long as Rally 0's stretch but quieter, so it ranks below it.
    ...windowsAt(3, 60, [0.6, 0.6, 0.6, 0.6, 0]),
  ]);
  const peaks = cheerPeaks(runs);
  assert.deepEqual(peaks.map((peak) => [peak.rank, peak.segmentIndex, peak.seconds]), [
    [1, 1, 5], [2, 0, 2], [3, 3, 2],
  ]);
  assert.deepEqual([peaks[0]!.start, peaks[0]!.end], [21.5, 26.5]);
  assert.ok(runs[2]!.smoothed.every((value) => value < CHEER_THRESHOLD));
  assert.equal(cheerPeaks(runs, 1).length, 1);
  // A stretch that lasts to the end of its run still counts.
  assert.deepEqual(cheerPeaks(cheerRuns(windowsAt(4, 0, [0, 0, 1, 1]))).map((peak) => peak.seconds), [1]);
});

test("each peak is marked over the centre of its whole Rally, even where marks crowd", () => {
  const peaks = [
    { rank: 2, segmentIndex: 3, start: 112, end: 116, seconds: 4 },
    { rank: 1, segmentIndex: 46, start: 102, end: 106, seconds: 4 },
    { rank: 3, segmentIndex: 7, start: 402, end: 406, seconds: 4 },
  ];
  const rallies = [
    { id: 46, start: 100, end: 108 },
    { id: 3, start: 110, end: 120 },
    { id: 7, start: 400, end: 408 },
  ];
  const view = { startSec: 0, endSec: 1000, durationSec: 1000 };
  // Rally 3 is centred at 115 s though its cheer is centred at 114 s; 1 s from its neighbour, it still stays put.
  assert.deepEqual(cheerPeakMarks(peaks, rallies, view).map((mark) => [mark.rank, Math.round(mark.x * 1e6) / 1e6]), [
    [1, 10.4], [2, 11.5], [3, 40.4],
  ]);
  assert.deepEqual(cheerPeakMarks(peaks, rallies, { startSec: 109, endSec: 1109, durationSec: 1000 }).map((mark) => mark.rank), [2, 3]);
});

function rally(id: number, score: [number, number] | null, game = 0): RallyModel {
  return {
    id, start: id * 30, end: id * 30 + 8, duration: 8, score, game, multi: false, subScores: [], splits: [],
    hits: null, audio: null, highlight: null,
    commentary: { status: "unavailable", source: null, summary: null, events: [] },
  };
}

test("tooltip reads the raw window against the score: peak place, game point and overtake", () => {
  // Pre-Rally scores: A saves two game points, then takes the lead at 21:20.
  const rallies = [rally(0, [18, 20]), rally(1, [19, 20]), rally(2, [20, 20]), rally(3, [21, 20])];
  const lead = scoreLeadModel(rallies);
  assert.deepEqual(cheerScoreMoments(lead, 1), { points: [{ side: "b", match: false }], overtake: null });
  // Rally 2 is the point that put A ahead, though the board shows it only before Rally 3.
  assert.deepEqual(cheerScoreMoments(lead, 2), { points: [], overtake: "a" });
  assert.deepEqual(cheerScoreMoments(lead, 3), { points: [{ side: "a", match: false }], overtake: null });
  const players = { model: lead, players: { a: "甲", b: "乙" } };
  const windows = [...windowsAt(1, 30, [0.2, 0.9, 0.9, 0.9]), ...windowsAt(2, 60, [0.4])];
  const peaks = cheerPeaks(cheerRuns(windows));
  assert.deepEqual(
    timelineHoverPreview({ kind: "cheer-window", id: 0 }, rallies, windows, players, peaks),
    { title: "00:31", lines: ["歡呼機率 0.20", "片段 002", "歡呼高峰 #1 · 持續 3 秒", "乙 局點"] },
  );
  assert.deepEqual(
    timelineHoverPreview({ kind: "cheer-window", id: 4 }, rallies, windows, players, peaks)?.lines,
    ["歡呼機率 0.40", "片段 003", "甲 反超"],
  );
});

test("old artifacts show no curve and malformed window data is rejected", () => {
  const old = parseMatchModel(adapt(fixtures(), manifest, "old"));
  assert.equal(old.cheerTimeline, undefined);
  assert.deepEqual(cheerRuns(old.cheerTimeline ?? []), []);
  assert.throws(() => parseMatchModel({ ...old, cheerTimeline: [
    { segmentIndex: 0, start: 2, end: 5, time: 3.5, score: 1.1 },
  ] }));
  assert.throws(() => parseMatchModel({ ...old, cheerTimeline: [
    { segmentIndex: 0, start: 2, end: 5, time: 2, score: 0.5 },
  ] }));
});
