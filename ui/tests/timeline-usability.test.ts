import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import type { RallyModel } from "../src/domain/models";
import {
  constrainTimelineViewport,
  doubleClickFit,
  focusBands,
  hoverBandRally,
  panTimelineViewport,
  zoomTimelineViewport,
} from "../src/temporal/timelineNavigation";
import {
  doubleClickHint,
  timelineHoverPreview,
  withDoubleClickHint,
} from "../src/components/timeline/timelinePreview";
import { analysisContextSummary } from "../src/components/workspace/analysisContext";

const rally: RallyModel = {
  id: 4,
  start: 40,
  end: 46,
  duration: 6,
  score: [8, 7],
  game: 0,
  multi: false,
  subScores: [],
  splits: [],
  hits: [{ eventIndex: 12, strokeIndex: 2, frame: 1260, time: 42, ordinal: 3, player: "a", type: "殺球", confidence: 0.9 }],
  audio: { segmentIndex: 4, confidence: 0.82, intensity: 0.5, windowCount: 3 },
  highlight: 0.734,
  commentary: {
    status: "available",
    source: "full-match",
    summary: null,
    events: [{ segmentIndex: 4, strokeIndex: 2, frame: 1260, timeSec: 42, player: "a", text: "精準的進攻壓迫", sourceFactIds: [], evidence: [] }],
  },
};
const source = (path: string) => readFileSync(path, "utf8");

