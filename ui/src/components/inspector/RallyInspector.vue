<script setup lang="ts">
import { computed } from "vue";
import type { EvidenceModel, MatchModel, RallyModel, StrokeModel } from "../../domain/models";
import RallyDetail from "./RallyDetail.vue";

const props = defineProps<{
  model: MatchModel;
  selectedId: number | null;
  activeId: number | null;
  selectedStrokeIndex: number | null;
  activeStrokeIndex: number | null;
}>();
const emit = defineEmits<{
  rally: [rally: RallyModel];
  back: [];
  stroke: [stroke: StrokeModel];
  evidence: [evidence: EvidenceModel];
  open: [rally: RallyModel, stroke: StrokeModel | null];
  previousStroke: [];
  nextStroke: [];
}>();
const active = computed(() =>
  props.model.rallies.find((rally) => rally.id === props.activeId) ?? null,
);
const activeStroke = computed(() =>
  active.value?.hits?.find((stroke) => stroke.eventIndex === props.activeStrokeIndex) ?? null,
);
</script>

<template>
  <aside class="rally-panel" aria-label="片段分析" :data-active-rally-id="activeId ?? undefined" :data-selected-rally-id="selectedId ?? undefined">
    <div v-if="!active" class="inspector-gap-detail">
      <strong>比賽空檔</strong>
      <p>等待下一段</p>
    </div>
    <RallyDetail
      v-else
      :model="model"
      :rally="active"
      :active-id="activeId"
      :selected-stroke-index="activeStrokeIndex"
      follow-playback
      @stroke="emit('stroke', $event)"
      @evidence="emit('evidence', $event)"
      @previous-stroke="emit('previousStroke')"
      @next-stroke="emit('nextStroke')"
    />
    <div v-if="active" class="inspector-open-rally">
      <button type="button" @click="emit('open', active, activeStroke)">開啟片段</button>
    </div>
  </aside>
</template>
