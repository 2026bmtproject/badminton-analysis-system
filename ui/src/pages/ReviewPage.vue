<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import ReviewPlayer from "../components/ReviewPlayer.vue";
import ReviewTimeline from "../components/ReviewTimeline.vue";
import AnalysisWindow from "../components/workspace/AnalysisWindow.vue";
import WorkspaceWindow from "../components/workspace/WorkspaceWindow.vue";
import WorkspacePopover from "../components/workspace/WorkspacePopover.vue";
import { availableTimelineModes } from "../components/timeline/timelineModeRegistry";
import type { EvidenceModel, RallyModel, StrokeModel } from "../domain/models";
import { formatTime, playerName, scoreText } from "../format";
import { playerShortcutAction } from "../interaction/playerShortcuts";
import { findStrokeTarget } from "../rallies/rallyRoute";
import { hitStatus } from "../review";
import { useMatchContext } from "../state/matchContext";
import {
  constrainAnalysisDockWidth,
  constrainTimelineDockHeight,
  DEFAULT_ANALYSIS_DOCK_WIDTH,
  DEFAULT_TIMELINE_DOCK_HEIGHT,
  useWorkspaceLayout,
  type AnalysisDockSide,
  type PanelLayout,
  type WorkspacePanelId,
} from "../state/workspaceLayout";
import { adaptiveDefaultPanelSizes } from "../presentation/workspaceDensity";

const context = useMatchContext();
const route = useRoute();
const router = useRouter();
const { model, workspace } = context;
const { layout, reset, redockAll } = useWorkspaceLayout();
const activeWindow = ref<WorkspacePanelId>("analysis");
const playing = ref(false);
const player = ref<InstanceType<typeof ReviewPlayer> | null>(null);
const viewportWidth = ref(typeof window === "undefined" ? 1440 : window.innerWidth);
const viewportHeight = ref(typeof window === "undefined" ? 900 : window.innerHeight);
const match = computed(() => {
  if (!model.value) throw new Error("Review requires a loaded MatchModel");
  return model.value;
});
const activeId = computed(() => workspace.activeRally.value?.id ?? null);
const currentLabel = computed(() => activeId.value === null ? "比賽空檔" : `片段 ${String(activeId.value + 1).padStart(3, "0")}`);
const displayPlayers = computed(() => ({ a: playerName(match.value.players.a, "選手 A"), b: playerName(match.value.players.b, "選手 B") }));
const headerScore = computed(() => workspace.currentScore.value ? scoreText(workspace.currentScore.value) : null);
const commentaryAvailabilityLabel = computed(() => {
  const availability = match.value.commentaryAvailability;
  if (availability.coverage === "none") return "未提供";
  if (availability.coverage === "complete") return `全部 ${availability.totalRallyCount} 個片段均有賽評`;
  return `部分提供（${availability.availableRallyCount}/${availability.totalRallyCount} 個片段）`;
});
const playerContext = computed(() => {
  const rally = workspace.activeRally.value;
  return rally ? {
    label: `片段 ${String(rally.id + 1).padStart(3, "0")}`,
    score: headerScore.value,
    meta: `${formatTime(rally.start)}–${formatTime(rally.end)} · ${hitStatus(match.value.states.events?.status, rally.hits?.length ?? null)}`,
  } : { label: "比賽空檔", score: headerScore.value, meta: formatTime(workspace.currentTimeSec.value) };
});
const timelineModes = computed(() => availableTimelineModes(match.value.capabilities));
const selectedTimelineLabel = computed(() => timelineModes.value.find((item) => item.id === layout.timelineMode)?.label ?? "片段");
const adaptiveSizes = computed(() => adaptiveDefaultPanelSizes(viewportWidth.value, viewportHeight.value));
const effectiveAnalysisDockWidth = computed(() =>
  layout.analysisDockWidth === DEFAULT_ANALYSIS_DOCK_WIDTH && layout.panels.analysis.presentation === "docked" && !layout.panels.analysis.collapsed
    ? adaptiveSizes.value.analysisWidth
    : layout.analysisDockWidth,
);
const effectiveTimelineDockHeight = computed(() =>
  layout.timelineDockHeight === DEFAULT_TIMELINE_DOCK_HEIGHT && layout.panels.timeline.presentation === "docked" && !layout.panels.timeline.collapsed
    ? adaptiveSizes.value.timelineHeight
    : layout.timelineDockHeight,
);

