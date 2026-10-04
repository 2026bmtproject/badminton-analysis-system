<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import CourtCalibrationPanel from "./CourtCalibrationPanel.vue";
import { importLocalMatch, loadCatalog, loadMatch } from "../data/matchRepository";
import {
  getPipelineLogs, getPipelineTask, listPipelineStages, previewPipeline, startPipeline,
  taskStatusLabel,
  type LocalAnalysisMatch, type PipelinePlan, type PipelineStage, type PipelineTask,
} from "../data/pipelineTasks";

const props = defineProps<{ match: LocalAnalysisMatch; hasReview: boolean }>();
const emit = defineEmits<{ updated: [] }>();
const stages = ref<PipelineStage[]>([]);
const selected = ref<string[]>([]);
const mode = ref<"continue" | "rerun-selected">("continue");
const plan = ref<PipelinePlan | null>(null);
const task = ref<PipelineTask | null>(props.match.latestTask);
const busy = ref(false);
const publishing = ref(false);
const error = ref("");
const publishError = ref("");
const publishDone = ref(false);
const showLogs = ref(false);
const lines = ref<string[]>([]);
const logOffset = ref(0);
const clock = ref(Date.now());
let pollTimer: ReturnType<typeof setInterval> | undefined;

const running = computed(() => task.value?.status === "queued" || task.value?.status === "running");
const elapsed = computed(() => {
  if (!task.value?.startedAt) return "—";
  const end = task.value.finishedAt ? Date.parse(task.value.finishedAt) : clock.value;
  return `${Math.max(0, Math.floor((end - Date.parse(task.value.startedAt)) / 1000))} 秒`;
});

watch([selected, mode], () => { plan.value = null; }, { deep: true });
watch(() => props.match.id, () => {
  selected.value = []; plan.value = null; task.value = props.match.latestTask;
  lines.value = []; logOffset.value = 0; error.value = "";
  publishError.value = ""; publishDone.value = false;
  if (task.value?.status === "succeeded") void syncReviewAfterSuccess();
});
watch(() => props.match.latestTask, (value) => {
  if (!task.value || (value && task.value.id !== value.id)) task.value = value;
});

