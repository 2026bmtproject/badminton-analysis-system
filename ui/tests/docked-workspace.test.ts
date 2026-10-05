import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  constrainAnalysisDockWidth,
  constrainTimelineDockHeight,
  defaultWorkspaceLayout,
  parseWorkspaceLayout,
} from "../src/state/workspaceLayout";

const source = (path: string) => readFileSync(path, "utf8");
const review = source("src/pages/ReviewPage.vue");
const windowComponent = source("src/components/workspace/WorkspaceWindow.vue");
const styles = source("src/styles/floating-workspace.css");

test("1. fresh workspace docks Analysis right and Timeline bottom", () => {
  const layout = defaultWorkspaceLayout();
  assert.equal(layout.version, 3);
  assert.equal(layout.analysisSide, "right");
  assert.equal(layout.panels.analysis.presentation, "docked");
  assert.equal(layout.panels.timeline.presentation, "docked");
});

test("2. Analysis dock width is directly adjustable within useful limits", () => {
  assert.equal(constrainAnalysisDockWidth(100), 180);
  assert.equal(constrainAnalysisDockWidth(390), 390);
  assert.equal(constrainAnalysisDockWidth(900), 460);
  assert.match(styles, /--analysis-dock-width/);
  assert.match(windowComponent, /調整分析寬度/);
});

test("3. Timeline dock height changes independently of temporal viewport", () => {
  assert.equal(constrainTimelineDockHeight(10), 96);
  assert.equal(constrainTimelineDockHeight(280), 280);
  assert.equal(constrainTimelineDockHeight(900), 380);
  const resizeBody = windowComponent.slice(windowComponent.indexOf("function resizeDock"), windowComponent.indexOf("function endDockResize"));
  assert.doesNotMatch(resizeBody, /renderViewport|seek|currentTime/);
});

test("4. collapsed Analysis releases its dock width and preserves stored size", () => {
  assert.match(styles, /review-workspace-stage--analysis-collapsed\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s);
  assert.match(windowComponent, /class="workspace-window__edge-tab"/);
  assert.doesNotMatch(styles, /writing-mode:\s*vertical-rl/);
  assert.match(review, /layout\.panels\.analysis\.collapsed/);
  assert.doesNotMatch(windowComponent.slice(windowComponent.indexOf("function toggleCollapsed"), windowComponent.indexOf("function handleResize")), /dockSize/);
});

test("5. collapsed Timeline remains a compact real-data Match Rail", () => {
  assert.match(styles, /review-workspace-stage--timeline-collapsed[^}]*64px/);
  assert.match(review, /:compact-rail="panels\.timeline\.presentation === 'docked' && panels\.timeline\.collapsed"/);
  assert.match(source("src/components/ReviewTimeline.vue"), /props\.compactRail\s*\? track === "rally"/);
});

test("6. Analysis detach reuses the shared draggable and resizable window", () => {
  assert.match(windowComponent, /changePresentation\('detached'\)/);
  assert.match(windowComponent, /startDrag/);
  assert.match(windowComponent, /startResize/);
  assert.equal((review.match(/<AnalysisWindow /g) ?? []).length, 1);
});

test("7. Analysis re-dock explicitly ignores detached geometry for dock layout", () => {
  assert.match(windowComponent, /停靠左側/);
  assert.match(windowComponent, /停靠右側/);
  assert.match(windowComponent, /props\.panel\.presentation === "detached" \? \(\{/);
  assert.match(styles, /\.workspace-window--docked[\s\S]*position: relative/);
});

test("8. Timeline detach leaves temporal viewport owned by ReviewTimeline", () => {
  assert.equal((review.match(/<ReviewTimeline /g) ?? []).length, 1);
  assert.doesNotMatch(review.slice(review.indexOf("function setPresentation"), review.indexOf("function setAnalysisSide")), /timelineMode|currentTime|viewport|zoom/);
});

test("9. transparency follows the selected panel layout", () => {
  assert.match(windowComponent, /<label>背景透明度<input/);
  assert.match(windowComponent, /"--workspace-panel-alpha": visiblePanel\.value\.alpha/);
});

test("10. Analysis side preference is finite and persisted in schema", () => {
  const saved = defaultWorkspaceLayout();
  saved.analysisSide = "left";
  assert.equal(parseWorkspaceLayout(JSON.stringify(saved)).analysisSide, "left");
  assert.match(review, /v-model="layout\.analysisSide"/);
});

test("11. v2 presentation modes and dock sizes survive reload parsing", () => {
  const saved = defaultWorkspaceLayout();
  saved.analysisDockWidth = 410;
  saved.timelineDockHeight = 310;
  saved.panels.analysis.presentation = "detached";
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(saved)), saved);
});

test("12. old floating v1 migrates safely while invalid state falls back", () => {
  const old = { ...defaultWorkspaceLayout(), version: 1 };
  delete (old as Partial<typeof old>).analysisSide;
  delete (old as Partial<typeof old>).analysisDockWidth;
  delete (old as Partial<typeof old>).timelineDockHeight;
  for (const panel of Object.values(old.panels)) delete (panel as Partial<typeof panel>).presentation;
  const migrated = parseWorkspaceLayout(JSON.stringify(old));
  assert.equal(migrated.version, 3);
  assert.equal(migrated.panels.analysis.presentation, "detached");
  assert.equal(migrated.panels.timeline.presentation, "detached");
  assert.deepEqual(parseWorkspaceLayout("broken"), defaultWorkspaceLayout());
});

test("13. docked and detached Timeline retain zoom, pan and hover implementation", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  assert.match(timeline, /zoomTimelineViewport/);
  assert.match(timeline, /panTimelineViewport/);
  assert.match(timeline, /timelineHoverPreview/);
  assert.doesNotMatch(windowComponent, /renderViewport/);
});

test("14. docked and detached Analysis share active context, Court and score", () => {
  assert.equal((review.match(/<AnalysisWindow /g) ?? []).length, 1);
  assert.match(review, /:active-id="activeId"/);
  assert.match(review, /:current-score="workspace\.currentScore\.value"/);
  assert.match(source("src/components/workspace/AnalysisWindow.vue"), /RallyCourtMap/);
});

test("15. responsive layout stacks before docks can crush Video", () => {
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*review-workspace-stage \{ display: flex/);
  assert.match(styles, /review-workspace-stage > \.player \{ order: 1;/);
  assert.match(styles, /workspace-window--analysis \{ order: 2;/);
  assert.match(styles, /workspace-window--timeline \{ order: 3;/);
  assert.match(styles, /grid-template-columns: minmax\(0, 1fr\) var\(--analysis-dock-width/);
  assert.doesNotMatch(styles, /min-width: 680px/);
});
