import assert from "node:assert/strict";
import test from "node:test";
import {
  constrainAnalysisDockWidth,
  constrainTimelineDockHeight,
  defaultWorkspaceLayout,
  parseWorkspaceLayout,
} from "../src/state/workspaceLayout";

test("1. the default layout is version 3 and stores no Analysis side", () => {
  const layout = defaultWorkspaceLayout();
  assert.equal(layout.version, 3);
  assert.ok(!("analysisSide" in layout));
});

test("2. Analysis dock width is directly adjustable within useful limits", () => {
  assert.equal(constrainAnalysisDockWidth(100), 180);
  assert.equal(constrainAnalysisDockWidth(390), 390);
  assert.equal(constrainAnalysisDockWidth(900), 460);
});

test("3. Timeline dock height changes independently of temporal viewport", () => {
  assert.equal(constrainTimelineDockHeight(10), 96);
  assert.equal(constrainTimelineDockHeight(280), 280);
  assert.equal(constrainTimelineDockHeight(900), 380);
});

test("4. docked windows cannot collapse; only fullscreen windows fold away", () => {
  // A docked collapse stored by an earlier version must not trap a window closed with no control to reopen it.
  const stored = defaultWorkspaceLayout();
  stored.panels.analysis.collapsed = true;
  stored.panels.timeline.collapsed = true;
  stored.fullscreenPanels.analysis.collapsed = true;
  const parsed = parseWorkspaceLayout(JSON.stringify(stored));
  assert.equal(parsed.panels.analysis.collapsed, false);
  assert.equal(parsed.panels.timeline.collapsed, false);
  assert.equal(parsed.fullscreenPanels.analysis.collapsed, true);
});

test("5e. fullscreen docks the timeline under the video or floats it over it; Analysis always floats", () => {
  assert.equal(defaultWorkspaceLayout().fullscreenTimelineDocked, false, "fullscreen floats until asked to dock");
  const docked = { ...defaultWorkspaceLayout(), fullscreenTimelineDocked: true };
  assert.equal(parseWorkspaceLayout(JSON.stringify(docked)).fullscreenTimelineDocked, true);
  assert.equal(parseWorkspaceLayout(JSON.stringify({ ...docked, fullscreenTimelineDocked: "yes" })).fullscreenTimelineDocked, false);
});

test("10. a stored Analysis side is dropped", () => {
  const saved = { ...defaultWorkspaceLayout(), analysisSide: "left" };
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(saved)), defaultWorkspaceLayout());
});

test("11. dock sizes survive reload parsing and a stored presentation is dropped", () => {
  const saved = defaultWorkspaceLayout();
  saved.analysisDockWidth = 410;
  saved.timelineDockHeight = 310;
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(saved)), saved);
  const legacy = JSON.parse(JSON.stringify(saved));
  legacy.panels.analysis.presentation = "detached";
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(legacy)), saved);
});

test("12. a layout stored by an older version or unreadable falls back to the defaults", () => {
  const old = { ...defaultWorkspaceLayout(), version: 2, timelineMode: "stroke" };
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(old)), defaultWorkspaceLayout());
  assert.deepEqual(parseWorkspaceLayout("broken"), defaultWorkspaceLayout());
});
