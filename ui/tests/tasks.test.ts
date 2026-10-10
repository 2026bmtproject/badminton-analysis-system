import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formatDuration, formatLogLine, formatTaskTime, ranStages, ranStagesSummary, stageElapsed, taskProgress, type PipelineTask,
} from "../src/data/pipelineTasks";

function task(stageStates: PipelineTask["stageStates"], currentStage: string | null = null): PipelineTask {
  return {
    id: "a".repeat(32), matchId: "m", status: "running", stageStates, currentStage,
    plan: { planId: "p", matchId: "m", mode: "continue", requestedStages: [], requiredStages: [], stages: [],
      affectedOutsideScope: [], includesGemini: false },
    createdAt: "2026-10-10T03:00:00Z", startedAt: "2026-10-10T03:00:00Z", finishedAt: null, error: null, exitCode: null,
  };
}
const stage = (status: string, progress: number | null = null) => ({ status, progress, reason: "" });

test("task progress counts only the stages the task runs, not reused ones", () => {
  const value = task({
    match_segmentation: stage("skipped"), court_detection: stage("skipped"),
    event_detection: stage("succeeded", 1), stroke_classification: stage("running", .5), player_identity: stage("queued"),
  }, "stroke_classification");
  assert.deepEqual(ranStages(value).map(([name]) => name), ["event_detection", "stroke_classification", "player_identity"]);
  const progress = taskProgress(value);
  assert.equal(progress.done, 1);
  assert.equal(progress.total, 3);
  assert.equal(progress.fraction, 1.5 / 3);
});

test("a row summary names the first ran stages and counts the rest", () => {
  const value = task({ a: stage("skipped"), b: stage("succeeded"), c: stage("succeeded"), d: stage("succeeded"), e: stage("failed") });
  assert.equal(ranStagesSummary(value, name => name.toUpperCase()), "B、C +2");
  assert.equal(ranStagesSummary(task({ a: stage("succeeded") }), name => name), "a");
});

test("durations switch to hours only past an hour", () => {
  assert.equal(formatDuration(54), "0:54");
  assert.equal(formatDuration(3725), "1:02:05");
  assert.equal(stageElapsed({ ...stage("running"), startedAt: "2026-10-10T03:00:00Z" }, Date.parse("2026-10-10T03:01:30Z")), "1:30");
  assert.equal(stageElapsed(stage("queued"), Date.now()), null);
});

test("task times read as today, yesterday or a date", () => {
  const now = new Date(2026, 9, 10, 15, 0).getTime();
  assert.equal(formatTaskTime(new Date(2026, 9, 10, 3, 9).toISOString(), now), "今天 03:09");
  assert.equal(formatTaskTime(new Date(2026, 9, 9, 22, 14).toISOString(), now), "昨天 22:14");
  assert.equal(formatTaskTime(new Date(2026, 9, 8, 14, 2).toISOString(), now), "10/08 14:02");
  assert.equal(formatTaskTime(new Date(2025, 11, 31, 9, 0).toISOString(), now), "2025/12/31 09:00");
});

test("log lines show their UTC stamp as local time and leave other lines alone", () => {
  const stamp = new Date(2026, 9, 10, 3, 22, 14).toISOString();
  assert.equal(formatLogLine(`${stamp} pose: started`), "03:22:14  pose: started");
  assert.equal(formatLogLine("worker exited"), "worker exited");
});
