import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { adapt, manifestSchema, type Input } from "../scripts/adapter";
import { parseMatchModel } from "../src/data/matchParser";
import type { CheerWindowModel, RallyModel } from "../src/domain/models";
import {
  cheerGameLabels,
  cheerRuns,
  cheerScoreMoments,
  cheerWavePaths,
  highlightMarks,
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
    title: "",
    lines: ["歡呼機率 0.80", "精華排名 #1 / 4"],
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

function rally(id: number, score: [number, number] | null, game = 0): RallyModel {
  return {
    id, start: id * 30, end: id * 30 + 8, duration: 8, score, game, multi: false, subScores: [], splits: [],
    hits: null, audio: null, highlight: null,
    commentary: { status: "unavailable", source: null, summary: null, events: [] },
  };
}

test("triangles mark the top highlight places, numbered as the Rally tooltip numbers them", () => {
  const scores = [0.3, 0.9, null, 0.5, 0.7, 0.2, 0.6, 0.1];
  const rallies = scores.map((highlight, id) => ({ ...rally(id, [id, 0]), highlight }));
  const view = matchViewport(300);
  assert.deepEqual(highlightMarks(rallies, view).map((mark) => [mark.rank, mark.rallyId]), [
    [1, 1], [2, 4], [3, 6], [4, 3], [5, 0],
  ]);
  assert.equal(highlightMarks(rallies, view, 1).length, 1);
  assert.deepEqual(highlightMarks(rallies.map((item) => ({ ...item, highlight: null })), view), []);
  for (const mark of highlightMarks(rallies, view)) {
    const lines = timelineHoverPreview({ kind: "rally", id: mark.rallyId }, rallies)?.lines ?? [];
    assert.ok(lines.includes(`精華排名 #${mark.rank} / 7`), lines.join());
  }
});

test("Rallies tied on a place share its number and each keeps a mark", () => {
  const rallies = [0.9, 0.6, 0.6, 0.5, 0.4, 0.4, 0.3].map((highlight, id) => ({ ...rally(id, [id, 0]), highlight }));
  assert.deepEqual(highlightMarks(rallies, matchViewport(300)).map((mark) => [mark.rank, mark.rallyId]), [
    [1, 0], [2, 1], [2, 2], [4, 3], [5, 4], [5, 5],
  ]);
});

test("each mark sits over the centre of its whole Rally, even where marks crowd", () => {
  const rallies = [
    { ...rally(46, [0, 0]), start: 100, end: 108, highlight: 0.9 },
    { ...rally(3, [0, 0]), start: 110, end: 120, highlight: 0.8 },
    { ...rally(7, [0, 0]), start: 400, end: 408, highlight: 0.7 },
  ];
  const view = { startSec: 0, endSec: 1000, durationSec: 1000 };
  assert.deepEqual(highlightMarks(rallies, view).map((mark) => [mark.rank, Math.round(mark.x * 1e6) / 1e6]), [
    [1, 10.4], [2, 11.5], [3, 40.4],
  ]);
  assert.deepEqual(highlightMarks(rallies, { startSec: 109, endSec: 1109, durationSec: 1000 }).map((mark) => mark.rank), [2, 3]);
});

test("game labels move right past the highlight triangles that share their row", () => {
  const labels = [
    { game: 0, x: 0, text: "G1" },
    { game: 1, x: 40, text: "G2" },
    { game: 2, x: 80, text: "G3" },
  ];
  // 1000 px track: G2 spans 400–420 px; a triangle spans its centre ± 9 px.
  const placed = (...xs: number[]) =>
    cheerGameLabels(labels, xs.map((x) => ({ x })), 1000).map((label) => [label.text, Math.round(label.x * 10) / 10]);
  assert.deepEqual(placed(39), [["G1", 0], ["G2", 40], ["G3", 80]], "a triangle that ends at the separator");
  assert.deepEqual(placed(43), [["G1", 0], ["G2", 40], ["G3", 80]], "a triangle past the label's end");
  assert.deepEqual(placed(41), [["G1", 0], ["G2", 41.9], ["G3", 80]], "the label starts where the triangle ends");
  assert.deepEqual(placed(39.2), [["G1", 0], ["G2", 40.1], ["G3", 80]], "a triangle reaching past the separator");
  assert.deepEqual(placed(41.5, 43), [["G1", 0], ["G2", 43.9], ["G3", 80]], "past every triangle in the way");
  assert.deepEqual(placed(41, 42.6, 44.2), [["G1", 0], ["G3", 80]], "dropped rather than pushed far from its separator");
});

test("tooltip reads the raw window against the score: highlight place, game point and overtake", () => {
  // Pre-Rally scores: A saves two game points, then takes the lead at 21:20.
  const rallies = [rally(0, [18, 20]), { ...rally(1, [19, 20]), highlight: 0.7 }, rally(2, [20, 20]), { ...rally(3, [21, 20]), highlight: 0.9 }];
  const lead = scoreLeadModel(rallies);
  assert.deepEqual(cheerScoreMoments(lead, 1), { points: [{ side: "b", match: false }], overtake: null });
  // Rally 2 is the point that put A ahead, though the board shows it only before Rally 3.
  assert.deepEqual(cheerScoreMoments(lead, 2), { points: [], overtake: "a" });
  assert.deepEqual(cheerScoreMoments(lead, 3), { points: [{ side: "a", match: false }], overtake: null });
  const players = { model: lead, players: { a: "甲", b: "乙" } };
  const windows = [...windowsAt(1, 30, [0.2, 0.9, 0.9, 0.9]), ...windowsAt(2, 60, [0.4])];
  assert.deepEqual(
    timelineHoverPreview({ kind: "cheer-window", id: 0 }, rallies, windows, players),
    { title: "", lines: ["歡呼機率 0.20", "精華排名 #2 / 2", "乙 局點"] },
  );
  assert.deepEqual(
    timelineHoverPreview({ kind: "cheer-window", id: 4 }, rallies, windows, players)?.lines,
    ["歡呼機率 0.40", "甲 反超"],
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