test("1. wheel zoom preserves its temporal anchor", () => {
  const next = zoomTimelineViewport({ startSec: 20, endSec: 60, durationSec: 40 }, 0.5, 30, 100);
  assert.deepEqual(next, { startSec: 25, endSec: 45, durationSec: 20 });
});
test("2. wheel pan changes viewport without changing scale", () => {
  assert.deepEqual(panTimelineViewport({ startSec: 20, endSec: 60, durationSec: 40 }, 8, 100), { startSec: 28, endSec: 68, durationSec: 40 });
});
test("3. temporal zoom clamps to a precise usable minimum", () => {
  assert.deepEqual(zoomTimelineViewport({ startSec: 0, endSec: 20, durationSec: 20 }, 0.001, 10, 100), { startSec: 9.75, endSec: 10.25, durationSec: 0.5 });
});
test("4. navigation clamps both match boundaries", () => {
  assert.deepEqual(constrainTimelineViewport(-10, 30, 100), { startSec: 0, endSec: 30, durationSec: 30 });
  assert.deepEqual(panTimelineViewport({ startSec: 70, endSec: 100, durationSec: 30 }, 20, 100), { startSec: 70, endSec: 100, durationSec: 30 });
});
test("5. double-click focuses the rally under the pointer and returns to the match from anywhere", () => {
  const rally = { id: 3 };
  assert.deepEqual(doubleClickFit("match", rally), { fit: "rally", rally });
  assert.equal(doubleClickFit("match", null), null);
  assert.deepEqual(doubleClickFit("custom", rally), { fit: "rally", rally });
  assert.deepEqual(doubleClickFit("custom", null), { fit: "match", rally: null });
  assert.deepEqual(doubleClickFit("rally", rally), { fit: "match", rally: null });
  assert.deepEqual(doubleClickFit("rally", null), { fit: "match", rally: null });
});
test("6. zoom and pan are manual navigation, never seek emissions", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  const body = timeline.slice(timeline.indexOf("function panTimeline"), timeline.indexOf("function toggleFitOnDoubleClick"));
  assert.match(body, /pauseFollow\(\)/);
  assert.match(timeline, /function pauseFollow\(\) \{[^}]*manualNavigation\.value = true;[^}]*setTimeout\(resumeFollow, FOLLOW_RESUME_DELAY_MS\)/s);
  assert.match(body, /fit\.value = "custom"/);
  assert.doesNotMatch(body, /emit\("seek"/);
});
test("7. switching timeline mode does not reset the temporal viewport", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  assert.doesNotMatch(timeline, /watch\([^)]*timelineMode[\s\S]{0,220}renderViewport/);
});
test("8. rally hover preview uses canonical duration, strokes, score and highlight place", () => {
  assert.deepEqual(timelineHoverPreview({ kind: "rally", id: 4 }, [rally]), { title: "", lines: ["6.00 秒", "比分 8:7", "精華排名 #1 / 1"] });
});
test("9. stroke and commentary previews expose real metadata", () => {
  assert.deepEqual(timelineHoverPreview({ kind: "stroke", id: 12 }, [rally]), { title: "第 3 拍", lines: ["00:42.00", "殺球", "a"] });
  assert.match(timelineHoverPreview({ kind: "commentary", id: "4:2" }, [rally])?.lines.join(" ") ?? "", /精準的進攻壓迫/);
});
test("10. hover preview is teleported above overlapping windows, into the fullscreen element when there is one", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  assert.match(timeline, /<Teleport :to="tooltipTarget">[\s\S]*timeline-hover-tooltip/);
  assert.match(timeline, /document\.fullscreenElement instanceof HTMLElement \? document\.fullscreenElement : "body"/);
  assert.match(timeline, /addEventListener\("fullscreenchange", syncTooltipTarget\)/);
  assert.match(source("src/styles/timeline.css"), /\.timeline-hover-tooltip\s*\{[\s\S]*z-index: calc\(var\(--z-overlay\) \+ 20\)/);
});
test("11. analysis header distinguishes rally and active stroke context", () => {
  const context = analysisContextSummary(rally, false, 12);
  assert.equal(context.state, "stroke");
  assert.equal(context.status, "第 1/1 拍");
  assert.equal(analysisContextSummary(rally, false, null).state, "rally");
});
test("12. analysis header labels the Rally kept on show during a gap", () => {
  const context = analysisContextSummary(rally, true, 12);
  assert.equal(context.state, "previous");
  assert.equal(context.status, "上一回合");
  assert.equal(analysisContextSummary(null, false, null).state, "empty");
});
test("13. window focus is transient stacking state, not persisted layout", () => {
  const review = source("src/pages/ReviewPage.vue");
  const layout = source("src/state/workspaceLayout.ts");
  assert.match(review, /activeWindow = ref<WorkspacePanelId>/);
  assert.match(review, /@activate="activeWindow = 'timeline'"/);
  assert.doesNotMatch(layout, /activeWindow|zIndex/);
});
test("14. court geometry keeps its canonical aspect ratio while resizing", () => {
  // The SVG fills the window; the viewBox and `meet` keep the court's real proportions inside it.
  assert.match(source("src/components/inspector/RallyCourtMap.vue"), /viewBox="-32 -32 284 524" preserveAspectRatio="xMidYMid meet"/);
  assert.match(source("src/styles/floating-workspace.css"), /\.analysis-court-view \.rally-court-map__court \{[^}]*width: 100%;/);
});
test("15. passive playback only fades management chrome and restores on interaction", () => {
  const window = source("src/components/workspace/WorkspaceWindow.vue");
  const css = source("src/styles/floating-workspace.css");
  assert.match(window, /passive\?: boolean/);
  assert.match(window, /workspace-window--chrome-idle/);
  assert.match(css, /workspace-window--chrome-idle[^}]*workspace-window__actions/);
  assert.doesNotMatch(css, /workspace-window--chrome-idle[^}]*workspace-window__body/);
});
test("16. the double-click hint names what a double-click would do, and nothing where it does nothing", () => {
  assert.equal(doubleClickHint("match", rally), "雙擊放大片段");
  assert.equal(doubleClickHint("custom", rally), "雙擊放大片段");
  assert.equal(doubleClickHint("rally", rally), "雙擊返回全場");
  assert.equal(doubleClickHint("rally", null), "雙擊返回全場");
  assert.equal(doubleClickHint("custom", null), "雙擊返回全場");
  assert.equal(doubleClickHint("match", null), null);
});
test("17. the hint is appended to a preview, or stands alone where the mode has none", () => {
  const preview = timelineHoverPreview({ kind: "rally", id: 4 }, [rally]);
  assert.deepEqual(withDoubleClickHint(preview, "雙擊放大片段"), { ...preview, hint: "雙擊放大片段" });
  assert.deepEqual(withDoubleClickHint(null, "雙擊放大片段"), { title: "", lines: [], hint: "雙擊放大片段" });
  assert.equal(withDoubleClickHint(preview, null), preview);
  assert.equal(withDoubleClickHint(null, null), null);
});
test("18. the hover band follows the rally under the pointer only while it can be focused", () => {
  const rallies = [{ start: 0, end: 8 }, { start: 10, end: 18 }];
  const idle = { fit: "match" as const, lensActive: false };
  assert.equal(hoverBandRally(rallies, 12, idle), rallies[1]);
  assert.equal(hoverBandRally(rallies, 12, { ...idle, fit: "custom" }), rallies[1]);
  assert.equal(hoverBandRally(rallies, 9, idle), null, "a gap has no band");
  assert.equal(hoverBandRally(rallies, null, idle), null);
  assert.equal(hoverBandRally(rallies, 12, { ...idle, fit: "rally" }), null);
  assert.equal(hoverBandRally(rallies, 12, { ...idle, lensActive: true }), null);
});
test("19. the score lane leaves hover to the shared band and keeps only selected and active", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  assert.match(timeline, /class="timeline-hover-band"/);
  assert.doesNotMatch(timeline, /"hovered"/);
  assert.doesNotMatch(source("src/components/timeline/TimelineFocusBands.vue"), /hovered/);
  assert.doesNotMatch(source("src/styles/timeline.css"), /\.timeline-focus\s*\{/);
});
test("20. selected and playing bands follow each lane's span, and the playing one yields to the selected one", () => {
  const view = { startSec: 0, endSec: 100, durationSec: 100 };
  const spans: Record<number, { start: number; end: number }> = { 1: { start: 10, end: 18 }, 2: { start: 30, end: 36 } };
  const spanOf = (id: number) => spans[id] ?? null;
  assert.deepEqual(focusBands(1, 2, spanOf, view), [
    { state: "selected", left: 10, width: 8 },
    { state: "active", left: 30, width: 6 },
  ]);
  assert.deepEqual(focusBands(1, 1, spanOf, view), [{ state: "selected", left: 10, width: 8 }]);
  assert.deepEqual(focusBands(null, 2, spanOf, view), [{ state: "active", left: 30, width: 6 }]);
  assert.deepEqual(focusBands(9, null, spanOf, view), [], "a Rally without a span on this lane has no band");
});
test("21. the stroke and cheer lanes share the score lane's bands, game separators and labels", () => {
  const timeline = source("src/components/ReviewTimeline.vue");
  assert.match(timeline, /<TimelineFocusBands :bands="leadFocusBands" \/>\s*<svg class="lead-chart"/);
  for (const kind of ["stroke", "cheer"]) {
    const lane = timeline.slice(timeline.indexOf(`kind="${kind}"`), timeline.indexOf("</TimelineLane>", timeline.indexOf(`kind="${kind}"`)));
    assert.match(lane, /<TimelineFocusBands :bands="rallyFocusBands" \/>/, kind);
    assert.match(lane, /v-for="separator in leadMarks\.separators"[^>]*class="lead-separator"/, kind);
    assert.match(lane, /class="lead-game-label"/, kind);
  }
});
