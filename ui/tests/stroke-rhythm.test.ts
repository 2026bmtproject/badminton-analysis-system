import assert from "node:assert/strict";
import test from "node:test";
import type { RallyModel, StrokeModel } from "../src/domain/models";
import {
  MIN_DRAWN_DEPTH,
  STROKE_STACK_ORDER,
  strokeComposition,
  strokeDepth,
  strokeFamily,
  strokeGlyph,
  strokeLaneMarks,
  strokeLevel,
  strokeSpacingSec,
} from "../src/temporal/strokeRhythm";
import { timelineHoverPreview } from "../src/components/timeline/timelinePreview";

let nextEvent = 0;
function hit(time: number, extra: Partial<StrokeModel> = {}): StrokeModel {
  const eventIndex = nextEvent++;
  return {
    eventIndex,
    strokeIndex: eventIndex,
    frame: Math.round(time * 25),
    time,
    ordinal: 1,
    player: "A",
    type: "高遠球",
    confidence: 0.9,
    hitter: "a",
    hitterSide: "top",
    courtPosition: { x: 0.5, y: 0.9, coordinateSpace: "court_normalized_v1", source: "ankle_midpoint", sourceFrame: 0 },
    positionQuality: "measured",
    ...extra,
  };
}

function rally(id: number, start: number, hits: StrokeModel[]): RallyModel {
  const end = start + 10;
  return {
    id,
    start,
    end,
    duration: 10,
    score: null,
    game: 0,
    multi: false,
    subScores: [],
    splits: [],
    hits: hits.map((item, index) => ({ ...item, ordinal: index + 1 })),
    audio: null,
    highlight: null,
    commentary: { status: "unavailable", source: null, summary: null, events: [] },
  };
}

const view = (startSec: number, endSec: number) => ({ startSec, endSec, durationSec: endSec - startSec });

test("shot types fall into families with one glyph each; anything unrecognised is unknown", () => {
  assert.deepEqual(
    ["殺球", "撲球", "小球", "勾球", "切球", "高遠球", "平快球", "發球", "未知球種", null].map(strokeFamily),
    ["attack", "attack", "net", "net", "net", "transition", "transition", "serve", "unknown", "unknown"],
  );
  assert.deepEqual(["殺球", "切球", "平快球", "發球", "未知球種", null].map(strokeGlyph), ["殺", "切", "平", "發", "?", "?"]);
});

test("depth is the distance from the net at y = 0.5; unmeasured positions fall back to mid-court", () => {
  const at = (y: number) => strokeDepth(hit(0, { courtPosition: { ...hit(0).courtPosition!, y } }));
  assert.deepEqual(at(0.5), { depth: 0, estimated: false });
  assert.deepEqual(at(0.1), { depth: 0.8, estimated: false });
  assert.equal(at(-0.1).depth, 1, "a foot past the back line is clamped");
  assert.deepEqual(strokeDepth(hit(0, { courtPosition: undefined })), { depth: 0.5, estimated: true });
  assert.deepEqual(strokeDepth(hit(0, { positionQuality: "estimated" })), { depth: 0.5, estimated: true });
});

test("the trace follows the video: the top-of-frame hitter swings above the net line, whoever they are", () => {
  assert.equal(strokeLevel(hit(0, { hitter: "a", hitterSide: "top" })), 0.8);
  assert.equal(strokeLevel(hit(0, { hitter: "a", hitterSide: "bottom" })), -0.8, "after a change of ends A is drawn below");
  assert.equal(strokeLevel(hit(0, { hitter: "b", hitterSide: "top" })), 0.8);
  assert.equal(strokeLevel(hit(0, { hitter: null, hitterSide: null })), 0, "an unknown side sits on the net line");
  const net = { ...hit(0).courtPosition!, y: 0.52 };
  assert.equal(strokeLevel(hit(0, { courtPosition: net })), MIN_DRAWN_DEPTH, "a net shot still crosses visibly");
});

test("composition is each family's share, and the stack runs serve, transition, net, attack", () => {
  const shares = strokeComposition([hit(0, { type: "發球" }), hit(1), hit(2, { type: "殺球" }), hit(3, { type: "殺球" })]);
  assert.deepEqual(shares, { serve: 0.25, transition: 0.25, attack: 0.5 });
  assert.deepEqual(STROKE_STACK_ORDER.slice(0, 4), ["serve", "transition", "net", "attack"]);
});

test("stroke spacing is the median gap inside Rallies, never across them", () => {
  const rallies = [rally(0, 0, [hit(1), hit(2), hit(4)]), rally(1, 100, [hit(101), hit(101.5)])];
  assert.equal(strokeSpacingSec(rallies), 1);
  assert.equal(strokeSpacingSec([rally(0, 0, [hit(1)])]), 1, "no gaps falls back to one second");
});

