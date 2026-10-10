<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import CourtCalibrationPanel from "./CourtCalibrationPanel.vue";
import StageProgress from "./ui/StageProgress.vue";
import { stageLabel } from "../data/stageLabels";
import { useTaskFeed } from "../composables/useTaskFeed";
import { importLocalMatch } from "../data/matchRepository";
import {
  LOG_PAGE_SIZE, getPipelineLogs, getPipelineTask, listPipelineStages, planReasonLabel, previewPipeline,
  startPipeline, taskElapsed, taskProgress, taskStatusLabel,
  type LocalAnalysisMatch, type PipelinePlan, type PipelineStage, type PipelineTask,
} from "../data/pipelineTasks";

const props = defineProps<{ match: LocalAnalysisMatch; hasReview: boolean }>();
const emit = defineEmits<{ updated: [] }>();
const stages = ref<PipelineStage[]>([]);
const selected = ref<string[]>([]);
const mode = ref<"continue" | "rerun-selected">("continue");
const plan = ref<PipelinePlan | null>(null);
const planning = ref(false);
const task = ref<PipelineTask | null>(props.match.latestTask);
const busy = ref(false);
const importing = ref(false);
const importError = ref("");
const error = ref("");
const { reviewSync, syncReview, refresh: refreshTasks } = useTaskFeed();
const showLogs = ref(false);
const lines = ref<string[]>([]);
const logOffset = ref(0);
const clock = ref(Date.now());
let pollTimer: ReturnType<typeof setInterval> | undefined;
let planVersion = 0;
let fetchingLogs = false;

const running = computed(() => task.value?.status === "queued" || task.value?.status === "running");
const locked = computed(() => running.value || busy.value);
const elapsed = computed(() => task.value ? taskElapsed(task.value, clock.value) : null);
const overall = computed(() => task.value ? taskProgress(task.value) : null);
const incomplete = computed(() => stages.value.map(stage => stage.name).filter(name => !props.match.completedStages.includes(name)));
const runRows = computed(() => plan.value?.stages.filter(row => row.action === "run") ?? []);
const skipRows = computed(() => plan.value?.stages.filter(row => row.action === "skip") ?? []);
/** The shared feed publishes a succeeded task's Review; this panel only reports on it. */
const review = computed(() => task.value ? reviewSync.value[task.value.id] : undefined);

/** The plan follows the selection, so there is no separate "preview" step before starting. */
async function refreshPlan() {
  const version = ++planVersion;
  plan.value = null;
  if (!selected.value.length) { planning.value = false; return; }
  planning.value = true; error.value = "";
  try {
    const result = await previewPipeline(props.match.id, selected.value, mode.value);
    if (version === planVersion) plan.value = result;
  } catch (cause) {
    if (version === planVersion) error.value = cause instanceof Error ? cause.message : "無法產生執行計畫";
  } finally {
    if (version === planVersion) planning.value = false;
  }
}

watch([selected, mode], () => { void refreshPlan(); }, { deep: true });
watch(() => props.match.completedStages.join(), () => { if (selected.value.length && !running.value) void refreshPlan(); });
watch(() => props.match.id, () => {
  selected.value = []; plan.value = null; task.value = props.match.latestTask;
  lines.value = []; logOffset.value = 0; error.value = ""; importError.value = "";
  if (task.value?.status === "succeeded") void syncReview(task.value);
});
watch(() => review.value?.state, state => { if (state === "done") emit("updated"); });
watch(() => props.match.latestTask, (value) => {
  if (!task.value || (value && task.value.id !== value.id)) task.value = value;
});

function pick(names: string[]) {
  selected.value = [...names];
  mode.value = "continue";
}

async function launch(next: PipelinePlan) {
  task.value = await startPipeline(next);
  lines.value = []; logOffset.value = 0;
  void refreshTasks();
  emit("updated");
}

async function begin() {
  if (!plan.value) return;
  error.value = ""; busy.value = true;
  try { await launch(plan.value); }
  catch (cause) {
    error.value = cause instanceof Error ? cause.message : "無法開始分析";
    void refreshPlan();
  } finally { busy.value = false; }
}

async function retry() {
  if (!task.value) return;
  const { requestedStages, mode: previousMode } = task.value.plan;
  selected.value = [...requestedStages]; mode.value = previousMode;
  error.value = ""; busy.value = true;
  try { await launch(await previewPipeline(props.match.id, requestedStages, previousMode)); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法重新開始分析"; }
  finally { busy.value = false; }
}

/** Imports results that exist without a task from this session, e.g. analysed before the UI existed. */
async function importExisting() {
  if (importing.value) return;
  importing.value = true; importError.value = "";
  try {
    await importLocalMatch(props.match.id);
    emit("updated");
  } catch (cause) {
    importError.value = cause instanceof Error ? cause.message : "匯入失敗";
  } finally { importing.value = false; }
}

/** Reads every log line not yet shown, page by page, so the panel never needs a "load more" button. */
async function fetchLogs() {
  if (!task.value || fetchingLogs) return;
  fetchingLogs = true;
  try {
    for (;;) {
      const page = await getPipelineLogs(task.value.id, logOffset.value);
      lines.value.push(...page.lines);
      logOffset.value = page.nextOffset;
      if (page.lines.length < LOG_PAGE_SIZE) break;
    }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取詳細記錄"; }
  finally { fetchingLogs = false; }
}

function toggleLogs() {
  showLogs.value = !showLogs.value;
  if (showLogs.value) void fetchLogs();
}

async function poll() {
  clock.value = Date.now();
  if (!task.value || !running.value) return;
  try {
    const previous = task.value.status;
    task.value = await getPipelineTask(task.value.id);
    if (showLogs.value) await fetchLogs();
    if (previous !== task.value.status && !running.value) {
      emit("updated");
      if (task.value.status === "succeeded") void syncReview(task.value, { verify: false });
    }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "任務狀態連線失敗"; }
}

onMounted(async () => {
  try { stages.value = await listPipelineStages(); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法連接分析服務"; }
  if (task.value?.status === "succeeded") void syncReview(task.value);
  pollTimer = setInterval(() => { void poll(); }, 1500);
});
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); });
</script>

