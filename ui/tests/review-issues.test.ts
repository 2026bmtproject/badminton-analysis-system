import { test } from "node:test";
import assert from "node:assert/strict";
import { libraryIssueLines, reviewIssueLines, reviewStateLabel } from "../src/data/reviewIssues";
import type { LocalAnalysisMatch } from "../src/data/pipelineTasks";

const local = (patch: Partial<LocalAnalysisMatch>): LocalAnalysisMatch => ({
  id: "m", completedStages: [], staleStages: {}, unknownStages: [], resultsUpdatedAt: "2026-10-10T03:00:00Z",
  hasSegments: true, analysisStatus: "completed", latestTask: null, ...patch,
});

test("Review states are labelled by the stage that produced them, not the export alias", () => {
  assert.equal(reviewStateLabel("identity"), "球員身分對應");
  assert.equal(reviewStateLabel("audio_signals"), "音訊歡呼訊號");
  assert.equal(reviewStateLabel("shuttle"), "羽球軌跡");
});

test("library issues read in Chinese and fold every unknown-provenance result into one note", () => {
  const lines = reviewIssueLines({
    segments: { status: "unknown", message: "dependency fingerprints were not recorded" },
    court: { status: "unknown", message: "dependency fingerprints were not recorded" },
    events: { status: "available" },
    strokes: { status: "stale", message: "source events are unavailable" },
    identity: { status: "stale", message: "dependency changed: pose, stroke_classification" },
    scores: { status: "error", message: "stage status is failed" },
    commentary: { status: "missing" },
  });
  assert.deepEqual(lines, [
    "球種辨識 已過期，需要重跑",
    "球員身分對應 已過期，需要重跑",
    "比分辨識 資料錯誤（stage status is failed）",
    "舊版結果，無法確認是否最新：回合切割、球場邊界辨識",
  ]);
});

test("with an analysis folder the library trusts its live freshness over the Review snapshot", () => {
  const review = { importedAt: "2026-10-10T04:00:00Z", states: {
    identity: { status: "stale" as const, message: "dependency changed: pose" },
    scores: { status: "error" as const, message: "bad rows" },
  } };
  // Re-run since: the snapshot's stale identity is gone from the live list, only the Review's own error remains.
  assert.deepEqual(libraryIssueLines(local({ unknownStages: ["pose"] }), review),
    ["比分辨識 資料錯誤（bad rows）", "舊版結果，無法確認是否最新：骨架標記"]);
  assert.deepEqual(libraryIssueLines(local({ staleStages: { player_identity: ["pose"] } }), review),
    ["球員身分對應 已過期，需要重跑", "比分辨識 資料錯誤（bad rows）"]);
});

test("a Review imported before the newest result says it is behind instead of listing old problems", () => {
  const review = { importedAt: "2026-10-10T02:00:00Z", states: { events: { status: "stale" as const, message: "dependency changed: score_recognition" } } };
  assert.deepEqual(libraryIssueLines(local({}), review), ["回看不是最新的分析結果，按「匯入結果」更新"]);
  assert.deepEqual(libraryIssueLines(null, review), ["擊球偵測 已過期，需要重跑"]);
});
