import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { ankleGroundPoint, bboxGroundPoint, deriveCourtPositions, imagePointToCourt, invertHomography } from "../scripts/court-positions";
import { winnerFromTransition } from "../src/domain/scoreWinner";
import { parseMatchModel } from "../src/data/matchParser";
import type { MatchModel } from "../src/domain/models";

const matrix = [[2,0,10],[0,3,20],[0,0,1]];
const inverse = invertHomography(matrix)!;
function model(): MatchModel {
  return {
    players: { a: "A", b: "B" }, title: "test", video: "test.mp4", duration: 1, scenario: "test", fps: 25,
    capabilities: {} as MatchModel["capabilities"], states: {}, commentaryAvailability: {
      coverage: "none", availableRallyCount: 0, unsupportedRallyCount: 0, totalRallyCount: 1,
    },
    rallies: [{ id: 0, start: 0, end: 1, duration: 1, score: null, game: null,
      identity: { top: "a", bottom: "b" }, multi: false, subScores: [], splits: [], audio: null, highlight: null,
      commentary: { status: "unavailable", source: null, summary: null, events: [] },
      hits: [{ eventIndex: 0, strokeIndex: 0, ordinal: 1, frame: 10, time: .4, player: "A", hitter: "a", type: null, confidence: null }],
    }],
  };
}
function pose(ankles: unknown, frame = 10) {
  const keypoints = Array(17).fill(null);
  keypoints[15] = (ankles as unknown[])[0]; keypoints[16] = (ankles as unknown[])[1];
  return { frames: [{ frame, segment_index: 0, player: "top", keypoints }] };
}
const court = { courts: [{ homography: matrix }] };
test("court landmark and all four corners preserve far/near and left/right", () => {
  for (const [x,y] of [[0,0],[6.1,0],[0,13.41],[6.1,13.41],[3.05,6.705]]) {
    const result = imagePointToCourt([x*2+10,y*3+20], inverse)!;
    assert.ok(Math.abs(result.x-x/6.1) < 1e-9);
    assert.ok(Math.abs(result.y-y/13.41) < 1e-9);
  }
  assert.ok(imagePointToCourt([8,20], inverse)!.x < 0);
});
test("midpoint, single ankle confidence and no-ankle policy", () => {
  const both = ankleGroundPoint(pose([[12,23,.8],[14,25,.6]]).frames[0].keypoints)!;
  assert.deepEqual(both.point, [13,24]); assert.equal(both.source, "ankle_midpoint");
  const one = ankleGroundPoint(pose([[12,23,.8],[14,25,.1]]).frames[0].keypoints)!;
  assert.equal(one.source, "single_ankle"); assert.ok(one.confidence < both.confidence);
  const overshoot = ankleGroundPoint(pose([[12,23,1.0745],[14,25,1.0354]]).frames[0].keypoints)!;
  assert.equal(overshoot.confidence, 1);
  assert.equal(ankleGroundPoint(pose([[12,23,.1],[14,25,.1]]).frames[0].keypoints), null);
});
test("derive fails closed for hitter, frame, ankles, transform and out-of-court", () => {
  const cases: [string, (m: MatchModel) => void, unknown, unknown][] = [
    ["HITTER_UNRESOLVED", m => { m.rallies[0].hits![0].hitter = null; }, court, pose([[12,23,.8],[14,25,.8]])],
    ["POSE_UNAVAILABLE", () => {}, court, pose([[12,23,.8],[14,25,.8]], 14)],
    ["ANKLES_UNAVAILABLE", () => {}, court, pose([[12,23,.1],[14,25,.1]])],
    ["COURT_TRANSFORM_UNAVAILABLE", () => {}, { courts: [{ homography: [[1,0,0],[0,0,0],[0,0,1]] }] }, pose([[12,23,.8],[14,25,.8]])],
    ["OUT_OF_COURT", () => {}, court, pose([[0,23,.8],[0,25,.8]])],
  ];
  for (const [reason, update, c, p] of cases) {
    const m = model(); update(m); deriveCourtPositions(m,c,p);
    assert.equal(m.rallies[0].hits![0].positionUnavailableReason, reason);
    assert.equal(m.rallies[0].hits![0].courtPosition, undefined);
  }
});
test("derived point uses the exact source frame and never mutates artifacts", () => {
  const m = model(), p = pose([[15,23,.8],[17,25,.8]]);
  const hash = (v: unknown) => createHash("sha256").update(JSON.stringify(v)).digest("hex");
  const before = [hash(court), hash(p)];
  deriveCourtPositions(m,court,p);
  assert.deepEqual([hash(court),hash(p)], before);
  assert.equal(m.rallies[0].hits![0].courtPosition?.sourceFrame, 10);
  assert.equal(m.rallies[0].hits![0].courtPosition?.source, "ankle_midpoint");
  const adjacent = model();
  deriveCourtPositions(adjacent, court, pose([[16,18.5,.8],[16,18.5,.8]]));
  assert.ok(adjacent.rallies[0].hits![0].courtPosition!.y < 0);
  assert.ok(parseMatchModel(adjacent).rallies[0].hits![0].courtPosition);
  const distant = model();
  deriveCourtPositions(distant, court, pose([[16,15.5,.8],[16,15.5,.8]]));
  assert.equal(distant.rallies[0].hits![0].positionUnavailableReason, "OUT_OF_COURT");
  adjacent.rallies[0].hits![0].courtPosition!.y = -0.2;
  assert.throws(() => parseMatchModel(adjacent));
});
test("ordered fallbacks use the same hitter within 120 ms and mark estimates", () => {
  const bad = pose([[0,23,.8],[0,25,.8]]).frames[0];
  const good = pose([[15,23,.8],[17,25,.8]], 11).frames[0];
  const nearby = model();
  deriveCourtPositions(nearby, court, { frames: [bad, good] });
  assert.equal(nearby.rallies[0].hits![0].positionSource, "nearby_frame_ankle_midpoint");
  assert.equal(nearby.rallies[0].hits![0].positionQuality, "estimated");
  assert.equal(nearby.rallies[0].hits![0].courtPosition?.sourceFrame, 11);
  assert.ok(parseMatchModel(nearby));

  const single = model();
  deriveCourtPositions(single, court, pose([[15,23,.8],[17,25,.1]]));
  assert.equal(single.rallies[0].hits![0].positionSource, "single_ankle");
  assert.equal(single.rallies[0].hits![0].positionQuality, "estimated");

  assert.deepEqual(bboxGroundPoint([14,10,18,23]), [16,23]);
  assert.equal(bboxGroundPoint([18,10,14,23]), null);
  const box = model();
  const boxFrame = pose([[15,23,.1],[17,25,.1]]).frames[0];
  deriveCourtPositions(box, court, { frames: [{ ...boxFrame, bbox: [14,10,18,23] }] });
  assert.equal(box.rallies[0].hits![0].positionSource, "bbox_bottom_center");
  assert.equal(box.rallies[0].hits![0].positionQuality, "estimated");
  assert.equal(box.rallies[0].hits![0].courtPosition?.confidence, undefined);
  assert.ok(parseMatchModel(box));
});
test("unknown A/B may retain a known hitter side, but never guesses between two sides", () => {
  const neutral = model();
  neutral.rallies[0].hits![0].hitter = null;
  neutral.rallies[0].hits![0].hitterSide = "top";
  deriveCourtPositions(neutral, court, pose([[15,23,.8],[17,25,.8]]));
  assert.equal(neutral.rallies[0].hits![0].positionQuality, "measured");
  assert.equal(neutral.rallies[0].hits![0].hitter, null);
  assert.ok(parseMatchModel(neutral));
  const unknown = model();
  unknown.rallies[0].hits![0].hitter = null;
  deriveCourtPositions(unknown, court, { frames: [
    pose([[15,23,.8],[17,25,.8]]).frames[0],
    { ...pose([[15,23,.8],[17,25,.8]]).frames[0], player: "bottom" },
  ] });
  assert.equal(unknown.rallies[0].hits![0].positionQuality, "unresolved");
  assert.equal(unknown.rallies[0].hits![0].courtPosition, undefined);
});
test("old catalog JSON without court positions remains parseable", () => {
  const parsed = parseMatchModel(JSON.parse(JSON.stringify(model())));
  assert.ok(parsed.rallies.length > 0);
});
test("winner requires one explicit score increment and known identity", () => {
  assert.equal(winnerFromTransition([8,7],[9,7],true),"a");
  assert.equal(winnerFromTransition([8,7],[8,8],true),"b");
  for (const [before,after,known] of [[null,[9,7],true],[[8,7],null,true],[[8,7],[8,7],true],[[8,7],[9,8],true],[[8,7],[9,7],false]] as const)
    assert.equal(winnerFromTransition(before,after,known),null);
});
test("Court Map delegates to existing selected stroke action", () => {
  const page = readFileSync("src/pages/RallyDetailPage.vue", "utf8");
  const map = readFileSync("src/components/inspector/RallyCourtMap.vue", "utf8");
  assert.match(page, /<RallyCourtMap[\s\S]*:selected-stroke-index="workspace\.selectedStrokeIndex\.value"[\s\S]*@stroke="selectStroke"/);
  assert.match(page, /function selectStroke\(stroke: StrokeModel\)[\s\S]*workspace\.selectStroke\(stroke\)/);
  assert.match(map, /@keydown="selectOnKey\(\$event, hit\)"/);
  assert.doesNotMatch(map, /selectedCourtPoint|currentTime\s*=/);
});
