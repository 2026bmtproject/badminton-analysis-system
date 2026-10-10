import { test } from "node:test";
import assert from "node:assert/strict";
import {
  cancelPipelineTask, formatDuration, formatLogLine, formatTaskTime, ranStages, ranStagesSummary, retryTask, stageElapsed, taskProgress, taskStatusLabel, type PipelineTask,
} from "../src/data/pipelineTasks";
import { sameReview, type MatchModel, type RallyModel } from "../src/domain/models";

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

test("a one-segment commentary task names its segment and retries only that segment", async () => {
  const value = { ...task({ commentary: stage("failed") }), status: "failed" as const, segmentIndex: 4 };
  value.plan = { ...value.plan, requestedStages: ["commentary"] };
  assert.equal(ranStagesSummary(value, name => name === "commentary" ? "賽評生成" : name), "賽評生成（片段 005）");
  const calls: { url: string; body: unknown }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(value), { status: 201 });
  }) as typeof fetch;
  try { await retryTask(value); }
  finally { globalThis.fetch = original; }
  // Replanning the commentary stage would generate every Rally of the match.
  assert.deepEqual(calls, [{ url: "/api/pipeline/commentary", body: { matchId: "m", segmentIndex: 4 } }]);
});

test("cancelling posts to the task's own cancel route and reads back as cancelled", async () => {
  const value = { ...task({ commentary: stage("cancelled") }), status: "cancelled" as const };
  const calls: { url: string; method?: string; body: string }[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method, body: String(init?.body) });
    return new Response(JSON.stringify(value), { status: 200 });
  }) as typeof fetch;
  try { assert.equal((await cancelPipelineTask(value.id)).status, "cancelled"); }
  finally { globalThis.fetch = original; }
  // The service rejects an empty POST body, so the request carries an empty JSON object.
  assert.deepEqual(calls, [{ url: `/api/pipeline/tasks/${value.id}/cancel`, method: "POST", body: "{}" }]);
  assert.equal(taskStatusLabel("cancelled"), "已取消");
});

test("a refreshed export of the same Review keeps playback; a different one does not", () => {
  const rally = (id: number, start: number) => ({ id, start, end: start + 5 }) as RallyModel;
  const review = (video: string, rallies: RallyModel[]) => ({ video, rallies }) as MatchModel;
  const open = review("v.mp4", [rally(0, 1), rally(1, 20)]);
  assert.equal(sameReview(open, review("v.mp4", [rally(0, 1), rally(1, 20)])), true);
  assert.equal(sameReview(open, open), false);
  assert.equal(sameReview(null, open), false);
  assert.equal(sameReview(open, review("w.mp4", [rally(0, 1), rally(1, 20)])), false);
  assert.equal(sameReview(open, review("v.mp4", [rally(0, 1), rally(1, 21)])), false);
  assert.equal(sameReview(open, review("v.mp4", [rally(0, 1)])), false);
});
