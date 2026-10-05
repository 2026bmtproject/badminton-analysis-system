<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { importLocalMatch } from "../data/matchRepository";
import { clientToCourt, loadCourt, previewCourt, saveCourt, type CourtPoint, type CourtReview } from "../data/courtCalibration";

const props = defineProps<{ matchId: string; available: boolean; running: boolean; initialOpen?: boolean }>();
const emit = defineEmits<{ updated: []; detect: [] }>();
const open = ref(Boolean(props.initialOpen));
const court = ref<CourtReview | null>(null);
const corners = ref<CourtPoint[]>([]);
const points = ref<CourtPoint[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const publishError = ref("");
const staleStages = ref<string[]>([]);
const unknownStages = ref<string[]>([]);
const drag = ref<number | null>(null);
const touched = ref<number[]>([]);
const svg = ref<SVGSVGElement | null>(null);
let previewVersion = 0;
let previewTimer: ReturnType<typeof setTimeout> | undefined;
const labels = ["左上", "右上", "左下", "右下"];
const changed = computed(() => court.value !== null && JSON.stringify(corners.value) !== JSON.stringify(court.value.corners));
const canConfirm = computed(() => court.value !== null &&
  (court.value.coordinateUnknown ? touched.value.length === 4 : changed.value || !court.value.confirmed));

function reset() {
  if (!court.value) return;
  previewVersion++;
  if (previewTimer) clearTimeout(previewTimer);
  corners.value = court.value.corners.map(([x, y]) => [x, y]);
  points.value = court.value.points;
  touched.value = [];
  error.value = "";
}
async function reload() {
  loading.value = true; error.value = "";
  try { court.value = await loadCourt(props.matchId); reset(); }
  catch (cause) { court.value = null; error.value = cause instanceof Error ? cause.message : "無法載入場地"; }
  finally { loading.value = false; }
}
function toggle() {
  if (open.value) { open.value = false; reset(); return; }
  open.value = true;
  if (props.available) void reload();
}
watch(() => props.matchId, () => {
  open.value = false; court.value = null; error.value = ""; staleStages.value = []; unknownStages.value = [];
});
watch(() => props.available, value => { if (value && open.value) void reload(); });
watch(() => props.initialOpen, value => { if (value && !open.value) toggle(); });
if (props.initialOpen && props.available) void reload();

function queuePreview(immediate = false) {
  if (!court.value) return;
  if (previewTimer) clearTimeout(previewTimer);
  const version = ++previewVersion;
  const snapshot = corners.value.map(([x, y]): CourtPoint => [x, y]);
  previewTimer = setTimeout(async () => {
    try {
      const result = await previewCourt(props.matchId, court.value!.revision, snapshot);
      if (version === previewVersion) { points.value = result.points; error.value = ""; }
    } catch (cause) {
      if (version === previewVersion) error.value = cause instanceof Error ? cause.message : "角點無效";
    }
  }, immediate ? 0 : 90);
}
function move(event: PointerEvent) {
  if (drag.value === null || !court.value || !svg.value) return;
  const rect = svg.value.getBoundingClientRect();
  corners.value[drag.value] = clientToCourt(event.clientX, event.clientY, rect, court.value.width, court.value.height);
  corners.value = [...corners.value];
  queuePreview();
}
function down(event: PointerEvent, index: number) {
  if (!court.value) return;
  drag.value = index;
  if (!touched.value.includes(index)) touched.value = [...touched.value, index];
  svg.value?.setPointerCapture(event.pointerId);
  move(event);
}
function up(event: PointerEvent) {
  if (drag.value === null) return;
  move(event); drag.value = null; queuePreview(true);
  if (svg.value?.hasPointerCapture(event.pointerId)) svg.value.releasePointerCapture(event.pointerId);
}
function nudge(event: KeyboardEvent, index: number) {
  if (!court.value) return;
  const delta = event.shiftKey ? 10 : 1;
  const directions: Record<string, CourtPoint> = {
    ArrowLeft: [-delta, 0], ArrowRight: [delta, 0],
    ArrowUp: [0, -delta], ArrowDown: [0, delta],
  };
  const offset = directions[event.key];
  if (!offset) return;
  event.preventDefault();
  const [x, y] = corners.value[index];
  corners.value[index] = [Math.max(0, Math.min(court.value.width, x + offset[0])),
    Math.max(0, Math.min(court.value.height, y + offset[1]))];
  corners.value = [...corners.value];
  if (!touched.value.includes(index)) touched.value = [...touched.value, index];
  queuePreview(true);
}
async function save() {
  if (!court.value || !canConfirm.value || props.running || court.value.saveBlocked) return;
  saving.value = true; error.value = ""; publishError.value = "";
  try {
    const result = await saveCourt(props.matchId, court.value.revision, corners.value);
    staleStages.value = result.staleStages;
    unknownStages.value = result.unknownStages;
    await reload();
    emit("updated");
    try { await importLocalMatch(props.matchId); emit("updated"); }
    catch (cause) { publishError.value = cause instanceof Error ? cause.message : "回看資料更新失敗"; }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "保存失敗"; }
  finally { saving.value = false; }
}
</script>

<template>
  <section class="court-calibration" aria-label="場地校正">
    <button type="button" :aria-expanded="open" @click="toggle">場地校正 {{ open ? "▴" : "▾" }}</button>
    <div v-if="open" class="court-calibration-body">
      <template v-if="!available">
        <p>尚無場地結果。請先執行既有的「球場邊界辨識」階段。</p>
        <button type="button" @click="emit('detect')">選取球場邊界辨識</button>
      </template>
      <template v-else>
        <p v-if="loading" role="status">載入場地預覽中…</p>
        <p v-if="court">狀態：{{ court.confirmed ? "已確認" : "尚未確認" }} · 自動偵測：{{ court.detectionFailed ? "失敗，使用預設角點" : "成功" }}</p>
        <p v-if="court?.legacyPreview">此結果未保存預覽，已依原取樣設定重建背景。</p>
        <p v-if="court?.coordinateUnknown">舊資料沒有記錄校正影像尺寸，無法安全疊加原角點。請在重建背景上逐一標記四角後保存。</p>
        <svg v-if="court" ref="svg" class="court-calibration-image"
          :viewBox="`0 0 ${court.width} ${court.height}`" role="img" aria-label="球場背景與可拖曳角點"
          @pointermove="move" @pointerup="up" @pointercancel="up">
          <image :href="court.image" x="0" y="0" :width="court.width" :height="court.height" />
          <line v-for="([a, b], index) in court.lines" :key="index"
            :x1="points[a]?.[0]" :y1="points[a]?.[1]" :x2="points[b]?.[0]" :y2="points[b]?.[1]" class="court-calibration-line" />
          <g v-for="([x, y], index) in corners" :key="index" class="court-calibration-handle"
            :transform="`translate(${x} ${y})`" tabindex="0" role="button"
            :aria-label="`${labels[index]}角點，X ${Math.round(x)}，Y ${Math.round(y)}；方向鍵微調，Shift 加方向鍵移動十像素`"
            @pointerdown.prevent="down($event, index)" @keydown="nudge($event, index)">
            <circle r="12" /><text x="16" y="-14">{{ labels[index] }}</text>
          </g>
        </svg>
        <p v-if="court && (running || court.saveBlocked)">此比賽正在分析或 worker 狀態待確認，暫時不能保存場地。</p>
        <div v-if="court" class="court-calibration-actions">
          <button type="button" :disabled="saving || !changed" @click="reset">重設為本次載入的校正</button>
          <button type="button" :disabled="saving" @click="toggle">放棄修改</button>
          <button type="button" :disabled="saving || loading || !canConfirm || running || court.saveBlocked || !!error" @click="save">{{ saving ? "保存中…" : "確認並保存" }}</button>
        </div>
        <p v-if="error" class="error" role="alert">{{ error }} <button type="button" @click="reload">重新載入</button></p>
        <p v-if="publishError" class="error" role="alert">場地已保存，但回看更新失敗：{{ publishError }}。原有回看仍可使用。</p>
        <p v-if="staleStages.length">以下已完成階段的輸入已過期：{{ staleStages.join("、") }}。可在上方分析項目中查看計畫，自行決定是否重跑。</p>
        <p v-if="unknownStages.length">以下舊結果的輸入狀態無法確認：{{ unknownStages.join("、") }}。可在上方查看分析計畫。</p>
      </template>
    </div>
  </section>
</template>
