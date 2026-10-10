<script setup lang="ts">
import { computed } from "vue";
import type { RallyModel, StrokeModel } from "../../domain/models";
import { formatPreciseTime, playerName } from "../../format";

const props = defineProps<{
  rally: RallyModel;
  players: { a: string; b: string };
  selectedStrokeIndex: number | null;
  calibrationUnconfirmed?: boolean;
}>();
const emit = defineEmits<{ stroke: [stroke: StrokeModel] }>();
const positioned = computed(() => props.rally.hits?.filter((hit) => !!hit.courtPosition) ?? []);
const missing = computed(() => (props.rally.hits?.length ?? 0) - positioned.value.length);
const playerLabel = (hit: StrokeModel) => hit.hitter ? playerName(props.players[hit.hitter]) :
  `未知球員（畫面${hit.hitterSide === "top" ? "上方" : "下方"}）`;
const courtX = (hit: StrokeModel) => 10 + hit.courtPosition!.x * 200;
const courtY = (hit: StrokeModel) => 10 + hit.courtPosition!.y * 440;
const metresY = (metres: number) => 10 + metres / 13.41 * 440;
const metresX = (metres: number) => 10 + metres / 6.1 * 200;
/** The path from hit to hit, in the order they were played. */
const route = computed(() => positioned.value.map((hit) => `${courtX(hit)},${courtY(hit)}`).join(" "));
/** Only the shot being played stands out; when none is, every shot reads at full strength. */
const dimmed = (hit: StrokeModel) => props.selectedStrokeIndex !== null && hit.eventIndex !== props.selectedStrokeIndex;
function selectOnKey(event: KeyboardEvent, hit: StrokeModel) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    emit("stroke", hit);
  }
}
</script>

<template>
  <section class="rally-court-map" aria-label="擊球點">
    <p v-if="!positioned.length" class="rally-court-map__empty">這個回合沒有可用的擊球位置。</p>
    <svg v-else class="rally-court-map__court" viewBox="-32 -32 284 524" preserveAspectRatio="xMidYMid meet" role="group" aria-label="擊球點">
      <rect x="10" y="10" width="200" height="440" class="court-line court-line--outer" />
      <line v-for="x in [0.46, 5.64]" :key="`single-${x}`" :x1="metresX(x)" y1="10" :x2="metresX(x)" y2="450" class="court-line" />
      <line v-for="y in [0.76, 4.72, 8.685, 12.65]" :key="`service-${y}`" x1="10" :y1="metresY(y)" x2="210" :y2="metresY(y)" class="court-line" />
      <line :x1="metresX(3.05)" y1="10" :x2="metresX(3.05)" :y2="metresY(4.72)" class="court-line" />
      <line :x1="metresX(3.05)" :y1="metresY(8.685)" :x2="metresX(3.05)" y2="450" class="court-line" />
      <line x1="4" :y1="metresY(6.705)" x2="216" :y2="metresY(6.705)" class="court-line court-line--net" />
      <polyline v-if="positioned.length > 1" :points="route" class="court-route" />
      <g v-for="hit in positioned" :key="hit.eventIndex" :transform="`translate(${courtX(hit)}, ${courtY(hit)})`"
        class="court-shot" :class="[`court-shot--${hit.hitter ?? 'unknown'}`, { 'court-shot--selected': hit.eventIndex === selectedStrokeIndex, 'court-shot--estimated': hit.positionQuality === 'estimated', 'court-shot--dimmed': dimmed(hit) }]"
        role="button" tabindex="0" :aria-label="`第 ${hit.ordinal} 拍，${playerLabel(hit)}，${hit.type ?? '球種未知'}，${formatPreciseTime(hit.time)}${hit.positionQuality === 'estimated' ? '，估計位置' : ''}`"
        :aria-pressed="hit.eventIndex === selectedStrokeIndex"
        @click="emit('stroke', hit)" @keydown="selectOnKey($event, hit)">
        <title>第 {{ hit.ordinal }} 拍 · {{ playerLabel(hit) }} · {{ hit.type ?? '球種未知' }}{{ hit.positionQuality === 'estimated' ? ' · 估計位置' : '' }}</title>
        <circle r="12" class="court-shot__target" />
        <circle v-if="hit.eventIndex === selectedStrokeIndex" r="9" class="court-shot__ring" />
        <circle r="5.5" class="court-shot__dot" />
      </g>
    </svg>
    <p v-if="positioned.length && (missing > 0 || calibrationUnconfirmed)" class="rally-court-map__note">
      <span v-if="missing > 0">{{ missing }} 拍沒有位置</span>
      <span v-if="calibrationUnconfirmed">球場尚未人工校正</span>
    </p>
  </section>
</template>