watch(player, (value) => { context.player.value = value; }, { flush: "sync" });
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
function openRally(rally: RallyModel, stroke: StrokeModel | null) {
  void router.push({ name: "rally-detail", params: { matchId: route.params.matchId, segmentId: rally.id }, query: stroke ? { stroke: String(stroke.eventIndex) } : {} });
}
function openEvidence(evidence: EvidenceModel) {
  const target = findStrokeTarget(match.value, evidence.eventIndex);
  if (target) workspace.selectStroke(target.stroke);
}
function updatePanel(id: WorkspacePanelId, panel: PanelLayout) { layout.panels[id] = panel; }
function setPresentation(id: WorkspacePanelId, presentation: "docked" | "detached") {
  layout.panels[id].presentation = presentation;
  activeWindow.value = id;
}
function setAnalysisSide(side: AnalysisDockSide) {
  layout.analysisSide = side;
}
function setDockSize(id: WorkspacePanelId, value: number) {
  if (id === "analysis") layout.analysisDockWidth = constrainAnalysisDockWidth(value);
  else layout.timelineDockHeight = constrainTimelineDockHeight(value);
}
function expandTimeline() {
  layout.panels.timeline.collapsed = false;
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
  window.addEventListener("keydown", keyboard);
  window.addEventListener("resize", updateViewport);
  updateViewport();
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", keyboard);
  window.removeEventListener("resize", updateViewport);
  if (context.player.value === player.value) context.player.value = null;
});
</script>