<template>
  <section class="pipeline-panel" :aria-label="`${match.id} 分析設定`">
    <section class="pipeline-setup" aria-labelledby="pipeline-setup-title">
      <header class="pipeline-section-header">
        <h3 id="pipeline-setup-title">分析項目</h3>
        <button type="button" class="button-secondary" :disabled="locked || !incomplete.length" @click="pick(incomplete)">全選未完成</button>
      </header>
      <fieldset :disabled="locked" class="pipeline-stage-list">
        <legend class="sr-only">選擇分析項目</legend>
        <label v-for="stage in stages" :key="stage.name" class="pipeline-stage-choice">
          <input v-model="selected" type="checkbox" :value="stage.name" />
          <span>{{ stageLabel(stage.name) }}</span>
          <span class="status-chip" :data-status="match.completedStages.includes(stage.name) ? 'succeeded' : 'missing'">{{ match.completedStages.includes(stage.name) ? "已完成" : "未執行" }}</span>
        </label>
      </fieldset>
      <fieldset :disabled="locked" class="pipeline-mode">
        <legend>已完成的項目</legend>
        <label><input v-model="mode" type="radio" value="continue" /> 沿用，只補跑缺少或過期的</label>
        <label><input v-model="mode" type="radio" value="rerun-selected" /> 勾選的全部重跑</label>
      </fieldset>
      <div v-if="selected.length && !running" class="pipeline-plan" aria-label="執行計畫" aria-live="polite">
        <p v-if="planning" class="secondary">正在計算要執行的階段…</p>
        <template v-else-if="plan">
          <p v-if="!runRows.length" class="secondary">勾選的項目都已是最新結果，不需要執行。</p>
          <ol v-else><li v-for="row in runRows" :key="row.name"><strong>{{ stageLabel(row.name) }}</strong><span class="secondary">{{ planReasonLabel(row) }}</span></li></ol>
          <details v-if="skipRows.length" class="pipeline-plan-skipped">
            <summary>其餘 {{ skipRows.length }} 項沿用既有結果</summary>
            <ul><li v-for="row in skipRows" :key="row.name">{{ stageLabel(row.name) }} · {{ planReasonLabel(row) }}</li></ul>
          </details>
          <p v-if="plan.affectedOutsideScope.length" class="warning">完成後，這些未勾選的階段結果會過期：{{ plan.affectedOutsideScope.map(stageLabel).join("、") }}</p>
          <p v-if="plan.includesGemini" class="warning">此計畫會呼叫 Gemini API。</p>
        </template>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button type="button" class="button-primary pipeline-start" :disabled="locked || planning || !runRows.length" @click="begin">開始分析</button>
    </section>

    <section v-if="task && overall" class="pipeline-task" aria-labelledby="pipeline-task-title">
      <header class="pipeline-section-header">
        <h3 id="pipeline-task-title" role="status">{{ taskStatusLabel(task.status) }}</h3>
        <span class="secondary">{{ overall.done }}/{{ overall.total }} 階段<template v-if="elapsed"> · {{ elapsed }}</template></span>
      </header>
      <StageProgress :status="task.status" :progress="overall.fraction" label="整體進度" />
      <ul class="task-stage-list"><li v-for="(stage, name) in task.stageStates" :key="name">
        <span>{{ stageLabel(String(name)) }}</span><span>{{ taskStatusLabel(stage.status) }}<template v-if="stage.progress !== null"> · {{ Math.round(stage.progress * 100) }}%</template></span>
        <StageProgress :status="stage.status" :progress="stage.progress" :label="`${stageLabel(String(name))}進度`" />
      </li></ul>
      <p v-if="task.error" class="error" role="alert">{{ task.error }}</p>
      <p v-if="task.status === 'succeeded' && review && review.state !== 'failed'" class="secondary">{{ review.state === "publishing" ? "正在更新回看…" : "回看已更新。" }}</p>
      <div class="pipeline-task-actions">
        <button v-if="task.status === 'failed' || task.status === 'interrupted'" type="button" class="button-primary" :disabled="busy" @click="retry">重試</button>
        <button type="button" class="button-secondary" :aria-expanded="showLogs" @click="toggleLogs">{{ showLogs ? "收合詳細記錄" : "查看詳細記錄" }}</button>
      </div>
      <div v-if="showLogs" class="pipeline-logs"><pre>{{ lines.join('\n') }}</pre></div>
    </section>

    <p v-if="task && review?.state === 'failed'" class="error" role="alert">回看更新失敗：{{ review.error }}。原有回看資料仍可使用。 <button type="button" @click="syncReview(task, { verify: false, retry: true })">重新匯入</button></p>
    <p v-if="importError" class="error" role="alert">匯入失敗：{{ importError }}</p>
    <CourtCalibrationPanel :match-id="match.id" :available="match.completedStages.includes('court_detection')"
      :running="running" @updated="emit('updated')" @select="pick" />
    <button v-if="!hasReview && match.hasSegments && !running" type="button" class="button-secondary" :disabled="importing" @click="importExisting">{{ importing ? "匯入中…" : "匯入已有分析結果" }}</button>
  </section>
</template>
