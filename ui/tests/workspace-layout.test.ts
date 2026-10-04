import assert from "node:assert/strict";
import test from "node:test";
import { defaultWorkspaceLayout, parseWorkspaceLayout, sanitizePanel, useWorkspaceLayout } from "../src/state/workspaceLayout";
import { availableTimelineModes } from "../src/components/timeline/timelineModeRegistry";
import type { MatchCapabilities } from "../src/domain/models";

test("invalid persisted workspace state falls back without breaking Review", () => {
  assert.deepEqual(parseWorkspaceLayout("not-json"), defaultWorkspaceLayout());
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify({ version: 9 })), defaultWorkspaceLayout());
});

test("persisted panels are constrained to reachable, readable bounds", () => {
  const fallback = defaultWorkspaceLayout().panels.timeline;
  const panel = sanitizePanel({ x: 9, y: -4, width: 0.01, height: 2, alpha: 0.1, collapsed: true }, fallback);
  assert.equal(panel.width, 0.18);
  assert.equal(panel.height, 0.92);
  assert.ok(Math.abs(panel.x - 0.82) < Number.EPSILON);
  assert.equal(panel.y, 0);
  assert.equal(panel.alpha, 0.78);
  assert.equal(panel.collapsed, true);
});

test("default workspace exposes exactly two persistent panels and truthful modes", () => {
  const layout = defaultWorkspaceLayout();
  assert.deepEqual(Object.keys(layout.panels).sort(), ["analysis", "timeline"]);
  assert.equal(layout.timelineMode, "rally");
  assert.equal(layout.analysisView, "analysis");
});

test("layout reset restores every persisted presentation field", () => {
  const { layout, reset } = useWorkspaceLayout();
  layout.timelineMode = "score";
  layout.analysisView = "court";
  layout.analysisSide = "left";
  layout.analysisDockWidth = 420;
  layout.timelineDockHeight = 360;
  layout.panels.analysis = {
    x: 0.1,
    y: 0.2,
    width: 0.5,
    height: 0.4,
    collapsed: true,
    alpha: 0.8,
    presentation: "detached",
  };

  reset();

  assert.deepEqual(JSON.parse(JSON.stringify(layout)), defaultWorkspaceLayout());
});

test("timeline mode registry exposes only renderers backed by match capabilities", () => {
  const capabilities: MatchCapabilities = { score: true, stroke: false, identity: false, cheer: true, highlight: false, commentary: false, court: false, pose: false, shuttle: false };
  assert.deepEqual(availableTimelineModes(capabilities).map((mode) => mode.id), ["rally", "score", "cheer"]);
});

test("main Review consolidates audio-derived Highlight into Cheer", () => {
  const capabilities: MatchCapabilities = { score: true, stroke: true, identity: false, cheer: true, highlight: true, commentary: true, court: true, pose: false, shuttle: false };
  const modes = availableTimelineModes(capabilities).map((mode) => mode.id);
  assert.deepEqual(modes, ["rally", "stroke", "score", "commentary", "cheer"]);
  assert.equal(modes.some((mode) => String(mode) === "highlight"), false);
});
