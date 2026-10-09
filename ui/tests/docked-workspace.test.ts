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

test("1. normal mode always docks Analysis right and Timeline bottom; fullscreen always floats", () => {
  const layout = defaultWorkspaceLayout();
  assert.equal(layout.version, 3);
  assert.ok(!("analysisSide" in layout));
  assert.doesNotMatch(source("src/state/workspaceLayout.ts"), /presentation/);
  assert.match(windowComponent, /const presentation = computed\(\(\) => props\.fullscreen \? "detached" : "docked"\)/);
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

test("5. collapsed Timeline shrinks to its header with a single expand control", () => {
  assert.match(styles, /review-workspace-stage--timeline-collapsed\s*\{[^}]*grid-template-rows:\s*minmax\(320px, 1fr\) auto/);
  assert.doesNotMatch(review, /compact-rail|expandTimeline/);
  assert.doesNotMatch(source("src/components/ReviewTimeline.vue"), /compactRail|timeline-rail-expand|emit\("expand"\)/);
  assert.match(windowComponent, /<div v-show="!panel\.collapsed" class="workspace-window__body">/);
  assert.equal((windowComponent.match(/@click="toggleCollapsed"/g) ?? []).length, 2, "header button plus the docked Analysis edge tab");
});

test("5b. Timeline merges mode, player controls and window actions into one toolbar, at the bottom in fullscreen", () => {
  assert.match(review, / toolbar :toolbar-bottom="fullscreen"/);
  assert.match(review, /<template #header>[\s\S]*<div v-if="controlsInTimeline" ref="playerControlsHost"[\s\S]*<\/template>/);
  assert.match(review, /:controls-target="controlsInTimeline \? playerControlsHost : null"/);
  assert.match(review, /const controlsInTimeline = computed\(\(\) => !match\.value\.layoutOnly && \(fullscreen\.value \|\| viewportWidth\.value > 1_100\)\)/, "the stacked layout puts Analysis between video and timeline, so the player keeps its bar");
  assert.match(styles, /\.workspace-window--toolbar \.workspace-window__header \{ min-height: 36px;/);
  assert.match(styles, /\.workspace-window--toolbar-bottom:not\(\.workspace-window--collapsed\) \.workspace-window__header \{ order: 2;/);
  assert.doesNotMatch(windowComponent, /collapsible|\$slots\.footer/);
  assert.match(source("src/components/ReviewPlayer.vue"), /<Teleport :to="controlsTarget \?\? 'body'" :disabled="!controlsTarget">/);
  assert.doesNotMatch(source("src/styles/desktop-shell.css"), /:fullscreen \.controls/);
});

test("5d. a collapsed docked Timeline keeps the player controls in its toolbar", () => {
  assert.match(styles, /\.workspace-window--docked\.workspace-window--toolbar\.workspace-window--collapsed \.workspace-window__header-slot \{ display: flex; \}/);
  assert.match(styles, /\.workspace-window--docked\.workspace-window--toolbar\.workspace-window--collapsed \.workspace-window__header-slot > :not\(\.workspace-window__player-controls\) \{ display: none; \}/);
  assert.match(windowComponent, /closest\("button,select,input,label,a,\.workspace-window__header-slot"\)/, "clicking the controls must not expand the collapsed timeline");
  assert.match(styles, /\.workspace-window--timeline \.workspace-window__dock-resize \{ top: -6px;[^}]*height: 10px;/, "the dock resize strip clears the 28px toolbar controls");
});

test("5c. a collapsed floating window becomes a movable capsule", () => {
  assert.match(windowComponent, /const capsule = computed\(\(\) => props\.fullscreen && props\.panel\.collapsed\)/);
  assert.match(windowComponent, /width: capsule\.value \? "auto"/);
  assert.match(styles, /\.workspace-window--capsule \{[^}]*border-radius: 999px/);
});

test("6. windows cannot be detached, re-docked or snapped by hand", () => {
  assert.doesNotMatch(windowComponent, /脫離工作區|停靠底部|停靠左側|停靠右側|changePresentation|dockAnalysis|snap/i);
  assert.doesNotMatch(review, /全部重新停靠|setPresentation|snap/i);
  assert.match(windowComponent, /startDrag/);
  assert.match(windowComponent, /startResize/);
  assert.equal((review.match(/<AnalysisWindow /g) ?? []).length, 1);
});

test("7. docked windows ignore floating geometry and opacity", () => {
  assert.match(windowComponent, /const style = computed\(\(\) => props\.fullscreen \? \(\{[\s\S]*\}\) : \{\}\);/);
  assert.match(styles, /\.workspace-window--docked[\s\S]*position: relative/);
});

test("8. window settings exist only in fullscreen and hold just transparency and restore", () => {
  assert.equal((review.match(/<ReviewTimeline /g) ?? []).length, 1);
  assert.match(windowComponent, /<WorkspacePopover v-if="fullscreen && !capsule"/);
  const menu = windowComponent.slice(windowComponent.indexOf("<WorkspacePopover"), windowComponent.indexOf("</WorkspacePopover>"));
  assert.equal((menu.match(/<button /g) ?? []).length, 1);
  assert.match(menu, /回復全螢幕預設位置/);
});

test("9. transparency follows the selected panel layout across a visible range", () => {
  assert.match(windowComponent, /<label><span>背景透明度 <output>/);
  assert.match(windowComponent, /"--workspace-panel-alpha": visiblePanel\.value\.alpha/);
  assert.match(windowComponent, /:max="maxTransparency"/);
});

test("10. a stored Analysis side is dropped and the header has no workspace settings", () => {
  const saved = { ...defaultWorkspaceLayout(), analysisSide: "left" };
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(saved)), defaultWorkspaceLayout());
  assert.doesNotMatch(review, /analysisSide|工作區設定|還原預設版面/);
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

test("12. old floating v1 migrates safely while invalid state falls back", () => {
  const old = { ...defaultWorkspaceLayout(), version: 1 };
  delete (old as Partial<typeof old>).analysisDockWidth;
  delete (old as Partial<typeof old>).timelineDockHeight;
  assert.deepEqual(parseWorkspaceLayout(JSON.stringify(old)), defaultWorkspaceLayout());
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
