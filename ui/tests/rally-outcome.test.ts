import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import type { RallyModel } from "../src/domain/models";
import { scoreLeadModel } from "../src/temporal/scoreLead";
import {
  MIN_BAR_FRACTION,
  breakAt,
  highlightRanks,
  rallyBarFraction,
  rallyIndexLabels,
  rallyLaneMarks,
  rallyLaneModel,
  rallyOutcomes,
} from "../src/temporal/rallyOutcome";
import { rallyBreakPreview, timelineHoverPreview } from "../src/components/timeline/timelinePreview";

/** Rallies 8 s long, one every 30 s unless `start` is given. */
function rally(
  id: number,
  score: [number, number] | null,
  game: number | null = 0,
  extra: Partial<RallyModel> = {},
): RallyModel {
  const start = extra.start ?? id * 30;
  const duration = extra.duration ?? 8;
  return {
    id,
    start,
    end: start + duration,
    duration,
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

const model = (rallies: RallyModel[]) => rallyLaneModel(rallies, scoreLeadModel(rallies));
const winners = (rallies: RallyModel[]) =>
  rallyOutcomes(rallies, scoreLeadModel(rallies)).map((outcome) => outcome.winner);

test("a Rally's winner is the side the next pre-Rally score shows one point ahead", () => {
  assert.deepEqual(
    winners([rally(0, [0, 0]), rally(1, [1, 0]), rally(2, [1, 1]), rally(3, [2, 1])]),
    ["a", "b", "a", null],
    "the last Rally has no next score and its game has no resolved winner",
  );
});

test("an unobserved score, a jump of more than one point or a reversal leaves the winner unknown", () => {
  assert.deepEqual(
    winners([rally(0, [0, 0]), rally(1, null), rally(2, [2, 0]), rally(3, [4, 0]), rally(4, [4, 1])]),
    [null, null, null, "b", null],
  );
  assert.deepEqual(winners([rally(0, [3, 2]), rally(1, [3, 1])]), [null, null]);
});

test("the final Rally of a game takes the game winner from the lead model", () => {
  const rallies = [rally(0, [19, 20]), rally(1, [20, 20]), rally(2, [21, 20]), rally(3, [0, 0], 1), rally(4, [1, 0], 1)];
  const outcomes = rallyOutcomes(rallies, scoreLeadModel(rallies));
  assert.deepEqual(outcomes.map((item) => item.winner), ["a", "a", "a", "a", null]);
  assert.deepEqual(outcomes.map((item) => item.game), [0, 0, 0, 1, 1]);
  // 20:20 is game point for both sides, so the lead model leaves that game open.
  assert.equal(winners([rally(0, [19, 20]), rally(1, [20, 20])]).at(-1), null);
});

test("winners never cross a game boundary", () => {
  assert.deepEqual(winners([rally(0, [5, 3]), rally(1, [0, 0], 1), rally(2, [0, 1], 1)]), [null, "b", null]);
});

test("a multi-point segment stays unresolved", () => {
  assert.deepEqual(
    winners([rally(0, [0, 0]), rally(1, [1, 0], 0, { multi: true }), rally(2, [2, 1]), rally(3, [3, 1])]),
    ["a", null, "a", null],
  );
});

test("breaks: a new game, the 10→11 interval and other long gaps", () => {
  const rallies = [
    rally(0, [9, 10], 0, { start: 0 }),
    rally(1, [9, 11], 0, { start: 80 }), // 72 s after 10→11
    rally(2, [10, 11], 0, { start: 108 }), // 20 s: normal
    rally(3, [10, 12], 0, { start: 170 }), // 54 s: long
    rally(4, [0, 0], 1, { start: 320 }), // new game
    rally(5, [1, 0], 1, { start: 350 }),
  ];
  const lane = model(rallies);
  assert.deepEqual(lane.breaks.map((item) => [item.kind, item.afterRallyId, item.end - item.start]), [
    ["interval", 0, 72],
    ["long", 2, 54],
    ["game", 3, 142],
  ]);
  assert.equal(breakAt(lane, 50)?.kind, "interval");
  assert.equal(breakAt(lane, 100), null);
  assert.deepEqual(rallyBreakPreview(lane.breaks[0]!), { title: "技術暫停", lines: ["72.0 秒"] });
});

test("a short pause at 10→11 is not an interval", () => {
  assert.deepEqual(model([rally(0, [10, 4], 0, { start: 0 }), rally(1, [11, 4], 0, { start: 25 })]).breaks, []);
});

test("bar height is sqrt of duration against the 95th percentile, floored and capped", () => {
  assert.equal(rallyBarFraction(16, 16), 1);
  assert.equal(rallyBarFraction(4, 16), 0.5);
  assert.equal(rallyBarFraction(40, 16), 1);
  assert.equal(rallyBarFraction(0.1, 16), MIN_BAR_FRACTION);
  const durations = Array.from({ length: 20 }, (_, index) => index + 1);
  assert.equal(model(durations.map((duration, id) => rally(id, [id, 0], 0, { duration }))).durationCap, 19);
});

test("lane marks are neutral: bars carry no winner", () => {
  const rallies = [rally(0, [0, 0]), rally(1, [1, 0]), rally(2, [1, 1])];
  const marks = rallyLaneMarks(model(rallies), rallies, { startSec: 0, endSec: 100, durationSec: 100 }, 1000);
  assert.deepEqual(Object.keys(marks.bars[0]!).sort(), ["fraction", "rallyId", "splits", "wide", "width", "x"]);
});

test("bars keep a visible minimum width, cull outside the viewport and turn to a tint when wide", () => {
  const rallies = [0, 1, 2, 3, 4].map((id) => rally(id, [id, 0]));
  const lane = model(rallies);
  const marks = rallyLaneMarks(lane, rallies, { startSec: 55, endSec: 100, durationSec: 45 }, 450);
  assert.deepEqual(marks.bars.map((bar) => bar.rallyId), [2, 3]);
  const wide = rallyLaneMarks(lane, rallies, { startSec: 0, endSec: 100000, durationSec: 100000 }, 1000);
  assert.ok(wide.bars.every((bar) => bar.width >= 0.15 - 1e-9));
  const at = (endSec: number) =>
    rallyLaneMarks(lane, rallies, { startSec: 0, endSec, durationSec: endSec }, 1000).bars[0]!.wide;
  assert.equal(at(200), false, "8 s of 200 s is 40 px");
  assert.equal(at(10), true);
});

test("breaks are never drawn on the lane; hovering the gap names them", () => {
  const rallies = [rally(0, [10, 4], 0, { start: 0 }), rally(1, [11, 4], 0, { start: 90 })];
  const lane = model(rallies);
  assert.equal(lane.breaks[0]?.kind, "interval");
  const marks = rallyLaneMarks(lane, rallies, { startSec: 0, endSec: 200, durationSec: 200 }, 2000);
  assert.deepEqual(Object.keys(marks).sort(), ["bars", "flags"]);
});

test("the rally tooltip never says who took the point", () => {
  const rallies = [rally(0, [0, 0]), rally(1, [0, 1]), rally(2, [1, 1])];
  const lead = { model: scoreLeadModel(rallies), players: { a: "甲（記分板列）", b: "乙" } };
  assert.deepEqual(timelineHoverPreview({ kind: "rally", id: 0 }, rallies, [], lead)?.lines, ["8.00 秒", "比分 0:0"]);
  for (const item of rallies)
    assert.ok(!timelineHoverPreview({ kind: "rally", id: item.id }, rallies, [], lead)?.lines.some((line) => line.startsWith("得分")));
});

test("the sample match: every technical interval and game break is found", () => {
  const match = JSON.parse(readFileSync("public/matches/ASG_vs_AA_2020.json", "utf8")) as { rallies: RallyModel[] };
  const lead = scoreLeadModel(match.rallies);
  const lane = rallyLaneModel(match.rallies, lead);
  const kinds = (kind: string) => lane.breaks.filter((item) => item.kind === kind).map((item) => item.afterRallyId);
  assert.deepEqual(kinds("game"), [37, 73]);
  assert.deepEqual(kinds("interval"), [19, 54, 88]);
  assert.equal(rallyOutcomes(match.rallies, lead).filter((outcome) => outcome.winner === null).length, 0);
});

const spaced = (count: number, period: number) =>
  Array.from({ length: count }, (_, id) => rally(id, [id, 0], 0, { start: id * period, duration: 8 }));

test("rally numbers stride with zoom alone, so scrolling never reshuffles them", () => {
  const rallies = spaced(40, 30);
  const numbers = (startSec: number, durationSec: number, width = 1000) =>
    rallyIndexLabels(rallies, { startSec, endSec: startSec + durationSec, durationSec }, width, null, []).map((item) => item.text);
  // 30 s apart at 1000 px per 300 s is 100 px: every Rally fits.
  assert.deepEqual(numbers(0, 300).slice(0, 3), ["001", "002", "003"]);
  // 30 s apart at 1000 px per 1200 s is 25 px: every second one.
  assert.deepEqual(numbers(0, 1200).slice(0, 3), ["002", "004", "006"]);
  assert.deepEqual(numbers(15, 1200).slice(0, 2), ["002", "004"], "a small scroll keeps the same numbers");
  // 30 s apart at 1000 px per 12000 s is 2.5 px: too dense to number.
  assert.deepEqual(numbers(0, 12000), []);
});

test("the selected Rally is always numbered and others make room for it", () => {
  const rallies = spaced(40, 30);
  const view = { startSec: 0, endSec: 1200, durationSec: 1200 };
  const labels = rallyIndexLabels(rallies, view, 1000, 4, []);
  assert.deepEqual(labels.filter((item) => item.selected).map((item) => item.text), ["005"]);
  assert.ok(!labels.some((item) => ["004", "006"].includes(item.text)), "neighbours within one slot yield");
  const dense = rallyIndexLabels(rallies, { startSec: 0, endSec: 12000, durationSec: 12000 }, 1000, 4, []);
  assert.deepEqual(dense.map((item) => item.text), ["005"]);
});

test("a number stays on its Rally while any of it is in view", () => {
  const rallies = [rally(0, [0, 0], 0, { start: 100, duration: 20 })];
  const at = (startSec: number) =>
    rallyIndexLabels(rallies, { startSec, endSec: startSec + 20, durationSec: 20 }, 1000, null, [])[0]?.x;
  assert.equal(at(100), 50, "locked to the centre while the centre is in view");
  // View 112–132: the centre (110) has left, so the label pins inside the edge.
  assert.ok(Math.abs(at(112)! - 1.4) < 1e-9);
  assert.equal(at(121), undefined, "gone once the Rally has left the view");
});

test("rally numbers yield to game labels", () => {
  const rallies = spaced(10, 30);
  const view = { startSec: 0, endSec: 300, durationSec: 300 };
  const texts = rallyIndexLabels(rallies, view, 1000, null, [{ x: 0 }]).map((item) => item.text);
  assert.ok(!texts.includes("001"));
  assert.ok(texts.includes("002"));
});

test("a selected Rally out of view waits at the nearer edge with an arrow", () => {
  const rallies = [rally(0, [0, 0], 0, { start: 100, duration: 10 }), rally(1, [1, 0], 0, { start: 140, duration: 10 })];
  const labels = (startSec: number) =>
    rallyIndexLabels(rallies, { startSec, endSec: startSec + 10, durationSec: 10 }, 1000, 0, []);
  // View 115–125 sits in the gap after Rally 0: no Rally in view, the number still shows.
  const gap = labels(115);
  assert.deepEqual(gap.map(({ text, pinned }) => [text, pinned]), [["‹ 001", "start"]]);
  assert.ok(Math.abs(gap[0]!.x - 2.2) < 1e-9);
  assert.deepEqual(labels(80).map(({ text, pinned }) => [text, pinned]), [["001 ›", "end"]]);
  assert.deepEqual(labels(98).map(({ text, pinned }) => [text, pinned]), [["001", null]]);
});

test("highlight places run best first, ties share a place, unscored rallies are unranked", () => {
  const scored = [0.4, 0.9, null, 0.6, 0.6].map((highlight, id) => rally(id, [id, 0], 0, { highlight }));
  assert.deepEqual([...highlightRanks(scored)].sort((a, b) => a[0] - b[0]), [[0, 4], [1, 1], [3, 2], [4, 2]]);
});

test("the rally tooltip gives the highlight place, never the score", () => {
  const scored = [0.4, 0.9, 0.6].map((highlight, id) => rally(id, [id, 0], 0, { highlight }));
  const lines = timelineHoverPreview({ kind: "rally", id: 2 }, scored)?.lines ?? [];
  assert.equal(lines.at(-1), "精華排名 #2 / 3");
  assert.ok(!lines.some((line) => line.includes("0.6")));
});

test("review flags mark an unreadable score or a disputed game, one triangle each", () => {
  const rallies = [
    rally(0, [0, 0]),
    rally(1, null, 0, { scoreIssue: "parse error: no digits" }),
    rally(2, [2, 0], 0, { gameConflict: "局數衝突：scores 第 1 局；identity 推導第 2 局" }),
    rally(3, [3, 0]),
  ];
  const marks = rallyLaneMarks(model(rallies), rallies, { startSec: 0, endSec: 120, durationSec: 120 }, 1200);
  assert.deepEqual(marks.flags.map((flag) => flag.rallyId), [1, 2]);
  assert.equal(marks.flags[0]!.x, 34 / 120 * 100, "at the Rally centre");
});

test("a multi-point bar is cut at each score change once it has room", () => {
  const multi = rally(0, [3, 2], 0, { start: 0, duration: 20, multi: true, subScores: [[3, 2], [3, 3]], splits: [12, 40] });
  const lane = model([multi]);
  const at = (width: number) =>
    rallyLaneMarks(lane, [multi], { startSec: 0, endSec: 100, durationSec: 100 }, width).bars[0]!.splits;
  assert.deepEqual(at(1000), [12], "a split outside the Rally is dropped");
  assert.deepEqual(at(50), [], "a 10 px bar has no room for a cut");
  const single = rally(1, [0, 0], 0, { start: 0, duration: 20, splits: [12] });
  assert.deepEqual(rallyLaneMarks(model([single]), [single], { startSec: 0, endSec: 100, durationSec: 100 }, 1000).bars[0]!.splits, []);
});

test("the rally tooltip explains a review flag and lists multi-point scores", () => {
  const long = "parse error: " + "x".repeat(120);
  const rallies = [
    rally(0, null, 0, { scoreIssue: long }),
    rally(1, [3, 2], 0, { gameConflict: "局數衝突：scores 第 1 局；identity 推導第 2 局", multi: true, subScores: [[3, 2], [3, 3]], splits: [35] }),
  ];
  const issue = timelineHoverPreview({ kind: "rally", id: 0 }, rallies)!.lines.at(-1)!;
  assert.ok(issue.startsWith("比分無法辨識：parse error"));
  assert.equal(issue.length, "比分無法辨識：".length + 60);
  assert.deepEqual(timelineHoverPreview({ kind: "rally", id: 1 }, rallies)!.lines.slice(-2), [
    "多筆比分 3:2 → 3:3",
    "局數衝突：scores 第 1 局；identity 推導第 2 局",
  ]);
});
