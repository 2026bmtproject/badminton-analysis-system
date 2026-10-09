import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { defaultWorkspaceLayout, parseWorkspaceLayout } from "../src/state/workspaceLayout";
import {
  constrainPanel,
  movePanel,
  PANEL_MAX_HEIGHT_RATIO,
  PANEL_MAX_WIDTH_RATIO,
  resizePanel,
} from "../src/state/workspaceGeometry";

const bounds = { width: 1000, height: 800 };

test("1. obsolete visible movement controller is removed from both shared windows", () => {
  const component = readFileSync("src/components/workspace/WorkspaceWindow.vue", "utf8");
  assert.doesNotMatch(component, /移動面板|向左移動|向右移動|向上移動|向下移動|nudge\(/);
  assert.doesNotMatch(component, />•••</);
});

test("2. shared direct dragging changes position and stays within workspace bounds", () => {
  const panel = defaultWorkspaceLayout().panels.timeline;
  const moved = movePanel(panel, 2400, -2400, bounds);
  assert.equal(moved.x, 1 - moved.width);
  assert.equal(moved.y, 0);
});

test("3. Timeline resize supports both dimensions and clamps minimums", () => {
  const panel = defaultWorkspaceLayout().panels.timeline;
  const larger = resizePanel(panel, 100, 80, bounds);
  assert.ok(larger.width > panel.width);
  assert.ok(larger.height > panel.height);
  const minimum = resizePanel(panel, -5000, -5000, bounds);
  assert.equal(minimum.width, 0.22);
  assert.equal(minimum.height, 0.12);
});

test("4. Analysis uses the shared resizer and remains within maximum dimensions", () => {
  const panel = defaultWorkspaceLayout().panels.analysis;
  const maximum = resizePanel(panel, 5000, 5000, bounds);
  assert.equal(maximum.width, PANEL_MAX_WIDTH_RATIO);
  assert.equal(maximum.height, PANEL_MAX_HEIGHT_RATIO);
  assert.equal(maximum.x, 1 - PANEL_MAX_WIDTH_RATIO);
});

test("5. resized and repositioned dimensions survive versioned persistence", () => {
  const layout = defaultWorkspaceLayout();
  layout.panels.timeline = movePanel(
    resizePanel(layout.panels.timeline, 80, 40, bounds),
    50,
    -20,
    bounds,
  );
  const restored = parseWorkspaceLayout(JSON.stringify(layout));
  assert.deepEqual(restored.panels.timeline, layout.panels.timeline);
});

test("6. collapse and expand preserve the user-defined expanded dimensions", () => {
  const resized = resizePanel(defaultWorkspaceLayout().panels.timeline, 80, 40, bounds);
  const collapsed = constrainPanel({ ...resized, collapsed: true }, bounds);
  const expanded = constrainPanel({ ...collapsed, collapsed: false }, bounds);
  assert.equal(expanded.width, resized.width);
  assert.equal(expanded.height, resized.height);
});

test("6b. a collapsed capsule is clamped by its own size and re-clamped on expand", () => {
  const panel = { ...defaultWorkspaceLayout().fullscreenPanels.analysis, collapsed: true };
  const capsule = { width: 120, height: 36 };
  const moved = movePanel(panel, 2400, 2400, bounds, capsule);
  assert.equal(moved.x, 1 - capsule.width / bounds.width);
  assert.equal(moved.y, 1 - capsule.height / bounds.height);
  const expanded = constrainPanel({ ...moved, collapsed: false }, bounds);
  assert.equal(expanded.x, 1 - expanded.width);
  assert.equal(expanded.y, 1 - expanded.height);
});

test("7. smaller workspace constrains dimensions and keeps the header reachable", () => {
  const panel = { ...defaultWorkspaceLayout().panels.analysis, x: 0.9, y: 0.9, width: 0.9, height: 0.9 };
  const constrained = constrainPanel(panel, { width: 920, height: 500 });
  assert.ok(constrained.x + constrained.width <= 1);
  assert.ok(constrained.y + constrained.height <= 1);
  assert.ok(constrained.width <= PANEL_MAX_WIDTH_RATIO);
  assert.ok(constrained.height <= PANEL_MAX_HEIGHT_RATIO);
});

test("8. transparency changes only the rgba panel backgrounds", () => {
  const component = readFileSync("src/components/workspace/WorkspaceWindow.vue", "utf8");
  const styles = readFileSync("src/styles/floating-workspace.css", "utf8");
  assert.match(component, /aria-label="背景透明度"/);
  assert.match(component, /alpha: 1 - Number/);
  assert.match(styles, /background: rgb\(13 16 17 \/ var\(--workspace-panel-alpha\)\)/);
  assert.doesNotMatch(styles, /\.workspace-window\s*\{[^}]*opacity:/s);
});

test("9. settings contains transparency without duplicate movement actions", () => {
  const component = readFileSync("src/components/workspace/WorkspaceWindow.vue", "utf8");
  assert.match(component, /AppIcon name="sliders"/);
  assert.match(component, /背景透明度/);
  assert.doesNotMatch(component, /workspace-window__keyboard-move|→|←|↑|↓/);
});

test("10. stacked responsive panels hide resizing and avoid fixed inner overflow", () => {
  const styles = readFileSync("src/styles/floating-workspace.css", "utf8");
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*\.workspace-window__resize-grip \{ display: none; \}/);
  assert.match(styles, /\.workspace-window__body > \.intelligence-timeline \{ min-width: 0; \}/);
  assert.doesNotMatch(styles, /min-width: 680px/);
});
