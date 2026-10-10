<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import AppIcon from "../components/ui/AppIcon.vue";
import StageProgress from "../components/ui/StageProgress.vue";
import { mergeLibrary, type LibraryMatch } from "../data/library";
import { importLocalMatch, listLocalMatches, loadCatalog, loadMatch, type ImportableMatch } from "../data/matchRepository";
import { listPipelineMatches, taskStatusLabel, type LocalAnalysisMatch } from "../data/pipelineTasks";
import { libraryIssueLines, type ReviewSnapshot } from "../data/reviewIssues";
import { stageLabel } from "../data/stageLabels";
import type { CatalogEntry } from "../domain/models";

const router = useRouter();
const catalog = ref<CatalogEntry[]>([]);
const local = ref<LocalAnalysisMatch[]>([]);
const candidates = ref<ImportableMatch[]>([]);
const loading = ref(true);
const serviceError = ref("");
const catalogError = ref("");
const candidateError = ref("");
const actionError = ref("");
/** Review snapshots by catalog ID; null when the Review file could not be read. */
const reviews = ref<Record<string, ReviewSnapshot | null>>({});
const publishing = ref<string | null>(null);
const query = ref("");
const filter = ref("all");
const scanning = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
const rows = computed(() => mergeLibrary(catalog.value, local.value, candidates.value));
const visible = computed(() => rows.value.filter(row => {
  if (query.value && !`${row.name} ${row.id}`.toLocaleLowerCase().includes(query.value.toLocaleLowerCase())) return false;
  if (filter.value === "review") return Boolean(row.review);
  if (filter.value === "active") return ["running", "queued"].includes(row.local?.latestTask?.status ?? "");
  if (filter.value === "attention") return ["failed", "interrupted"].includes(row.local?.latestTask?.status ?? "") || needsUpdate(row) || Boolean(row.candidate && !row.candidate.available);
  return true;
}));
function rawId(row: LibraryMatch) { return row.id.slice("match:".length); }
function needsUpdate(row: LibraryMatch) {
  return Boolean(row.local && Object.keys(row.local.staleStages).length);
}
function issues(row: LibraryMatch) {
  if (row.id in reviews.value && reviews.value[row.id] === null) return ["回看資料無法讀取，請重新整理回看資料"];
  return libraryIssueLines(row.local, reviews.value[row.id] ?? null);
}
function analysisStatus(row: LibraryMatch) {
  const task = row.local?.latestTask;
  if (task && task.status !== "succeeded") return task.status;
  return needsUpdate(row) ? "stale" : task?.status ?? row.local?.analysisStatus;
}
function analysisLabel(row: LibraryMatch) {
  if (row.kind === "fixture") return "示範資料";
  if (analysisStatus(row) === "stale") return "需要更新";
  if (row.local?.latestTask) return taskStatusLabel(row.local.latestTask.status);
  if (!row.local) return serviceError.value ? "分析服務離線" : "未取得分析狀態";
  return ({ completed: "分析完成", partial: "部分完成", unanalysed: "尚未分析" } as const)[row.local.analysisStatus];
}
function activeTask(row: LibraryMatch) {
  const task = row.local?.latestTask;
  return task && ["queued", "running"].includes(task.status) ? task : null;
}
function currentStageState(row: LibraryMatch) {
  const task = activeTask(row);
  return task?.currentStage ? task.stageStates[task.currentStage] ?? null : null;
}
function stageProgress(row: LibraryMatch) {
  const task = activeTask(row);
  if (!task) return "";
  const stage = currentStageState(row);
  return `${task.currentStage ? stageLabel(task.currentStage) : "等待執行"}${stage?.progress == null ? "" : ` · ${Math.round(stage.progress * 100)}%`}`;
}
async function refresh() {
  const [c, l, m] = await Promise.allSettled([loadCatalog(), listPipelineMatches(), listLocalMatches()]);
  if (c.status === "fulfilled") {
    catalog.value = c.value; catalogError.value = "";
    const reviewEntries = c.value.filter(row => row.kind === "match");
    void Promise.all(reviewEntries.map(async entry => {
      try {
        const model = await loadMatch(entry);
        reviews.value[entry.id] = { states: model.states, importedAt: model.source?.importedAt };
      } catch { reviews.value[entry.id] = null; }
    }));
  }
  else catalogError.value = "回看目錄讀取失敗。";
  if (l.status === "fulfilled") { local.value = l.value; serviceError.value = ""; }
  else serviceError.value = "分析服務未連線；現有回看仍可開啟。";
  if (m.status === "fulfilled") { candidates.value = m.value; candidateError.value = ""; }
  else candidateError.value = "無法掃描 matches 資料夾。";
  loading.value = false;
}
async function scan() { scanning.value = true; await refresh(); scanning.value = false; }
async function publish(row: LibraryMatch) {
  const id = rawId(row);
  actionError.value = ""; publishing.value = id;
  try { const reviewId = await importLocalMatch(id); await refresh(); await router.push({ name: "match-review", params: { matchId: reviewId } }); }
  catch (cause) { actionError.value = cause instanceof Error ? cause.message : "匯入分析結果失敗"; }
  finally { publishing.value = null; }
}
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh(); }, 10000); window.addEventListener("focus", refresh); });
onUnmounted(() => { if (timer) clearInterval(timer); window.removeEventListener("focus", refresh); });
</script>

