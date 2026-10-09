<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import ReviewPlayer from "../components/ReviewPlayer.vue";
import ReviewTimeline from "../components/ReviewTimeline.vue";
import AnalysisWindow from "../components/workspace/AnalysisWindow.vue";
import WorkspaceWindow from "../components/workspace/WorkspaceWindow.vue";
import WorkspacePopover from "../components/workspace/WorkspacePopover.vue";
import { availableTimelineModes } from "../components/timeline/timelineModeRegistry";
import type { EvidenceModel } from "../domain/models";
import { playerName, scoreText } from "../format";
import { playerShortcutAction } from "../interaction/playerShortcuts";
import { findStrokeTarget, resolveRouteRally, resolveRouteStroke } from "../rallies/rallyRoute";
import { useMatchContext } from "../state/matchContext";
import {
  constrainAnalysisDockWidth,
  constrainTimelineDockHeight,
  DEFAULT_ANALYSIS_DOCK_WIDTH,
  DEFAULT_TIMELINE_DOCK_HEIGHT,
  useWorkspaceLayout,
  type PanelLayout,
  type WorkspacePanelId,
} from "../state/workspaceLayout";
import { adaptiveDefaultPanelSizes } from "../presentation/workspaceDensity";
import { fittedFullscreenTimeline, fullscreenHomePanel } from "../state/workspaceGeometry";

defineOptions({ name: "ReviewPage" });
const context = useMatchContext();
const route = useRoute();
const { model, workspace } = context;
const { layout } = useWorkspaceLayout();
const activeWindow = ref<WorkspacePanelId>("analysis");
const playing = workspace.playing;
const player = ref<InstanceType<typeof ReviewPlayer> | null>(null);
const stage = ref<HTMLElement | null>(null);
/** The timeline's toolbar hosts the player controls, so the video and the timeline share one control bar. */
const playerControlsHost = ref<HTMLElement | null>(null);
const fullscreen = ref(false);
const fullscreenUiHidden = ref(false);
const panelInteracting = ref<Record<WorkspacePanelId, boolean>>({ timeline: false, analysis: false });
const pointerPressed = ref(false);
let idleTimer: number | undefined;
const viewportWidth = ref(typeof window === "undefined" ? 1440 : window.innerWidth);
const viewportHeight = ref(typeof window === "undefined" ? 900 : window.innerHeight);
const match = computed(() => {
  if (!model.value) throw new Error("Review requires a loaded MatchModel");
  return model.value;
});
/**
 * The docked timeline sits right under the video, so its toolbar takes the player controls. Below 1100px the
 * workspace stacks (floating-workspace.css) and Analysis comes between them, so the player keeps its own bar;
 * fullscreen always floats the timeline toolbar over the video.
 */
const controlsInTimeline = computed(() => !match.value.layoutOnly && (fullscreen.value || viewportWidth.value > 1_100));
const activeId = computed(() => workspace.activeRally.value?.id ?? null);
const currentLabel = computed(() => activeId.value === null ? "比賽空檔" : `片段 ${String(activeId.value + 1).padStart(3, "0")}`);
const displayPlayers = computed(() => ({ a: playerName(match.value.players.a, "選手 A"), b: playerName(match.value.players.b, "選手 B") }));
const matchTitle = computed(() => !match.value.title || /^(?:match:)?yt[_:-]/i.test(match.value.title) ? "比賽回看" : match.value.title);
const headerScore = computed(() => workspace.currentScore.value ? scoreText(workspace.currentScore.value) : null);
const timelineModes = computed(() => availableTimelineModes(match.value.capabilities));
const selectedTimelineLabel = computed(() => timelineModes.value.find((item) => item.id === layout.timelineMode)?.label ?? "片段");
const adaptiveSizes = computed(() => adaptiveDefaultPanelSizes(viewportWidth.value, viewportHeight.value));
const effectiveAnalysisDockWidth = computed(() =>
  layout.analysisDockWidth === DEFAULT_ANALYSIS_DOCK_WIDTH && !layout.panels.analysis.collapsed
    ? adaptiveSizes.value.analysisWidth
    : layout.analysisDockWidth,
);
const effectiveTimelineDockHeight = computed(() =>
  layout.timelineDockHeight === DEFAULT_TIMELINE_DOCK_HEIGHT && !layout.panels.timeline.collapsed
    ? adaptiveSizes.value.timelineHeight
    : layout.timelineDockHeight,
);
const fullscreenPanels = computed(() => ({
  ...layout.fullscreenPanels,
  timeline: fittedFullscreenTimeline(layout.fullscreenPanels.timeline, viewportHeight.value),
}));
const panels = computed(() => fullscreen.value ? fullscreenPanels.value : layout.panels);
/** Writable panel layouts; `panels` may present a fitted copy. */
const storedPanels = () => fullscreen.value ? layout.fullscreenPanels : layout.panels;
const timelineClock = () => player.value?.currentTime() ?? workspace.currentTimeSec.value;
/** Normal mode always docks both windows; fullscreen always floats them, so the dock collapse classes apply only outside fullscreen. */
const analysisDockCollapsed = computed(() => !fullscreen.value && layout.panels.analysis.collapsed);
const timelineDockCollapsed = computed(() => !fullscreen.value && layout.panels.timeline.collapsed);

