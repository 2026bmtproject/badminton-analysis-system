<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { AnalysisView } from "../../state/workspaceLayout";
import type { EvidenceModel, MatchModel, RallyModel, ScoreModel, StrokeModel } from "../../domain/models";
import RallyInspector from "../inspector/RallyInspector.vue";
import RallyCourtMap from "../inspector/RallyCourtMap.vue";
import { analysisContextSummary } from "./analysisContext";
import { resolveAnalysisDensity } from "../../presentation/workspaceDensity";

const props = defineProps<{
  model: MatchModel;
  selectedId: number | null;
  activeId: number | null;
  selectedStrokeIndex: number | null;
  activeStrokeIndex: number | null;
  currentScore: ScoreModel | null;
  currentTime: number;
  view: AnalysisView;
}>();
const emit = defineEmits<{
  view: [value: AnalysisView];
  rally: [rally: RallyModel];
  stroke: [stroke: StrokeModel];
  evidence: [evidence: EvidenceModel];
  open: [rally: RallyModel, stroke: StrokeModel | null];
  previousStroke: [];
  nextStroke: [];
  back: [];
}>();
const root = ref<HTMLElement | null>(null);
const density = ref<"full" | "compact">("full");
let observer: ResizeObserver | undefined;
const contextRally = computed(() =>
  props.model.rallies.find((rally) => rally.id === props.activeId) ?? null,
);
const context = computed(() =>
  analysisContextSummary(
    contextRally.value,
    props.activeStrokeIndex,
    props.currentScore,
    props.currentTime,
  ),
);
function forwardOpen(rally: RallyModel, stroke: StrokeModel | null) {
  emit("open", rally, stroke);
}
function measureDensity() {
  if (root.value) density.value = resolveAnalysisDensity(root.value.clientWidth);
}
onMounted(() => {
  observer = new ResizeObserver(measureDensity);
  if (root.value) observer.observe(root.value);
  measureDensity();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div ref="root" class="analysis-window-content" :data-density="density">
    <header
      class="analysis-context-header"
      :data-context-state="context.state"
      :aria-label="[context.title, ...context.details, context.status].join(' · ')"
    >
      <div class="analysis-context-header__identity">
        <strong>{{ context.title }}</strong>
        <span>{{ context.status }}</span>
      </div>
      <p><span v-for="detail in context.details" :key="detail">{{ detail }}</span></p>
    </header>
    <nav class="analysis-tabs" aria-label="分析檢視">
      <button type="button" :aria-pressed="view === 'analysis'" @click="emit('view', 'analysis')">摘要</button>
      <button type="button" :aria-pressed="view === 'court'" :disabled="!contextRally" @click="emit('view', 'court')">球場</button>
    </nav>
    <RallyInspector
      v-if="view === 'analysis'"
      :model="model"
      :selected-id="selectedId"
      :active-id="activeId"
      :selected-stroke-index="selectedStrokeIndex"
      :active-stroke-index="activeStrokeIndex"
      @rally="emit('rally', $event)"
      @stroke="emit('stroke', $event)"
      @evidence="emit('evidence', $event)"
      @open="forwardOpen"
      @previous-stroke="emit('previousStroke')"
      @next-stroke="emit('nextStroke')"
      @back="emit('back')"
    />
    <div v-else class="analysis-court-view">
      <RallyCourtMap
        v-if="contextRally"
        :rally="contextRally"
        :players="model.players"
        :selected-stroke-index="activeStrokeIndex"
        :calibration-unconfirmed="model.source?.limitations.some((item) => item.includes('球場為自動校正'))"
        @stroke="emit('stroke', $event)"
      />
      <p v-else class="empty-state">播放或選取片段後即可檢視球場位置。</p>
    </div>
  </div>
</template>
