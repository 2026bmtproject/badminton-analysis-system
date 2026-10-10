<script setup lang="ts">
import type { EvidenceModel, MatchModel, RallyModel, StrokeModel } from "../../domain/models";
import type { CommentaryRequestState } from "../../state/segmentCommentary";
import RallyDetail from "./RallyDetail.vue";

defineProps<{
  model: MatchModel;
  rally: RallyModel | null;
  previous: boolean;
  selectedStrokeIndex: number | null;
  commentaryRequest: CommentaryRequestState;
  strokesCollapsed: boolean;
}>();
const emit = defineEmits<{
  strokesCollapsed: [value: boolean];
  stroke: [stroke: StrokeModel];
  evidence: [evidence: EvidenceModel];
  requestCommentary: [rally: RallyModel];
}>();
</script>

<template>
  <aside class="rally-panel" aria-label="回合分析" :data-rally-id="rally?.id ?? undefined">
    <p v-if="!rally" class="inspector-gap-detail">比賽開始後，這裡會顯示每個回合的賽評與擊球。</p>
    <RallyDetail
      v-else
      :model="model"
      :rally="rally"
      :previous="previous"
      :selected-stroke-index="selectedStrokeIndex"
      :commentary-request="commentaryRequest"
      :strokes-collapsed="strokesCollapsed"
      @strokes-collapsed="emit('strokesCollapsed', $event)"
      @stroke="emit('stroke', $event)"
      @evidence="emit('evidence', $event)"
      @request-commentary="emit('requestCommentary', $event)"
    />
  </aside>
</template>