watch(player, (value) => { context.player.value = value; }, { flush: "sync" });
watch([() => route.query.segment, () => route.query.stroke, model, player], () => {
  const segment = route.query.segment;
  if (segment === undefined || !model.value || !player.value) return;
  const rally = resolveRouteRally(model.value, segment);
  if (!rally) return;
  const stroke = route.query.stroke === undefined ? null : resolveRouteStroke(rally, route.query.stroke);
  if (stroke) workspace.selectStroke(stroke);
  else workspace.selectRally(rally);
}, { immediate: true, flush: "post" });
watch(model, () => {
  if (!timelineModes.value.some((item) => item.id === layout.timelineMode)) layout.timelineMode = "rally";
}, { flush: "sync" });

function isTextEditingTarget(event: KeyboardEvent) {
  return event.target instanceof Element && Boolean(event.target.closest("input,select,textarea,[contenteditable]"));
}
function shortcutBlocked(event: KeyboardEvent) {
  return event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.repeat ||
    (event.target instanceof Element && Boolean(event.target.closest("input,select,textarea,button,a,summary,[contenteditable]")));
}
function keyboard(event: KeyboardEvent) {
  revealUi();
  if (event.code === "Escape") {
    if (event.isComposing || isTextEditingTarget(event)) return;
    if (workspace.selectedRallyIndex.value !== null) { event.preventDefault(); workspace.clearSelection(); }
    return;
  }
  if (shortcutBlocked(event)) return;
  if (workspace.handleKeyboard(event)) return;
  const action = playerShortcutAction(event);
  if (!action) return;
  event.preventDefault();
  player.value?.handleShortcut(action);
}
function openEvidence(evidence: EvidenceModel) {
  const target = findStrokeTarget(match.value, evidence.eventIndex);
  if (target) workspace.selectStroke(target.stroke);
}
function updatePanel(id: WorkspacePanelId, panel: PanelLayout) {
  const shown = panels.value[id];
  const stored = storedPanels()[id];
  // Moving, collapsing or fading a fitted panel must not freeze its fitted size into storage.
  storedPanels()[id] = {
    ...panel,
    height: panel.height === shown.height ? stored.height : panel.height,
    y: panel.y === shown.y ? stored.y : panel.y,
  };
}
function fullscreenBounds() {
  return { width: stage.value?.clientWidth ?? window.innerWidth, height: stage.value?.clientHeight ?? window.innerHeight };
}
function restoreFullscreenPanel(id: WorkspacePanelId) {
  layout.fullscreenPanels[id] = fullscreenHomePanel(id, layout.fullscreenPanels[id], fullscreenBounds());
}
function setDockSize(id: WorkspacePanelId, value: number) {
  if (id === "analysis") layout.analysisDockWidth = constrainAnalysisDockWidth(value);
  else layout.timelineDockHeight = constrainTimelineDockHeight(value);
}
function clearIdleTimer() {
  if (idleTimer !== undefined) window.clearTimeout(idleTimer);
  idleTimer = undefined;
}
function interactionActive() {
  return pointerPressed.value || panelInteracting.value.analysis || panelInteracting.value.timeline ||
    // A pointer resting on a window keeps it: hidden windows let presses through to the video,
    // so the next drag would toggle playback instead of moving the window.
    Boolean(stage.value?.querySelector("select:focus, input:focus, [aria-expanded='true']:focus, .workspace-window:hover"));
}
function scheduleIdle() {
  clearIdleTimer();
  if (!fullscreen.value || interactionActive()) return;
  idleTimer = window.setTimeout(() => {
    if (!interactionActive()) fullscreenUiHidden.value = true;
  }, 3000);
}
function revealUi() {
  if (!fullscreen.value) return;
  fullscreenUiHidden.value = false;
  scheduleIdle();
}
function panelInteraction(id: WorkspacePanelId, active: boolean) {
  panelInteracting.value[id] = active;
  if (active) { fullscreenUiHidden.value = false; clearIdleTimer(); }
  else scheduleIdle();
}
function pointerDown() { pointerPressed.value = true; revealUi(); }
function pointerUp() { pointerPressed.value = false; scheduleIdle(); }
function syncFullscreen() {
  fullscreen.value = document.fullscreenElement === stage.value;
  fullscreenUiHidden.value = false;
  pointerPressed.value = false;
  scheduleIdle();
}
async function toggleFullscreen() {
  if (!stage.value) return;
  try {
    if (document.fullscreenElement === stage.value) await document.exitFullscreen();
    else await stage.value.requestFullscreen();
  } catch { /* A user gesture is required by some browsers. */ }
}
function cycleTimelineMode(direction: -1 | 1) {
  const index = timelineModes.value.findIndex((item) => item.id === layout.timelineMode);
  layout.timelineMode = timelineModes.value[(index + direction + timelineModes.value.length) % timelineModes.value.length]?.id ?? "rally";
}
function modeWheel(event: WheelEvent) {
  if (!event.shiftKey || event.deltaY === 0) return;
  event.preventDefault();
  cycleTimelineMode(event.deltaY > 0 ? 1 : -1);
}
function updateViewport() {
  viewportWidth.value = window.innerWidth;
  viewportHeight.value = window.innerHeight;
}
onMounted(() => {
  window.addEventListener("resize", updateViewport);
  document.addEventListener("fullscreenchange", syncFullscreen);
  window.addEventListener("pointerup", pointerUp);
  window.addEventListener("pointercancel", pointerUp);
  updateViewport();
});
onActivated(() => window.addEventListener("keydown", keyboard));
onDeactivated(() => {
  window.removeEventListener("keydown", keyboard);
  player.value?.pause();
  clearIdleTimer();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", keyboard);
  window.removeEventListener("resize", updateViewport);
  document.removeEventListener("fullscreenchange", syncFullscreen);
  window.removeEventListener("pointerup", pointerUp);
  window.removeEventListener("pointercancel", pointerUp);
  clearIdleTimer();
  if (context.player.value === player.value) context.player.value = null;
});
</script>

