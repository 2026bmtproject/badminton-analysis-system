import assert from "node:assert/strict";
import test from "node:test";
import { clientToCourt, courtCheck, courtReaders, courtSaveLock } from "../src/data/courtCalibration";
import type { PipelineTask } from "../src/data/pipelineTasks";

const image = { x: 0, y: 0, width: 2000, height: 1000 };

test("pointer coordinates follow the same image viewBox through resize", () => {
  const full = { left: 50, top: 20, width: 1000, height: 500 } as DOMRect;
  const half = { left: 50, top: 20, width: 500, height: 250 } as DOMRect;
  assert.deepEqual(clientToCourt(300, 145, full, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(175, 82.5, half, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(0, 0, full, image, 2000, 1000), [0, 0]);
  assert.deepEqual(clientToCourt(2000, 2000, full, image, 2000, 1000), [2000, 1000]);
});

test("padding around the image shifts the origin but never lets a corner leave the image", () => {
  const padded = { x: -100, y: -100, width: 2200, height: 1200 };
  const rect = { left: 0, top: 0, width: 1100, height: 600 } as DOMRect;
  assert.deepEqual(clientToCourt(50, 50, rect, padded, 2000, 1000), [0, 0]);
  assert.deepEqual(clientToCourt(300, 175, rect, padded, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(10, 10, rect, padded, 2000, 1000), [0, 0]);
});

test("a height-capped element letterboxes the viewBox instead of stretching it", () => {
  const capped = { left: 0, top: 0, width: 1000, height: 250 } as DOMRect;
  // Scale 0.25 leaves 250px empty on each side; the image spans x = 250..750.
  assert.deepEqual(clientToCourt(375, 62.5, capped, image, 2000, 1000), [500, 250]);
  assert.deepEqual(clientToCourt(100, 0, capped, image, 2000, 1000), [0, 0]);
});

const STAGES = [
  ["match_segmentation", []], ["court_detection", ["match_segmentation"]], ["shuttle_tracking", ["match_segmentation"]],
  ["pose", ["match_segmentation", "court_detection"]], ["event_detection", ["shuttle_tracking", "pose"]],
].map(([name, dependencies]) => ({ name: name as string, description: "", dependencies: dependencies as string[],
  optionalDependencies: [], usesGemini: false }));

function task(states: Record<string, string>, extra: Partial<PipelineTask> = {}): PipelineTask {
  const names = Object.keys(states);
  return {
    id: "t", matchId: "M", status: "running", currentStage: null, createdAt: "", startedAt: null, finishedAt: null,
    error: null, exitCode: null,
    plan: { planId: "p", matchId: "M", mode: "continue", requestedStages: names, requiredStages: names, stages: [],
      affectedOutsideScope: [], includesGemini: false },
    stageStates: Object.fromEntries(names.map((name) => [name, { status: states[name]!, progress: null, reason: "" }])),
    ...extra,
  };
}

test("court readers include stages that read the court through another stage", () => {
  assert.deepEqual(courtReaders(STAGES), ["pose", "event_detection"]);
});

test("a running task asks for a court check only once its court is detected", () => {
  const readers = courtReaders(STAGES);
  const base = { match_segmentation: "succeeded", court_detection: "succeeded", shuttle_tracking: "running" };
  assert.equal(courtCheck(task({ ...base, court_detection: "running", pose: "queued" }), readers), null);
  assert.deepEqual(courtCheck(task({ ...base, pose: "queued", event_detection: "queued" }), readers),
    { ran: [], upcoming: ["pose", "event_detection"] });
  assert.deepEqual(courtCheck(task({ ...base, pose: "succeeded", event_detection: "running" }), readers),
    { ran: ["pose", "event_detection"], upcoming: [] });
  // A court reused from before, a finished task and an on-demand commentary task ask nothing.
  assert.equal(courtCheck(task({ ...base, court_detection: "skipped", pose: "queued" }), readers), null);
  assert.equal(courtCheck(task({ ...base, pose: "queued" }, { status: "succeeded" }), readers), null);
  assert.equal(courtCheck(task({ commentary: "running" }, { segmentIndex: 3 }), readers), null);
});

test("the court can be saved mid-task once the task's court stage is done", () => {
  assert.ok(courtSaveLock(task({ court_detection: "queued" })));
  assert.ok(courtSaveLock(task({ court_detection: "running" })));
  assert.equal(courtSaveLock(task({ court_detection: "succeeded", pose: "running" })), null);
  assert.equal(courtSaveLock(task({ shuttle_tracking: "running" })), null);
  assert.ok(courtSaveLock(task({ commentary: "running" }, { segmentIndex: 0 })));
  assert.equal(courtSaveLock(task({ court_detection: "queued" }, { status: "failed" })), null);
  assert.equal(courtSaveLock(null), null);
});
