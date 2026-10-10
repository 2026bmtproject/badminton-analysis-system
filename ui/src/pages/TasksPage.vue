<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import StageProgress from "../components/ui/StageProgress.vue";
import { useTaskFeed } from "../composables/useTaskFeed";
import { loadCatalog } from "../data/matchRepository";
import {
  LOG_PAGE_SIZE, formatDuration, formatLogLine, formatTaskTime, getPipelineLogs, ranStages, ranStagesSummary, retryTask, stageElapsed,
  stageSeconds, taskElapsed, taskProgress, taskStatusLabel, type PipelineTask, type TaskStage,
} from "../data/pipelineTasks";
import { stageLabel } from "../data/stageLabels";

const { tasks, error, loaded, now, active, refresh, acknowledgeFailure, reviewVersion } = useTaskFeed();
const reviewIds = ref(new Set<string>());
const expanded = ref<string | null>(null);
const logsOpen = ref<string | null>(null);
const logs = ref<Record<string, string[]>>({});
const offsets: Record<string, number> = {};
const retrying = ref<string | null>(null);
const actionError = ref("");

/** The running task, or the newest one when it failed: both need the user's attention more than history does. */
const featured = computed(() => {
  if (active.value) return active.value;
  const newest = tasks.value[0];
  return newest && (newest.status === "failed" || newest.status === "interrupted") ? newest : null;
});
const history = computed(() => tasks.value.filter(task => task.id !== featured.value?.id));
const overall = computed(() => featured.value ? taskProgress(featured.value) : null);
const skippedCount = computed(() => featured.value
  ? Object.values(featured.value.stageStates).filter(stage => stage.status === "skipped").length : 0);

async function loadReviews() {
  try { reviewIds.value = new Set((await loadCatalog()).map(entry => entry.id)); }
  catch { reviewIds.value = new Set(); }
}
const hasReview = (task: PipelineTask) => reviewIds.value.has(`match:${task.matchId}`);
const failed = (task: PipelineTask) => task.status === "failed" || task.status === "interrupted";

function stageMeta(stage: TaskStage) {
  if (stage.status === "running") return stage.progress === null ? "執行中" : `${Math.round(stage.progress * 100)}%`;
  if (stage.status === "skipped") return "沿用既有結果";
  if (stage.status === "queued") return "等待中";
  const elapsed = stageElapsed(stage, now.value);
  if (stage.status === "succeeded") return elapsed ?? "完成";
  return elapsed ? `${taskStatusLabel(stage.status)} · ${elapsed}` : taskStatusLabel(stage.status);
}

/** Reads every log line not yet shown; finished tasks are read once, the running one on every refresh. */
async function fetchLogs(id: string) {
  try {
    for (;;) {
      const page = await getPipelineLogs(id, offsets[id] ?? 0);
      logs.value[id] = [...(logs.value[id] ?? []), ...page.lines];
      offsets[id] = page.nextOffset;
      if (page.lines.length < LOG_PAGE_SIZE) break;
    }
  } catch (cause) { actionError.value = cause instanceof Error ? cause.message : "無法讀取記錄"; }
}
function toggleLogs(id: string) {
  logsOpen.value = logsOpen.value === id ? null : id;
  if (logsOpen.value === id && !(id in logs.value)) void fetchLogs(id);
}
/** Where a finished task spent its time: each ran stage with a bar scaled to the slowest one. */
function timing(task: PipelineTask) {
  const rows = ranStages(task).map(([name, stage]) => ({ name, stage, seconds: stageSeconds(stage, now.value) }));
  const longest = Math.max(1, ...rows.map(row => row.seconds ?? 0));
  return rows.map(row => ({ ...row, share: (row.seconds ?? 0) / longest }));
}
function timingLabel(stage: TaskStage, seconds: number | null) {
  if (seconds === null) return "未執行";
  const time = seconds < 1 ? "<0:01" : formatDuration(seconds);
  return stage.status === "succeeded" ? time : `${taskStatusLabel(stage.status)} ${time}`;
}
function reused(task: PipelineTask) {
  return Object.entries(task.stageStates).filter(([, stage]) => stage.status === "skipped").map(([name]) => stageLabel(name));
}
function toggleRow(id: string) {
  expanded.value = expanded.value === id ? null : id;
}

async function retry(task: PipelineTask) {
  retrying.value = task.id; actionError.value = "";
  try { await retryTask(task); await refresh(); }
  catch (cause) { actionError.value = cause instanceof Error ? cause.message : "無法重新開始分析"; }
  finally { retrying.value = null; }
}

watch(now, () => {
  const id = logsOpen.value;
  if (id && active.value?.id === id) void fetchLogs(id);
});
// A succeeded task publishes a new Review in the shared feed, which decides whether its row links to it.
watch(reviewVersion, () => { void loadReviews(); });
watch(featured, task => { if (task && failed(task)) acknowledgeFailure(); }, { immediate: true });
onMounted(() => { void loadReviews(); });
</script>

