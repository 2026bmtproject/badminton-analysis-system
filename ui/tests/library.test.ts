import { test } from "node:test";
import assert from "node:assert/strict";
import { mergeLibrary } from "../src/data/library";
import type { LocalAnalysisMatch } from "../src/data/pipelineTasks";

test("library merges local analysis and Review by canonical match ID", () => {
  const local: LocalAnalysisMatch = { id: "Kunlavut", completedStages: [], staleStages: {}, unknownStages: [], resultsUpdatedAt: null, hasSegments: false, analysisStatus: "unanalysed", latestTask: null };
  const rows = mergeLibrary(
    [{ id: "match:Kunlavut", name: "Kunlavut", url: "/matches/a.json", kind: "match" },
      { id: "demo", name: "Kunlavut", url: "/generated/demo.json", kind: "fixture" }],
    [local], [{ id: "Kunlavut", available: false, reason: "missing segments" }],
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].id, "match:Kunlavut");
  assert.equal(rows[0].local, local);
  assert.equal(rows[0].review?.url, "/matches/a.json");
  assert.equal(rows[0].candidate?.reason, "missing segments");
  assert.equal(rows[1].kind, "fixture");
});

test("local match without segments stays in the library and can open analysis", () => {
  const rows = mergeLibrary([], [{ id: "raw", completedStages: [], staleStages: {}, unknownStages: [], resultsUpdatedAt: null, hasSegments: false, analysisStatus: "unanalysed", latestTask: null }], []);
  assert.equal(rows[0].id, "match:raw");
  assert.equal(rows[0].review, null);
  assert.equal(rows[0].local?.hasSegments, false);
});
