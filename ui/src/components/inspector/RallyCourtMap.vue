<script setup lang="ts">
import { computed } from "vue";
import type { RallyModel, StrokeModel } from "../../domain/models";
import { formatPreciseTime } from "../../format";

const props = defineProps<{
  rally: RallyModel;
  players: { a: string; b: string };
  selectedStrokeIndex: number | null;
  calibrationUnconfirmed?: boolean;
}>();
const emit = defineEmits<{ stroke: [stroke: StrokeModel] }>();
const positioned = computed(() => props.rally.hits?.filter((hit) => !!hit.courtPosition) ?? []);
const count = computed(() => props.rally.hits?.length ?? 0);
const selected = computed(() => positioned.value.find((hit) => hit.eventIndex === props.selectedStrokeIndex));
const hasUnknownPlayer = computed(() => positioned.value.some((hit) => !hit.hitter));
const playerLabel = (hit: StrokeModel) => hit.hitter === "a" ? props.players.a :
  hit.hitter === "b" ? props.players.b :
  `未知球員（畫面${hit.hitterSide === "top" ? "上方" : "下方"}）`;
const courtX = (hit: StrokeModel) => 10 + hit.courtPosition!.x * 200;
const courtY = (hit: StrokeModel) => 10 + hit.courtPosition!.y * 440;
const metresY = (metres: number) => 10 + metres / 13.41 * 440;
const metresX = (metres: number) => 10 + metres / 6.1 * 200;
function selectOnKey(event: KeyboardEvent, hit: StrokeModel) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    emit("stroke", hit);
  }
}
</script>

<template>
  <section class="rally-court-map" aria-label="擊球位置球場圖">
    <header class="rally-court-map__header">
      <div><span class="section-kicker">SPATIAL VIEW</span><h2>擊球位置</h2></div>
      <span v-if="positioned.length < count" class="rally-court-map__coverage">{{ positioned.length }} / {{ count }} 個擊球位置可用</span>
      <span v-else class="rally-court-map__coverage">{{ count }} 個擊球位置</span>
    </header>
    <p v-if="!positioned.length" class="rally-court-map__empty">此片段沒有足夠的定位資料。</p>
    <svg v-else class="rally-court-map__court" viewBox="-32 -32 284 524" preserveAspectRatio="xMidYMid meet" role="group" aria-label="遠端球場在上方；近端球場在下方；球場外側保留鄰界擊球位置">
      <rect x="10" y="10" width="200" height="440" class="court-line court-line--outer" />
      <line v-for="x in [0.46, 5.64]" :key="`single-${x}`" :x1="metresX(x)" y1="10" :x2="metresX(x)" y2="450" class="court-line" />
      <line v-for="y in [0.76, 4.72, 8.685, 12.65]" :key="`service-${y}`" x1="10" :y1="metresY(y)" x2="210" :y2="metresY(y)" class="court-line" />
      <line :x1="metresX(3.05)" y1="10" :x2="metresX(3.05)" :y2="metresY(4.72)" class="court-line" />
      <line :x1="metresX(3.05)" :y1="metresY(8.685)" :x2="metresX(3.05)" y2="450" class="court-line" />
      <line x1="4" :y1="metresY(6.705)" x2="216" :y2="metresY(6.705)" class="court-line court-line--net" />
      <g v-for="hit in positioned" :key="hit.eventIndex" :transform="`translate(${courtX(hit)}, ${courtY(hit)})`"
        class="court-shot" :class="[`court-shot--${hit.hitter ?? 'unknown'}`, { 'court-shot--selected': hit.eventIndex === selectedStrokeIndex, 'court-shot--estimated': hit.positionQuality === 'estimated' }]"
        role="button" tabindex="0" :aria-label="`第 ${hit.ordinal} 拍，${playerLabel(hit)}，${hit.type ?? '球種未提供'}，${formatPreciseTime(hit.time)}${hit.positionQuality === 'estimated' ? '，估計位置' : ''}`"
        :aria-pressed="hit.eventIndex === selectedStrokeIndex"
        @click="emit('stroke', hit)" @keydown="selectOnKey($event, hit)">
        <title>第 {{ hit.ordinal }} 拍 · {{ playerLabel(hit) }} · {{ hit.type ?? '球種未提供' }} · {{ formatPreciseTime(hit.time) }}{{ hit.positionQuality === 'estimated' ? ' · 估計位置' : '' }}</title>
        <circle r="12" class="court-shot__target" />
        <circle v-if="hit.positionQuality === 'estimated'" r="8" class="court-shot__estimate" />
        <circle r="5" class="court-shot__dot" />
        <circle v-if="hit.eventIndex === selectedStrokeIndex" r="10" class="court-shot__ring" />
        <text v-if="hit.eventIndex === selectedStrokeIndex" x="13" y="-10" class="court-shot__number">{{ hit.ordinal }}</text>
      </g>
    </svg>
    <div class="rally-court-map__legend" aria-label="球員圖例">
      <span><i class="court-legend-dot court-legend-dot--a" aria-hidden="true" />A · {{ players.a }}</span>
      <span><i class="court-legend-dot court-legend-dot--b" aria-hidden="true" />B · {{ players.b }}</span>
      <span v-if="hasUnknownPlayer"><i class="court-legend-dot court-legend-dot--unknown" aria-hidden="true" />未知球員</span>
    </div>
    <p v-if="selected" class="rally-court-map__reading">第 {{ selected.ordinal }} 拍 · {{ playerLabel(selected) }} · {{ selected.type ?? '球種未提供' }} · {{ formatPreciseTime(selected.time) }}<span v-if="selected.positionQuality === 'estimated'"> · 估計位置</span></p>
    <p class="rally-court-map__note">球員擊球時的腳下位置估計；虛線圈為估計點，界線外鄰近點可能受起跳與校正影響。得分方未判定。<span v-if="calibrationUnconfirmed">球場為自動校正，尚未人工確認。</span></p>
  </section>
</template>
