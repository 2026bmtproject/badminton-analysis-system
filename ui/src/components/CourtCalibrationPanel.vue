<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { importLocalMatch } from "../data/matchRepository";
import { clientToCourt, loadCourt, previewCourt, saveCourt, type CourtPoint, type CourtReview } from "../data/courtCalibration";
import { stageLabel } from "../data/stageLabels";

const props = defineProps<{
  matchId: string; available: boolean;
  /** An analysis is running; it publishes the Review itself once it finishes. */
  running: boolean;
  /** Why the court cannot be saved right now, or null. */
  locked: string | null;
  /** Set while a running task waits for the user to look at the court it just detected. */
  check: { ran: string[]; upcoming: string[] } | null;
}>();
/** `select` asks the analysis panel to tick these stages for the next run; `checked` ends the court check. */
const emit = defineEmits<{ updated: []; select: [stages: string[]]; checked: [] }>();
const open = ref(false);
const court = ref<CourtReview | null>(null);
const corners = ref<CourtPoint[]>([]);
const points = ref<CourtPoint[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const publishError = ref("");
const staleStages = ref<string[]>([]);
const unknownStages = ref<string[]>([]);
const rerunStages = ref<string[]>([]);
/** Readers the running task had not reached when the court was saved: they read the new one, nothing reruns. */
const appliedStages = ref<string[]>([]);
const drag = ref<number | null>(null);
const touched = ref<number[]>([]);
const svg = ref<SVGSVGElement | null>(null);
let previewVersion = 0;
let previewTimer: ReturnType<typeof setTimeout> | undefined;
const labels = ["左上", "右上", "左下", "右下"];
/** Handle sizes scale with the image so they read the same at any preview resolution. */
const unit = computed(() => court.value ? Math.max(court.value.width, court.value.height) / 100 : 1);
/** Room around the image so a corner on the edge stays whole and grabbable inside the rounded frame. */
const view = computed(() => {
  const pad = unit.value * 3;
  return { x: -pad, y: -pad, width: (court.value?.width ?? 0) + 2 * pad, height: (court.value?.height ?? 0) + 2 * pad };
});
/** Labels sit on the court side of each handle so they never run off the frame. */
function labelPlacement(index: number) {
  const left = index % 2 === 0, top = index < 2;
  return { x: (left ? 1.8 : -1.8) * unit.value, y: (top ? 3.2 : -2) * unit.value, anchor: left ? "start" : "end" };
}
const changed = computed(() => court.value !== null && JSON.stringify(corners.value) !== JSON.stringify(court.value.corners));
/**
 * Confirming an unchanged court still rewrites court.json, which every reader sees as a new court; mid-task that would
 * rerun them for nothing, so then only a moved corner saves ("看起來沒問題" covers the rest).
 */
const canConfirm = computed(() => court.value !== null &&
  (court.value.coordinateUnknown ? touched.value.length === 4 : changed.value || (!court.value.confirmed && !props.running)));

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
  rerunStages.value = []; appliedStages.value = [];
});
watch(() => props.available, value => { if (value && open.value) void reload(); });
// The service's own save lock was read with the court, so it is read again when the task's lock lifts.
watch(() => props.locked, () => { if (props.available && open.value) void reload(); });
/** A freshly detected court opens itself, so a crooked one is seen while the stages that do not read it run. */
watch(() => props.check !== null, pending => {
  if (!pending || open.value) return;
  open.value = true;
  rerunStages.value = []; appliedStages.value = [];
  if (props.available) void reload();
}, { immediate: true });
function looksFine() {
  open.value = false;
  reset();
  emit("checked");
}
const outdated = computed(() => [...staleStages.value, ...unknownStages.value]);
const statusLine = computed(() => {
  if (!court.value) return "";
  const parts = [court.value.confirmed ? "已確認" : "尚未確認"];
  if (court.value.detectionFailed) parts.push("自動偵測失敗，已套用預設角點");
  return parts.join(" · ");
});

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
  corners.value[drag.value] = clientToCourt(event.clientX, event.clientY, rect, view.value, court.value.width, court.value.height);
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
  if (!court.value || !canConfirm.value || props.locked || court.value.saveBlocked) return;
  saving.value = true; error.value = ""; publishError.value = "";
  const upcoming = props.check?.upcoming ?? [];
  try {
    const result = await saveCourt(props.matchId, court.value.revision, corners.value);
    staleStages.value = result.staleStages;
    unknownStages.value = result.unknownStages;
    rerunStages.value = result.rerunStages ?? [];
    appliedStages.value = upcoming.filter(name => !rerunStages.value.includes(name));
    await reload();
    emit("updated");
    if (props.check) emit("checked");
    // A running task publishes its own Review when it finishes, from the court it ends up using.
    if (props.running) return;
    try { await importLocalMatch(props.matchId); emit("updated"); }
    catch (cause) { publishError.value = cause instanceof Error ? cause.message : "回看資料更新失敗"; }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "保存失敗"; }
  finally { saving.value = false; }
}
</script>

