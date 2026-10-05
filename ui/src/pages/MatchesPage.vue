<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import AppIcon from "../components/ui/AppIcon.vue";
import { mergeLibrary, type LibraryMatch } from "../data/library";
import { importLocalMatch, listLocalMatches, loadCatalog, loadMatch, type ImportableMatch } from "../data/matchRepository";
import { listPipelineMatches, taskStatusLabel, type LocalAnalysisMatch } from "../data/pipelineTasks";
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
const reviewIssues = ref<Record<string, string[]>>({});
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
  if (filter.value === "attention") return ["failed", "interrupted"].includes(row.local?.latestTask?.status ?? "") || Boolean(row.candidate && !row.candidate.available);
  return true;
}));
function rawId(row: LibraryMatch) { return row.id.slice("match:".length); }
function analysisLabel(row: LibraryMatch) {
  if (row.kind === "fixture") return "示範資料";
  if (row.local?.latestTask) return taskStatusLabel(row.local.latestTask.status);
  if (!row.local) return serviceError.value ? "分析服務離線" : "未取得分析狀態";
  return ({ completed: "分析完成", partial: "部分完成", unanalysed: "尚未分析" } as const)[row.local.analysisStatus];
}
function stageProgress(row: LibraryMatch) {
  const task = row.local?.latestTask;
  if (!task || !["queued", "running"].includes(task.status)) return "";
  const stage = task.currentStage ? task.stageStates[task.currentStage] : null;
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
        reviewIssues.value[entry.id] = Object.entries(model.states)
          .filter(([, state]) => ["error", "stale", "unknown"].includes(state.status))
          .map(([name, state]) => `${stageLabel(name)}：${({ error: "資料錯誤", stale: "輸入過期", unknown: "來源狀態未知" } as Record<string, string>)[state.status]}${state.message ? `（${state.message}）` : ""}`);
      } catch { reviewIssues.value[entry.id] = ["回看資料無法讀取，請重新整理回看資料"]; }
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
  catch (cause) { actionError.value = cause instanceof Error ? cause.message : "回看更新失敗"; }
  finally { publishing.value = null; }
}
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh(); }, 10000); window.addEventListener("focus", refresh); });
onUnmounted(() => { if (timer) clearInterval(timer); window.removeEventListener("focus", refresh); });
</script>

<template>
  <main class="section-page library-page">
    <header class="section-page-header"><div><span class="section-kicker">工作空間</span><h1>比賽庫 <span class="heading-count">{{ rows.length }}</span></h1><p>本機比賽與可用回看集中在這裡。</p></div><button type="button" class="button-primary" :disabled="scanning" @click="scan"><AppIcon name="refresh" />{{ scanning ? '掃描中…' : '掃描 matches 資料夾' }}</button></header>
    <div class="library-toolbar"><label class="library-search"><AppIcon name="search" /><span class="sr-only">搜尋比賽</span><input v-model="query" type="search" placeholder="搜尋比賽名稱或 ID" /></label><label class="library-filter"><span>狀態</span><select v-model="filter"><option value="all">全部</option><option value="review">可回看</option><option value="active">分析中</option><option value="attention">需要處理</option></select></label><span class="library-result-count" aria-live="polite">顯示 {{ visible.length }} 場</span></div>
    <p v-if="serviceError" class="inline-notice" role="status">{{ serviceError }} <RouterLink :to="{ name: 'settings', query: { advanced: '1' } }">前往設定</RouterLink></p>
    <p v-if="catalogError || candidateError" class="inline-notice" role="status">{{ catalogError }} {{ candidateError }}</p>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
    <p v-if="loading" role="status">正在讀取比賽…</p>
    <p v-else-if="!visible.length" class="console-panel empty-state">{{ rows.length ? '找不到符合條件的比賽。' : '尚無比賽。請在設定中選擇 matches 資料夾，或將現有影片資料放入該目錄後掃描。' }}</p>
    <div v-else class="library-list"><article v-for="row in visible" :key="row.id" class="console-panel library-card">
      <div class="library-thumbnail" role="img" aria-label="沒有可用的比賽縮圖"><AppIcon name="video" :size="27" /></div>
      <div class="library-card-content"><div class="library-card-title"><h2>{{ row.name }}</h2><span v-if="row.kind === 'fixture'" class="status-chip">示範</span></div><div class="library-card-meta"><span class="status-chip" :data-status="row.local?.latestTask?.status ?? row.local?.analysisStatus">{{ analysisLabel(row) }}</span><span class="status-chip" :data-status="row.review ? 'succeeded' : 'missing'">{{ row.review ? '可回看' : '尚無回看' }}</span><span v-if="row.candidate && !row.candidate.available" class="secondary">{{ row.candidate.reason }}</span></div><p v-if="stageProgress(row)" class="library-stage" role="status">{{ stageProgress(row) }}</p><p v-for="issue in reviewIssues[row.id] ?? []" :key="issue" class="library-issue">{{ issue }}</p></div>
      <div class="library-card-actions"><RouterLink v-if="row.review" class="button-primary" :to="{ name: 'match-review', params: { matchId: row.id } }">開啟回看</RouterLink><RouterLink v-if="row.local && ['running', 'queued'].includes(row.local.latestTask?.status ?? '')" class="button-secondary" :to="{ name: 'tasks' }">查看進度</RouterLink><RouterLink v-else-if="row.local" class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: row.local.id } }">{{ row.local.analysisStatus === 'unanalysed' ? '開始分析' : '繼續分析' }}</RouterLink><details v-if="row.kind === 'match'" class="library-more"><summary title="更多操作" :aria-label="`${row.name} 更多操作`"><AppIcon name="more" /></summary><div class="library-more-menu"><RouterLink v-if="row.local" :to="{ name: 'match-analysis', params: { matchId: row.local.id } }">分析設定</RouterLink><RouterLink v-if="row.local" :to="{ name: 'match-analysis', params: { matchId: row.local.id }, query: { court: '1' } }">場地校正</RouterLink><button v-if="row.candidate?.available" type="button" :disabled="publishing !== null" @click="publish(row)">{{ publishing === rawId(row) ? '更新中…' : '匯出／重新整理回看資料' }}</button></div></details></div>
    </article></div>
  </main>
</template>