test("zoomed out each Rally is one stacked bar; only the focused stroke keeps a marker", () => {
  const focus = hit(3, { type: "殺球" });
  const rallies = [rally(0, 0, [hit(1, { type: "發球" }), focus]), rally(1, 100, [hit(101)])];
  const marks = strokeLaneMarks(rallies, view(0, 1000), 1000, {
    spacingSec: 1,
    focusEventIndices: [focus.eventIndex],
  });
  assert.equal(marks.mode, "bars");
  if (marks.mode !== "bars") return;
  assert.deepEqual(marks.bars[0]!.segments, [
    { family: "serve", from: 0, to: 0.5 },
    { family: "attack", from: 0.5, to: 1 },
  ]);
  assert.equal(marks.bars[0]!.x, 0);
  assert.deepEqual(marks.markers, [{ eventIndex: focus.eventIndex, x: 0.3 }]);
});

test("once strokes are far enough apart the lane becomes a trace, dashed where a depth is a guess", () => {
  const strokes = [
    hit(1, { type: "發球", hitterSide: "top" }),
    hit(2, { type: "小球", hitter: "b", hitterSide: "bottom" }),
    hit(3, { type: "殺球", hitterSide: "top", positionQuality: "estimated" }),
  ];
  const marks = strokeLaneMarks([rally(0, 0, strokes)], view(0, 10), 1000, {
    spacingSec: 1,
    focusEventIndices: [],
  });
  assert.equal(marks.mode, "trace");
  if (marks.mode !== "trace") return;
  assert.deepEqual(marks.vertices.map((vertex) => vertex.level), [0.8, -0.8, 0.5]);
  assert.deepEqual(marks.links.map((link) => link.dashed), [false, true]);
  assert.deepEqual(
    marks.labels.map((label) => [label.row, label.text]),
    [["top", "發"], ["bottom", "小"], ["top", "殺"]],
  );
});

test("labels thin out by stroke number at medium zoom, and the focused stroke is always labelled", () => {
  const strokes = Array.from({ length: 10 }, (_, index) => hit(1 + index * 0.35, { hitterSide: index % 2 ? "bottom" : "top" }));
  // 0.35 s apart at 20 px/s: 7 px a stroke, 14 px between labels on a row; a glyph needs 16.
  const marks = strokeLaneMarks([rally(0, 0, strokes)], view(0, 50), 1000, {
    spacingSec: 0.35,
    focusEventIndices: [strokes[3]!.eventIndex],
  });
  assert.equal(marks.mode, "trace");
  if (marks.mode !== "trace") return;
  const numbers = marks.labels.map((label) => strokes.findIndex((item) => item.eventIndex === label.eventIndex) + 1);
  assert.equal(numbers[0], 4, "the focused stroke comes first");
  assert.ok(numbers.slice(1).every((ordinal) => (ordinal - 1) % 2 === 0), `stride 2: ${numbers}`);
  assert.equal(marks.labels[0]!.selected, true);
});

test("labels are glyphs on the hitter's side of the net line; an unknown side gets none", () => {
  const strokes = [
    hit(1, { type: "切球" }),
    hit(3, { type: null, hitter: "b", hitterSide: "bottom" }),
    hit(5, { type: "殺球", hitter: null, hitterSide: null }),
  ];
  const marks = strokeLaneMarks([rally(0, 0, strokes)], view(0, 10), 1000, { spacingSec: 1, focusEventIndices: [] });
  assert.equal(marks.mode, "trace");
  if (marks.mode !== "trace") return;
  assert.deepEqual(marks.labels.map((label) => [label.row, label.text]), [["top", "切"], ["bottom", "?"]]);
  assert.equal(marks.vertices.length, 3, "the unknown side's vertex still sits on the net line");
});

test("hovering a composition bar summarises its Rally, most aggressive family first", () => {
  const strokes = [hit(1, { type: "發球" }), hit(2, { type: "小球" }), hit(3, { type: "殺球" }), hit(4, { type: "小球" })];
  const preview = timelineHoverPreview({ kind: "stroke-rally", id: 4 }, [rally(4, 0, strokes)]);
  assert.deepEqual(preview, { title: "", lines: ["4 拍", "進攻 1", "網前 2", "發球 1"] });
});

test("stroke hover names an unmeasured position but not a low-confidence call", () => {
  const stroke = hit(1, { confidence: 0.32, positionQuality: "estimated" });
  const preview = timelineHoverPreview({ kind: "stroke", id: stroke.eventIndex }, [rally(0, 0, [stroke])]);
  assert.ok(preview?.lines.includes("站位未量測，深度為估計"));
  assert.ok(!preview?.lines.some((line) => line.includes("信心")));
  const measured = hit(1);
  const clean = timelineHoverPreview({ kind: "stroke", id: measured.eventIndex }, [rally(0, 0, [measured])]);
  assert.equal(clean?.lines.length, 3, "time, shot and player only");
});