<template>
  <div class="review-page">
    <header class="topbar review-matchbar">
      <div class="header-navigation" aria-hidden="true" />
      <div class="match-identity">
        <span class="player-name">{{ displayPlayers.a }}</span>
        <div class="match-score-context">
          <strong v-if="headerScore" class="score-state match-score-state" :aria-label="headerScore"><span>{{ workspace.currentScore.value?.[0] }}</span><span class="score-state-divider" aria-hidden="true" /><span>{{ workspace.currentScore.value?.[1] }}</span></strong>
          <small>{{ currentLabel }}</small>
        </div>
        <span class="player-name">{{ displayPlayers.b }}</span>
      </div>
      <div class="header-actions">
        <WorkspacePopover label="快捷鍵" :min-width="320">
          <template #trigger><span aria-hidden="true">?</span></template>
          <div class="shortcut-help__content"><strong>快捷鍵</strong><p>Space／K 播放 · ←／→ 跳 5 秒 · Shift + ←／→ 切換擊球 · [／] 切換片段</p><p>時間軸滾輪平移 · Ctrl／Cmd + 滾輪縮放</p></div>
        </WorkspacePopover>
        <WorkspacePopover label="工作區設定" :min-width="220">
          <template #trigger><span class="workspace-settings__label">工作區</span></template>
          <div class="workspace-settings__content" aria-label="工作區版面">
            <label>分析停靠位置<select v-model="layout.analysisSide" aria-label="分析停靠位置">
              <option value="right">右側</option><option value="left">左側</option>
            </select></label>
            <button type="button" @click="redockAll">全部重新停靠</button>
            <button type="button" @click="reset">還原預設版面</button>
            <details class="workspace-source-details">
              <summary>比賽資訊</summary>
              <template v-if="match.source"><p v-for="note in match.source.limitations" :key="note">{{ note }}</p><p>來源：{{ match.source.matchId }} · 匯入：{{ match.source.importedAt }}</p></template>
              <p>賽評：{{ commentaryAvailabilityLabel }}。</p>
            </details>
          </div>
        </WorkspacePopover>
      </div>
    </header>
    <main class="review-main review-main--workspace">
      <section class="review-workspace-stage" :class="[`review-workspace-stage--analysis-${layout.analysisSide}`, { 'review-workspace-stage--analysis-detached': layout.panels.analysis.presentation === 'detached', 'review-workspace-stage--timeline-detached': layout.panels.timeline.presentation === 'detached', 'review-workspace-stage--analysis-collapsed': layout.panels.analysis.presentation === 'docked' && layout.panels.analysis.collapsed, 'review-workspace-stage--timeline-collapsed': layout.panels.timeline.presentation === 'docked' && layout.panels.timeline.collapsed }]" :style="{ '--analysis-dock-width': `${effectiveAnalysisDockWidth}px`, '--timeline-dock-height': `${effectiveTimelineDockHeight}px` }" aria-label="影片分析工作區">
        <ReviewPlayer v-if="!match.layoutOnly" ref="player" :src="match.video" :context="playerContext" :active-rally="workspace.activeRally.value" :active-stroke="workspace.activeStroke.value" :timeline-expanded="!layout.panels.timeline.collapsed" @time="workspace.updateTime" @playing="playing = $event" />
        <div v-else class="layout-placeholder"><h2>一小時 · 120 個合成片段</h2><p>僅顯示長時間軸與片段清單。</p></div>

        <WorkspaceWindow title="時間軸" panel-id="timeline" :panel="layout.panels.timeline" :active="activeWindow === 'timeline'" :passive="playing" :dock-size="effectiveTimelineDockHeight" @activate="activeWindow = 'timeline'" @change="updatePanel('timeline', $event)" @presentation="setPresentation('timeline', $event)" @dock-size="setDockSize('timeline', $event)">
          <template #header>
            <label class="timeline-mode-selector" @wheel="modeWheel"><span class="sr-only">時間軸模式</span><select v-model="layout.timelineMode" aria-label="時間軸模式"><option v-for="mode in timelineModes" :key="mode.id" :value="mode.id">{{ mode.label }}</option></select></label>
            <span class="workspace-window__mode-label">{{ selectedTimelineLabel }}</span>
          </template>
          <ReviewTimeline :model="match" :timeline-mode="layout.timelineMode" :compact-rail="layout.panels.timeline.presentation === 'docked' && layout.panels.timeline.collapsed" :selected-id="workspace.selectedRallyIndex.value" :selected-stroke-index="workspace.selectedStrokeIndex.value" :active-stroke-index="workspace.activeStroke.value?.eventIndex ?? null" :score-context-id="workspace.activeScoreRally.value?.id ?? null" :time="workspace.currentTimeSec.value" :active-id="activeId" :show-header="false" @rally="workspace.selectRally" @rally-at="workspace.selectRallyAt" @stroke="workspace.selectStroke" @commentary="workspace.selectCommentary" @seek="workspace.seek" @inspect="player?.setInspectionTime($event)" @expand="expandTimeline" />
        </WorkspaceWindow>

        <WorkspaceWindow title="分析" panel-id="analysis" :panel="layout.panels.analysis" :active="activeWindow === 'analysis'" :passive="playing" :dock-side="layout.analysisSide" :dock-size="effectiveAnalysisDockWidth" @activate="activeWindow = 'analysis'" @change="updatePanel('analysis', $event)" @presentation="setPresentation('analysis', $event)" @dock-side="setAnalysisSide" @dock-size="setDockSize('analysis', $event)">
          <AnalysisWindow :model="match" :selected-id="workspace.selectedRallyIndex.value" :active-id="activeId" :selected-stroke-index="workspace.selectedStrokeIndex.value" :active-stroke-index="workspace.activeStroke.value?.eventIndex ?? null" :current-score="workspace.currentScore.value" :current-time="workspace.currentTimeSec.value" :view="layout.analysisView" @view="layout.analysisView = $event" @rally="workspace.selectRally" @stroke="workspace.selectStroke" @evidence="openEvidence" @open="openRally" @previous-stroke="workspace.moveStroke(-1)" @next-stroke="workspace.moveStroke(1)" @back="workspace.clearSelection" />
        </WorkspaceWindow>
      </section>
    </main>
  </div>
</template>