<template>
  <main class="section-page library-page">
    <header class="section-page-header"><div><h1>比賽庫</h1></div><button type="button" class="button-primary" :disabled="scanning" @click="scan"><AppIcon name="refresh" />{{ scanning ? '掃描中…' : '掃描 matches 資料夾' }}</button></header>
    <div class="library-toolbar"><label class="library-search"><AppIcon name="search" /><span class="sr-only">搜尋比賽</span><input v-model="query" type="search" placeholder="搜尋比賽名稱或 ID" /></label><label class="library-filter"><span>狀態</span><select v-model="filter"><option value="all">全部</option><option value="review">可回看</option><option value="active">分析中</option><option value="attention">需要處理</option></select></label></div>
    <p v-if="serviceError" class="inline-notice" role="status">{{ serviceError }} <RouterLink :to="{ name: 'settings', hash: '#analysis-environment' }">前往設定</RouterLink></p>
    <p v-if="catalogError || candidateError" class="inline-notice" role="status">{{ catalogError }} {{ candidateError }}</p>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
    <p v-if="loading" role="status">正在讀取比賽…</p>
    <p v-else-if="!visible.length" class="console-panel empty-state">{{ rows.length ? '找不到符合條件的比賽。' : '尚無比賽。請在設定中選擇 matches 資料夾，或將現有影片資料放入該目錄後掃描。' }}</p>
    <div v-else class="library-list"><article v-for="row in visible" :key="row.id" class="console-panel library-card">
      <div class="library-thumbnail" role="img" aria-label="沒有可用的比賽縮圖"><AppIcon name="video" :size="27" /></div>
      <div class="library-card-content"><div class="library-card-title"><h2>{{ row.name }}</h2><span v-if="row.kind === 'fixture'" class="status-chip">示範</span></div><div class="library-card-meta"><span class="status-chip" :data-status="analysisStatus(row)">{{ analysisLabel(row) }}</span><span class="status-chip" :data-status="row.review ? 'succeeded' : 'missing'">{{ row.review ? '可回看' : '尚無回看' }}</span><span v-if="row.candidate && !row.candidate.available" class="secondary">{{ row.candidate.reason }}</span></div><p v-if="stageProgress(row)" class="library-stage" role="status">{{ stageProgress(row) }}</p><StageProgress v-if="currentStageState(row)" :status="currentStageState(row)!.status" :progress="currentStageState(row)!.progress" :label="`${row.name} 目前階段進度`" /><p v-for="issue in issues(row)" :key="issue" class="library-issue">{{ issue }}</p></div>
      <div class="library-card-actions"><RouterLink v-if="row.review" class="button-primary" :to="{ name: 'match-review', params: { matchId: row.id } }">開啟回看</RouterLink><RouterLink v-if="row.local && ['running', 'queued'].includes(row.local.latestTask?.status ?? '')" class="button-secondary" :to="{ name: 'tasks' }">查看進度</RouterLink><RouterLink v-else-if="row.local && needsUpdate(row)" class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: row.local.id }, query: { select: 'stale' } }">更新分析</RouterLink><RouterLink v-else-if="row.local" class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: row.local.id } }">{{ row.local.analysisStatus === 'unanalysed' ? '開始分析' : '分析設定' }}</RouterLink><button v-if="row.kind === 'match' && row.candidate?.available" type="button" class="button-secondary" :title="row.review ? '以目前的分析結果更新回看' : '把已有的分析結果匯入成回看'" :disabled="publishing !== null" @click="publish(row)">{{ publishing === rawId(row) ? '匯入中…' : '匯入結果' }}</button></div>
    </article></div>
  </main>
</template>
