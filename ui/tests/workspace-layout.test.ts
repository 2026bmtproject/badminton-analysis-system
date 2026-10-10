import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { defaultWorkspaceLayout, parseWorkspaceLayout, sanitizePanel } from "../src/state/workspaceLayout";
import { availableTimelineModes } from "../src/components/timeline/timelineModeRegistry";
import type { MatchCapabilities } from "../src/domain/models";
import { FULLSCREEN_TIMELINE_HEIGHT_PX, fittedFullscreenTimeline, fullscreenHomePanel } from "../src/state/workspaceGeometry";

test("invalid persisted workspace state falls back without breaking Review", () => {
  assert.deepEqual(parseWorkspaceLayout("not-json"), defaultWorkspaceLayout());
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify({ version: 9 })), defaultWorkspaceLayout());
});

test("persisted panels are constrained to reachable, readable bounds", () => {
  const fallback = defaultWorkspaceLayout().panels.timeline;
  const panel = sanitizePanel({ x: 9, y: -4, width: 0.01, height: 2, alpha: 0.05, collapsed: true }, fallback);
  assert.equal(panel.width, 0.18);
  assert.equal(panel.height, 0.92);
  assert.ok(Math.abs(panel.x - 0.82) < Number.EPSILON);
  assert.equal(panel.y, 0);
  assert.equal(panel.alpha, 0.2);
  assert.equal(panel.collapsed, true);
  assert.equal(sanitizePanel({ alpha: 1.4 }, fallback).alpha, 1);
});

test("default workspace exposes exactly two persistent panels and truthful modes", () => {
  const layout = defaultWorkspaceLayout();
  assert.deepEqual(Object.keys(layout.panels).sort(), ["analysis", "timeline"]);
  assert.deepEqual(Object.keys(layout.fullscreenPanels).sort(), ["analysis", "timeline"]);
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

test("unresized legacy fullscreen timelines adopt the current default height; moved ones keep their spot", () => {
  const home = defaultWorkspaceLayout().fullscreenPanels.timeline;
  for (const spot of [{ y: 0.66, height: 0.25 }, { y: 0.72, height: 0.19 }]) {
    const legacy = defaultWorkspaceLayout();
    legacy.fullscreenPanels.timeline = { ...legacy.fullscreenPanels.timeline, x: 0.1, ...spot };
    const migrated = parseWorkspaceLayout(JSON.stringify(legacy)).fullscreenPanels.timeline;
    assert.equal(migrated.y, home.y);
    assert.equal(migrated.height, home.height);
    assert.equal(migrated.x, 0.1);
    legacy.fullscreenPanels.timeline.y = 0.5;
    const moved = parseWorkspaceLayout(JSON.stringify(legacy)).fullscreenPanels.timeline;
    assert.equal(moved.y, 0.5);
    assert.equal(moved.height, home.height);
  }
  const resized = defaultWorkspaceLayout();
  resized.fullscreenPanels.timeline.height = 0.3;
  assert.equal(parseWorkspaceLayout(JSON.stringify(resized)).fullscreenPanels.timeline.height, 0.3);
});

test("untouched fullscreen timeline fits a fixed pixel height above its default bottom edge", () => {
  const home = defaultWorkspaceLayout().fullscreenPanels.timeline;
  const bottom = home.y + home.height;
  for (const height of [900, 1080, 1440]) {
    const fitted = fittedFullscreenTimeline(home, height);
    assert.equal(Math.round(fitted.height * height), FULLSCREEN_TIMELINE_HEIGHT_PX);
    assert.ok(Math.abs(fitted.y + fitted.height - bottom) < 1e-9);
  }
  const moved = { ...home, y: 0.3 };
  assert.equal(fittedFullscreenTimeline(moved, 1440).y, 0.3);
  const resized = { ...home, height: 0.4 };
  assert.deepEqual(fittedFullscreenTimeline(resized, 1440), resized);
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

test("timeline mode registry exposes only renderers backed by match capabilities", () => {
  const capabilities: MatchCapabilities = { score: true, stroke: false, identity: false, cheer: true, highlight: false, commentary: false, court: false, pose: false, shuttle: false };
  assert.deepEqual(availableTimelineModes(capabilities).map((mode) => mode.id), ["rally", "score", "cheer"]);
});

test("main Review consolidates audio-derived Highlight into Cheer", () => {
  const capabilities: MatchCapabilities = { score: true, stroke: true, identity: false, cheer: true, highlight: true, commentary: true, court: true, pose: false, shuttle: false };
  const modes = availableTimelineModes(capabilities).map((mode) => mode.id);
  // Commentary is read in the Analysis window; the rally lane only marks where it exists.
  assert.deepEqual(modes, ["rally", "stroke", "score", "cheer"]);
  assert.equal(modes.some((mode) => String(mode) === "highlight"), false);
});

test("the Analysis stroke list fold persists and defaults open", () => {
  assert.equal(defaultWorkspaceLayout().strokeListCollapsed, false);
  const folded = { ...defaultWorkspaceLayout(), strokeListCollapsed: true };
  assert.equal(parseWorkspaceLayout(JSON.stringify(folded)).strokeListCollapsed, true);
  assert.equal(parseWorkspaceLayout(JSON.stringify({ ...folded, strokeListCollapsed: "yes" })).strokeListCollapsed, false);
  const detail = readFileSync("src/components/inspector/RallyDetail.vue", "utf8");
  assert.match(detail, /:aria-expanded="!strokesCollapsed"/);
  assert.match(detail, /v-show="!strokesCollapsed"/);
});

test("folding the stroke list shrinks the floating Analysis window to its content", () => {
  const review = readFileSync("src/pages/ReviewPage.vue", "utf8");
  const windowSource = readFileSync("src/components/workspace/WorkspaceWindow.vue", "utf8");
  const styles = readFileSync("src/styles/floating-workspace.css", "utf8");
  assert.match(review, /:fit-content="layout\.analysisView === 'analysis' && layout\.strokeListCollapsed"/);
  // The stored height stays the cap, so unfolding returns the window to the size the user set.
  assert.match(windowSource, /height: visiblePanel\.value\.collapsed \|\| props\.fitContent \? "auto"/);
  assert.match(windowSource, /maxHeight: props\.fitContent && !visiblePanel\.value\.collapsed/);
  assert.match(styles, /\.workspace-window--fit \.rally-detail \{ position: relative; inset: auto; \}/);
});