<template>
  <section class="court-calibration" aria-label="場地校正">
    <button type="button" class="court-calibration-toggle" :aria-expanded="open" @click="toggle">場地校正<AppIcon :name="open ? 'chevron-up' : 'chevron-down'" /></button>
    <div v-if="open" class="court-calibration-body">
      <template v-if="!available">
        <p>還沒有場地結果，需要先執行「{{ stageLabel("court_detection") }}」。</p>
        <button type="button" class="button-secondary" @click="emit('select', ['court_detection'])">勾選{{ stageLabel("court_detection") }}</button>
      </template>
      <template v-else>
        <div v-if="check" class="court-check" role="status">
          <p><strong>球場偵測完成，請看一下線有沒有對齊場地。</strong>後面的分析照常進行，不用等。</p>
          <p>線大致貼著場地邊線就不用調：歪一點點不影響後續，球場只用來判斷誰是場上球員和換算站位。明顯歪掉（邊線偏到場外、角點落在別條線上）才需要拖曳角點調整。</p>
          <p v-if="check.ran.length">現在調整的話，已經用這個球場跑過的{{ check.ran.map(stageLabel).join("、") }}會自動重跑，需要多花時間。</p>
          <p v-else-if="check.upcoming.length">現在調整不用重跑任何階段：{{ check.upcoming.map(stageLabel).join("、") }}還沒開始，會直接使用調整後的球場。</p>
          <button type="button" class="button-secondary" @click="looksFine">看起來沒問題</button>
        </div>
        <p v-if="loading" role="status" class="secondary">載入場地預覽中…</p>
        <p v-if="court" class="secondary">{{ statusLine }}</p>
        <p v-if="court?.coordinateUnknown" class="warning">舊結果缺少影像尺寸，請在背景上重新拖曳四個角點後保存。</p>
        <svg v-if="court" ref="svg" class="court-calibration-image"
          :viewBox="`${view.x} ${view.y} ${view.width} ${view.height}`" role="img" aria-label="球場背景與可拖曳角點"
          @pointermove="move" @pointerup="up" @pointercancel="up">
          <image :href="court.image" x="0" y="0" :width="court.width" :height="court.height" />
          <line v-for="([a, b], index) in court.lines" :key="index"
            :x1="points[a]?.[0]" :y1="points[a]?.[1]" :x2="points[b]?.[0]" :y2="points[b]?.[1]" class="court-calibration-line" />
          <g v-for="([x, y], index) in corners" :key="index" class="court-calibration-handle"
            :transform="`translate(${x} ${y})`" tabindex="0" role="button"
            :aria-label="`${labels[index]}角點，X ${Math.round(x)}，Y ${Math.round(y)}；方向鍵微調，Shift 加方向鍵移動十像素`"
            @pointerdown.prevent="down($event, index)" @keydown="nudge($event, index)">
            <circle class="court-calibration-hit" :r="unit * 2.6" />
            <circle class="court-calibration-dot" :r="unit" />
            <text :x="labelPlacement(index).x" :y="labelPlacement(index).y" :text-anchor="labelPlacement(index).anchor"
              :font-size="unit * 1.9">{{ labels[index] }}</text>
          </g>
        </svg>
        <p v-if="court && (locked || court.saveBlocked)" class="warning">{{ locked ?? "分析進行中，暫時不能保存場地。" }}</p>
        <div v-if="court" class="court-calibration-actions">
          <button type="button" class="button-secondary" :disabled="saving || !changed" @click="reset">還原</button>
          <button type="button" class="button-primary" :disabled="saving || loading || !canConfirm || !!locked || court.saveBlocked || !!error" @click="save">{{ saving ? "保存中…" : "確認並保存" }}</button>
        </div>
        <p v-if="rerunStages.length" class="warning" role="status">場地已保存。{{ rerunStages.map(stageLabel).join("、") }}用舊球場跑過，分析會自動重跑。</p>
        <p v-else-if="appliedStages.length" class="secondary" role="status">場地已保存，{{ appliedStages.map(stageLabel).join("、") }}會直接使用新球場，不需重跑。</p>
        <p v-if="error" class="error" role="alert">{{ error }} <button type="button" @click="reload">重新載入</button></p>
        <p v-if="publishError" class="error" role="alert">場地已保存，但回看更新失敗：{{ publishError }}。原有回看仍可使用。</p>
        <p v-if="outdated.length" class="warning court-calibration-outdated">場地已保存，這些階段的結果可能需要重跑：{{ outdated.map(stageLabel).join("、") }}
          <button type="button" class="button-secondary" @click="emit('select', outdated)">勾選這些階段</button></p>
      </template>
    </div>
  </section>
</template>