<template>
  <main class="section-page tasks-page">
    <header class="section-page-header"><h1>任務</h1></header>
    <p v-if="error" class="error" role="alert">{{ error }} <RouterLink :to="{ name: 'settings', hash: '#analysis-environment' }">檢查服務設定</RouterLink></p>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
    <p v-if="!loaded" role="status">載入任務中…</p>

    <template v-else>
      <section v-if="featured && overall" class="console-panel task-now" :data-status="featured.status" aria-labelledby="task-now-title">
        <header class="task-now-header">
          <div>
            <span class="task-now-kicker" role="status">{{ taskStatusLabel(featured.status) }}</span>
            <h2 id="task-now-title">{{ featured.matchId }}</h2>
          </div>
          <span class="task-now-clock">{{ taskElapsed(featured, now) ?? "尚未開始" }}</span>
        </header>
        <div class="task-now-progress">
          <StageProgress :status="featured.status" :progress="overall.fraction" label="整體進度" />
          <span>{{ overall.done }} / {{ overall.total }} 階段 · {{ Math.round(overall.fraction * 100) }}%</span>
        </div>
        <ol class="task-steps">
          <li v-for="[name, stage] in ranStages(featured)" :key="name" :data-status="stage.status">
            <span class="task-step-mark" aria-hidden="true" />
            <span class="task-step-name">{{ stageLabel(name) }}</span>
            <span class="task-step-meta">{{ stageMeta(stage) }}</span>
            <StageProgress v-if="stage.status === 'running'" :status="stage.status" :progress="stage.progress" :label="`${stageLabel(name)}進度`" />
          </li>
        </ol>
        <p v-if="skippedCount" class="secondary task-now-skipped">另 {{ skippedCount }} 項沿用既有結果</p>
        <p v-if="featured.error" class="error" role="alert">{{ featured.error }}</p>
        <footer class="task-actions">
          <button v-if="failed(featured)" type="button" class="button-primary" :disabled="retrying !== null" @click="retry(featured)">{{ retrying === featured.id ? "重新開始中…" : "重試" }}</button>
          <RouterLink class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: featured.matchId } }">分析設定</RouterLink>
          <button type="button" class="button-secondary" :aria-expanded="logsOpen === featured.id" @click="toggleLogs(featured.id)">{{ logsOpen === featured.id ? "收合記錄" : "記錄" }}</button>
        </footer>
        <pre v-if="logsOpen === featured.id" class="task-log">{{ (logs[featured.id] ?? []).map(formatLogLine).join('\n') || "尚無記錄" }}</pre>
      </section>
      <p v-else class="task-idle">目前沒有執行中的任務 · <RouterLink :to="{ name: 'matches' }">從比賽庫開始分析</RouterLink></p>

      <section class="task-history" aria-labelledby="task-history-title">
        <h2 id="task-history-title">歷史</h2>
        <p v-if="!history.length" class="secondary">尚無過去的分析任務。</p>
        <ul v-else class="task-rows">
          <li v-for="task in history" :key="task.id" class="task-row" :data-status="task.status">
            <button type="button" class="task-row-summary" :aria-expanded="expanded === task.id" @click="toggleRow(task.id)">
              <span class="task-row-dot" :title="taskStatusLabel(task.status)"><span class="sr-only">{{ taskStatusLabel(task.status) }}</span></span>
              <span class="task-row-name">{{ task.matchId }}</span>
              <span class="task-row-stages">{{ failed(task) && task.error && expanded !== task.id ? task.error : ranStagesSummary(task, stageLabel) }}</span>
              <span class="task-row-time">{{ formatTaskTime(task.createdAt, now) }}</span>
              <span class="task-row-duration">{{ taskElapsed(task, now) ?? "—" }}</span>
            </button>
            <span class="task-row-action">
              <button v-if="failed(task)" type="button" class="button-secondary" :disabled="retrying !== null || active !== null" @click="retry(task)">{{ retrying === task.id ? "重新開始中…" : "重試" }}</button>
              <RouterLink v-else-if="task.status === 'succeeded' && hasReview(task)" class="button-secondary" :to="{ name: 'match-review', params: { matchId: `match:${task.matchId}` } }">回看</RouterLink>
              <RouterLink v-else class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: task.matchId } }">分析設定</RouterLink>
            </span>
            <div v-if="expanded === task.id" class="task-row-detail">
              <ol class="task-timing" aria-label="各階段耗時">
                <li v-for="row in timing(task)" :key="row.name" :data-status="row.stage.status">
                  <span class="task-timing-name">{{ stageLabel(row.name) }}</span>
                  <span class="task-timing-bar" aria-hidden="true"><span v-if="row.seconds !== null" :style="{ width: `${row.share * 100}%` }" /></span>
                  <span class="task-timing-time">{{ timingLabel(row.stage, row.seconds) }}</span>
                </li>
              </ol>
              <p v-if="task.error" class="task-detail-error">{{ task.error }}</p>
              <p v-if="reused(task).length" class="task-detail-reused">沿用既有結果：{{ reused(task).join("、") }}</p>
              <div class="task-detail-links">
                <RouterLink :to="{ name: 'match-analysis', params: { matchId: task.matchId } }">分析設定</RouterLink>
                <button type="button" :aria-expanded="logsOpen === task.id" @click="toggleLogs(task.id)">{{ logsOpen === task.id ? "收合記錄" : "記錄" }}</button>
              </div>
              <pre v-if="logsOpen === task.id" class="task-log">{{ (logs[task.id] ?? []).map(formatLogLine).join('\n') || "尚無記錄" }}</pre>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>