<template>
  <div class="review-page">
    <header class="topbar review-matchbar" :class="{ 'review-matchbar--video-wide': analysisDockCollapsed }" :style="{ '--analysis-dock-width': `${effectiveAnalysisDockWidth}px` }">
      <div class="review-video-heading">
        <h2 class="review-match-title">{{ matchTitle }}</h2>
        <div class="match-identity">
          <span class="player-name">{{ displayPlayers.a }}</span>
          <div class="match-score-context">
            <strong v-if="headerScore" class="score-state match-score-state" :aria-label="headerScore"><span>{{ workspace.currentScore.value?.[0] }}</span><span class="score-state-divider" aria-hidden="true" /><span>{{ workspace.currentScore.value?.[1] }}</span></strong>
            <small>{{ currentLabel }}</small>
          </div>
          <span class="player-name">{{ displayPlayers.b }}</span>
        </div>
      </div>
      <div class="header-actions">
        <WorkspacePopover label="快捷鍵" :min-width="320">
          <template #trigger><span aria-hidden="true">?</span></template>
          <div class="shortcut-help__content"><strong>快捷鍵</strong><p>Space／K 播放 · S 只播片段 · ←／→ 跳 5 秒 · Shift + ←／→ 切換擊球 · [／] 切換片段</p><p>時間軸雙擊片段放大 · 再次雙擊回到全場</p><p>時間軸滾輪平移 · Ctrl／Cmd + 滾輪縮放</p></div>
        </WorkspacePopover>
      </div>
    </header>
    <main class="review-main review-main--workspace">
      <section ref="stage" class="review-workspace-stage" :class="{ 'review-workspace-stage--analysis-collapsed': analysisDockCollapsed, 'review-workspace-stage--timeline-collapsed': timelineDockCollapsed, 'review-workspace-stage--ui-hidden': fullscreenUiHidden }" :style="{ '--analysis-dock-width': `${effectiveAnalysisDockWidth}px`, '--timeline-dock-height': `${effectiveTimelineDockHeight}px` }" aria-label="影片分析工作區" @pointermove="revealUi" @pointerdown="pointerDown" @focusin="revealUi">
        <ReviewPlayer v-if="!match.layoutOnly" ref="player" :src="match.video" :fullscreen="fullscreen" :controls-target="controlsInTimeline ? playerControlsHost : null" :segments="match.rallies" v-model:segments-only="layout.segmentsOnly" @time="workspace.updateTime" @playing="playing = $event" @fullscreen-toggle="toggleFullscreen" />
        <div v-else class="layout-placeholder"><h2>一小時 · 120 個合成片段</h2><p>僅顯示長時間軸與片段清單。</p></div>

        <WorkspaceWindow title="時間軸" panel-id="timeline" :panel="panels.timeline" :active="activeWindow === 'timeline'" :passive="playing" :fullscreen="fullscreen" toolbar :toolbar-bottom="fullscreen" :dock-size="effectiveTimelineDockHeight" @activate="activeWindow = 'timeline'" @change="updatePanel('timeline', $event)" @dock-size="setDockSize('timeline', $event)" @interaction="panelInteraction('timeline', $event)" @restore="restoreFullscreenPanel('timeline')">
          <template #header>
            <label class="timeline-mode-selector" @wheel="modeWheel"><span class="sr-only">時間軸模式</span><select v-model="layout.timelineMode" aria-label="時間軸模式"><option v-for="mode in timelineModes" :key="mode.id" :value="mode.id">{{ mode.label }}</option></select></label>
            <span class="workspace-window__mode-label">{{ selectedTimelineLabel }}</span>
            <div v-if="controlsInTimeline" ref="playerControlsHost" class="workspace-window__player-controls" />
          </template>
          <ReviewTimeline :model="match" :timeline-mode="layout.timelineMode" :selected-id="workspace.selectedRallyIndex.value" :selected-stroke-index="workspace.selectedStrokeIndex.value" :active-stroke-index="workspace.activeStroke.value?.eventIndex ?? null" :score-context-id="workspace.activeScoreRally.value?.id ?? null" :time="workspace.currentTimeSec.value" :playing="playing" :clock="timelineClock" :active-id="activeId" @rally="workspace.selectRally" @rally-at="workspace.selectRallyAt" @stroke="workspace.selectStroke" @commentary="workspace.selectCommentary" @seek="workspace.seek" />
        </WorkspaceWindow>

        <WorkspaceWindow title="分析" panel-id="analysis" :panel="panels.analysis" :active="activeWindow === 'analysis'" :passive="playing" :fullscreen="fullscreen" :dock-size="effectiveAnalysisDockWidth" @activate="activeWindow = 'analysis'" @change="updatePanel('analysis', $event)" @dock-size="setDockSize('analysis', $event)" @interaction="panelInteraction('analysis', $event)" @restore="restoreFullscreenPanel('analysis')">
          <AnalysisWindow :model="match" :selected-id="workspace.selectedRallyIndex.value" :active-id="activeId" :selected-stroke-index="workspace.selectedStrokeIndex.value" :active-stroke-index="workspace.activeStroke.value?.eventIndex ?? null" :current-score="workspace.currentScore.value" :current-time="workspace.currentTimeSec.value" :view="layout.analysisView" @view="layout.analysisView = $event" @rally="workspace.selectRally" @stroke="workspace.selectStroke" @evidence="openEvidence" @previous-stroke="workspace.moveStroke(-1)" @next-stroke="workspace.moveStroke(1)" @back="workspace.clearSelection" />
        </WorkspaceWindow>
      </section>
    </main>
  </div>
</template>
