<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { AnalysisView } from "../../state/workspaceLayout";
import type { EvidenceModel, MatchModel, StrokeModel } from "../../domain/models";
import RallyInspector from "../inspector/RallyInspector.vue";
import RallyCourtMap from "../inspector/RallyCourtMap.vue";
import { analysisContextSummary } from "./analysisContext";
import { resolveAnalysisDensity } from "../../presentation/workspaceDensity";
import { rallyAtOrBefore } from "../../temporal/activeContext";
import { useMatchContext } from "../../state/matchContext";
import { useSegmentCommentary } from "../../state/segmentCommentary";

const props = defineProps<{
  model: MatchModel;
  activeId: number | null;
  activeStrokeIndex: number | null;
  currentTime: number;
  view: AnalysisView;
  strokesCollapsed: boolean;
}>();
const emit = defineEmits<{
  view: [value: AnalysisView];
  strokesCollapsed: [value: boolean];
  stroke: [stroke: StrokeModel];
  evidence: [evidence: EvidenceModel];
}>();
const root = ref<HTMLElement | null>(null);
const density = ref<"full" | "compact">("full");
let observer: ResizeObserver | undefined;
const activeRally = computed(() =>
  props.model.rallies.find((rally) => rally.id === props.activeId) ?? null,
);
/** In a gap the window keeps the Rally just played, so its commentary can still be read. */
const shownRally = computed(() => activeRally.value ?? rallyAtOrBefore(props.model.rallies, props.currentTime));
const previous = computed(() => !activeRally.value && shownRally.value !== null);
const context = computed(() =>
  analysisContextSummary(shownRally.value, previous.value, props.activeStrokeIndex),
);
const commentary = useSegmentCommentary(computed(() => props.model), useMatchContext().refreshMatch);
const commentaryRequest = computed(() =>
  shownRally.value ? commentary.state(shownRally.value) : { kind: "none" as const },
);
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
      :aria-label="[context.title, context.status, ...context.details].join(' · ')"
    >
      <div class="analysis-context-header__identity">
        <strong>{{ context.title }}</strong>
        <span>{{ context.status }}</span>
      </div>
      <p><span v-for="detail in context.details" :key="detail">{{ detail }}</span></p>
    </header>
    <nav class="analysis-tabs" aria-label="分析檢視">
      <button type="button" :aria-pressed="view === 'analysis'" @click="emit('view', 'analysis')">回合</button>
      <button type="button" :aria-pressed="view === 'court'" :disabled="!shownRally" @click="emit('view', 'court')">擊球點</button>
    </nav>
    <RallyInspector
      v-if="view === 'analysis'"
      :model="model"
      :rally="shownRally"
      :previous="previous"
      :selected-stroke-index="activeStrokeIndex"
      :commentary-request="commentaryRequest"
      :strokes-collapsed="strokesCollapsed"
      @strokes-collapsed="emit('strokesCollapsed', $event)"
      @stroke="emit('stroke', $event)"
      @evidence="emit('evidence', $event)"
      @request-commentary="commentary.request"
    />
    <div v-else class="analysis-court-view">
      <RallyCourtMap
        v-if="shownRally"
        :rally="shownRally"
        :players="model.players"
        :selected-stroke-index="activeStrokeIndex"
        :calibration-unconfirmed="model.source?.limitations.some((item) => item.includes('球場為自動校正'))"
        @stroke="emit('stroke', $event)"
      />
      <p v-else class="empty-state">比賽開始後即可檢視擊球點。</p>
    </div>
  </div>
</template>
