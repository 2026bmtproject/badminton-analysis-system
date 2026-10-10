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

test("4. docked windows cannot collapse; only fullscreen windows fold away", () => {
  assert.match(windowComponent, /<div v-if="fullscreen" class="workspace-window__actions">[\s\S]*class="workspace-window__collapse"/);
  assert.doesNotMatch(windowComponent, /workspace-window__edge-tab|analysisEdgeCollapsed/);
  assert.doesNotMatch(styles, /review-workspace-stage--(analysis|timeline)-collapsed|workspace-window--docked[^{]*workspace-window--collapsed/);
  assert.doesNotMatch(review, /layout\.panels\.(analysis|timeline)\.collapsed|DockCollapsed/);
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

test("5. a collapsed fullscreen window keeps its header with a single expand control", () => {
  assert.doesNotMatch(review, /compact-rail|expandTimeline/);
  assert.doesNotMatch(source("src/components/ReviewTimeline.vue"), /compactRail|timeline-rail-expand|emit\("expand"\)/);
  assert.match(windowComponent, /<div v-show="!panel\.collapsed" class="workspace-window__body">/);
  assert.equal((windowComponent.match(/@click="toggleCollapsed"/g) ?? []).length, 1, "the header button only");
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

test("5d. the docked Timeline toolbar keeps its dock resize strip clear of the controls", () => {
  assert.match(windowComponent, /closest\("button,select,input,label,a,\.workspace-window__header-slot"\)/, "clicking the controls must not expand a collapsed timeline");
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

test("8. window settings exist only in fullscreen and hold transparency, idle delay and restore", () => {
  assert.equal((review.match(/<ReviewTimeline /g) ?? []).length, 1);
  assert.match(windowComponent, /<div v-if="fullscreen" class="workspace-window__actions">\s*<WorkspacePopover v-if="!capsule"/);
  const menu = windowComponent.slice(windowComponent.indexOf("<WorkspacePopover"), windowComponent.indexOf("</WorkspacePopover>"));
  assert.equal((menu.match(/<button /g) ?? []).length, 1);
  assert.match(menu, />回復預設</);
  assert.match(menu, /aria-label="閒置後隱藏秒數"/);
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
  assert.match(review, /:current-time="workspace\.currentTimeSec\.value"/);
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

test("16. the docked stage fills the height the headers leave, without a hand-tuned viewport offset", () => {
  const shell = source("src/styles/desktop-shell.css");
  assert.doesNotMatch(shell, /review-workspace-stage[^}]*100dvh/);
  assert.match(shell, /@media \(min-width: 1101px\) \{[\s\S]*\.desktop-content \.review-workspace-stage \{ flex: 1 1 0; height: auto; min-height: 540px; max-height: 1100px; \}/);
});
