import assert from "node:assert/strict";
import test from "node:test";
import { defaultWorkspaceLayout, parseWorkspaceLayout, sanitizePanel, useWorkspaceLayout } from "../src/state/workspaceLayout";
import { availableTimelineModes } from "../src/components/timeline/timelineModeRegistry";
import type { MatchCapabilities } from "../src/domain/models";
import { FULLSCREEN_SNAP_DISTANCE_PX, fullscreenHomePanel, fullscreenSnapCandidate } from "../src/state/workspaceGeometry";

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
  assert.deepEqual(Object.keys(layout.fullscreenPanels).sort(), ["analysis", "timeline"]);
  assert.equal(layout.fullscreenPanels.timeline.presentation, "detached");
  assert.equal(layout.timelineMode, "rally");
  assert.equal(layout.analysisView, "analysis");
});

test("normal and fullscreen floating settings persist independently", () => {
  const source = defaultWorkspaceLayout();
  source.panels.analysis.alpha = 0.81;
  source.fullscreenPanels.analysis.alpha = 0.88;
  source.fullscreenPanels.analysis.x = 0.32;
  source.fullscreenPanels.timeline.y = 0.45;
  const restored = parseWorkspaceLayout(JSON.stringify(source));
  assert.equal(restored.panels.analysis.alpha, 0.81);
  assert.equal(restored.fullscreenPanels.analysis.alpha, 0.88);
  assert.equal(restored.fullscreenPanels.analysis.x, 0.32);
  assert.equal(restored.fullscreenPanels.timeline.y, 0.45);
  const v2 = { ...source, version: 2, fullscreenPanels: undefined };
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(v2)).fullscreenPanels, defaultWorkspaceLayout().fullscreenPanels);
});

test("fullscreen home restores position without changing size, opacity, or normal layout", () => {
  const layout = defaultWorkspaceLayout();
  const normal = { ...layout.panels.analysis };
  const current = { ...layout.fullscreenPanels.analysis, x: 0.31, y: 0.42, width: 0.28, height: 0.48, alpha: 0.81 };
  const home = fullscreenHomePanel("analysis", current, { width: 1920, height: 1080 });
  assert.equal(home.x, 1 - current.width); // Wider windows remain entirely on screen.
  assert.equal(home.y, layout.fullscreenPanels.analysis.y);
  assert.equal(home.width, current.width);
  assert.equal(home.height, current.height);
  assert.equal(home.alpha, current.alpha);
  assert.deepEqual(layout.panels.analysis, normal);
});

test("fullscreen drag snaps only near its own home position", () => {
  const bounds = { width: 1920, height: 1080 };
  const panel = defaultWorkspaceLayout().fullscreenPanels.timeline;
  const near = { ...panel, x: panel.x + 40 / bounds.width, y: panel.y - 35 / bounds.height };
  const far = { ...panel, x: panel.x + (FULLSCREEN_SNAP_DISTANCE_PX + 20) / bounds.width };
  assert.deepEqual(fullscreenSnapCandidate("timeline", near, bounds), panel);
  assert.equal(fullscreenSnapCandidate("timeline", far, bounds), null);
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