async function preview() {
  error.value = ""; busy.value = true;
  try { plan.value = await previewPipeline(props.match.id, selected.value, mode.value); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法產生計畫"; }
  finally { busy.value = false; }
}

async function begin() {
  if (!plan.value) return;
  error.value = ""; busy.value = true;
  try {
    task.value = await startPipeline(plan.value);
    lines.value = []; logOffset.value = 0; publishDone.value = false; publishError.value = "";
    emit("updated");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "無法開始分析";
    plan.value = null;
  } finally { busy.value = false; }
}

async function publish() {
  if ((!task.value || task.value.status !== "succeeded") && !props.match.hasSegments) return;
  if (publishing.value) return;
  publishing.value = true; publishError.value = "";
  try {
    await importLocalMatch(props.match.id);
    publishDone.value = true;
    emit("updated");
  } catch (cause) {
    publishError.value = cause instanceof Error ? cause.message : "回看更新失敗";
  } finally { publishing.value = false; }
}

async function syncReviewAfterSuccess() {
  if (task.value?.status !== "succeeded") return;
  try {
    const entry = (await loadCatalog()).find(row => row.id === `match:${props.match.id}`);
    if (entry && task.value.finishedAt) {
      const existing = await loadMatch(entry, { fresh: true });
      if (existing.source?.importedAt &&
          Date.parse(existing.source.importedAt) >= Date.parse(task.value.finishedAt)) {
        publishDone.value = true;
        return;
      }
    }
  } catch {
    // An unreadable old cache should not prevent a fresh export.
  }
  await publish();
}

async function moreLogs() {
  if (!task.value) return;
  try {
    const page = await getPipelineLogs(task.value.id, logOffset.value);
    lines.value.push(...page.lines);
    logOffset.value = page.nextOffset;
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取 log"; }
}

async function poll() {
  clock.value = Date.now();
  if (!task.value || !running.value) return;
  try {
    const previous = task.value.status;
    task.value = await getPipelineTask(task.value.id);
    if (showLogs.value) await moreLogs();
    if (previous !== task.value.status && !running.value) {
      emit("updated");
      if (task.value.status === "succeeded") await syncReviewAfterSuccess();
    }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "任務狀態連線失敗"; }
}

onMounted(async () => {
  try { stages.value = await listPipelineStages(); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法連接分析服務"; }
  if (task.value?.status === "succeeded") await syncReviewAfterSuccess();
  pollTimer = setInterval(() => { void poll(); }, 1500);
});
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<template>
  <section class="pipeline-panel" :aria-label="`${match.id} 分析設定`">
    <header><h3>{{ match.id }}</h3><span>{{ match.hasSegments ? "已有分段資料" : "尚未分析分段" }}</span></header>
    <fieldset :disabled="running || busy">
      <legend>選擇分析項目</legend>
      <label v-for="stage in stages" :key="stage.name" class="pipeline-stage-choice">
        <input v-model="selected" type="checkbox" :value="stage.name" />
        <span>{{ stage.description }} <small>{{ stage.name }}{{ stage.usesGemini ? " · Gemini" : "" }}</small></span>
      </label>
    </fieldset>
    <fieldset :disabled="running || busy" class="pipeline-mode">
      <legend>執行方式</legend>
      <label><input v-model="mode" type="radio" value="continue" /> 沿用有效結果，補跑缺少或過期階段</label>
      <label><input v-model="mode" type="radio" value="rerun-selected" /> 重跑選取項目</label>
    </fieldset>
    <button type="button" :disabled="!selected.length || running || busy" @click="preview">查看執行計畫</button>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <section v-if="plan" class="pipeline-plan" aria-label="執行計畫">
      <h4>執行計畫</h4>
      <p v-if="plan.includesGemini">此計畫會呼叫 Gemini API。</p>
      <ol><li v-for="row in plan.stages" :key="row.name">
        <strong>{{ row.name }}</strong> · {{ row.action === "run" ? "執行" : "沿用" }} · {{ row.reason }}
        <span v-if="row.unknown">（舊結果的輸入狀態未知）</span>
      </li></ol>
      <p v-if="plan.affectedOutsideScope.length">本次範圍外受影響下游：{{ plan.affectedOutsideScope.join("、") }}</p>
      <button type="button" :disabled="busy || running || !plan.stages.some(row => row.action === 'run')" @click="begin">開始分析</button>
    </section>
    <section v-if="task" class="pipeline-task">
      <h4 role="status">任務：{{ taskStatusLabel(task.status) }}</h4>
      <p>經過時間：{{ elapsed }}<span v-if="task.currentStage"> · 目前階段：{{ task.currentStage }}</span></p>
      <p v-if="running">關閉頁面後分析會繼續。重新開啟即可查看進度。</p>
      <ul><li v-for="(stage, name) in task.stageStates" :key="name">
        {{ name }}：{{ taskStatusLabel(stage.status) }}<span v-if="stage.progress !== null"> · {{ Math.round(stage.progress * 100) }}%</span>
      </li></ul>
      <p v-if="task.error" class="error" role="alert">{{ task.error }}<span v-if="task.exitCode !== null">（exit {{ task.exitCode }}）</span></p>
      <button type="button" :aria-expanded="showLogs" @click="showLogs = !showLogs; if (showLogs) moreLogs()">{{ showLogs ? "收合 log" : "查看 log" }}</button>
      <div v-if="showLogs" class="pipeline-logs"><pre>{{ lines.join('\n') }}</pre><button type="button" @click="moreLogs">載入更多</button></div>
      <p v-if="task.status === 'succeeded'">分析已完成。{{ publishDone ? "回看資料已更新。" : "回看資料可重新整理。" }}</p>
      <button v-if="task.status === 'succeeded'" type="button" :disabled="publishing" @click="publish">{{ publishing ? "更新中…" : "重新整理回看資料" }}</button>
      <p v-if="task.status === 'failed' || task.status === 'interrupted'">可重新查看計畫並建立新任務重試。</p>
    </section>
    <p v-if="publishError" class="error" role="alert">回看更新失敗：{{ publishError }}。原有回看資料仍可使用。</p>
    <CourtCalibrationPanel :match-id="match.id" :available="match.completedStages.includes('court_detection')"
      :running="running" @updated="emit('updated')"
      @detect="selected = ['court_detection']; mode = 'continue'; plan = null" />
    <RouterLink v-if="hasReview" :to="{ name: 'match-review', params: { matchId: `match:${match.id}` } }">查看已有結果 →</RouterLink>
    <button v-else-if="match.hasSegments" type="button" :disabled="publishing" @click="publish">匯入已有分析結果</button>
  </section>
</template>
