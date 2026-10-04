<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { useRouter } from "vue-router";
import { importLocalMatch, listLocalMatches, loadCatalog, type ImportableMatch } from "../data/matchRepository";
import { listPipelineMatches, taskStatusLabel, type LocalAnalysisMatch } from "../data/pipelineTasks";
import PipelinePanel from "../components/PipelinePanel.vue";
import type { CatalogEntry } from "../domain/models";

const entries = ref<CatalogEntry[]>([]);
const loading = ref(true);
const loadError = ref("");
const heading = ref<HTMLHeadingElement | null>(null);
const router = useRouter();
const showImport = ref(false);
const candidates = ref<ImportableMatch[]>([]);
const importLoading = ref(false);
const importingId = ref<string | null>(null);
const importError = ref("");
const showAnalysis = ref(false);
const localMatches = ref<LocalAnalysisMatch[]>([]);
const localError = ref("");
const selectedLocalId = ref<string | null>(null);
const selectedLocal = computed(() => localMatches.value.find(row => row.id === selectedLocalId.value) ?? null);

async function loadAnalysis() {
  try {
    localMatches.value = await listPipelineMatches();
    localError.value = "";
    if (selectedLocalId.value && !localMatches.value.some(row => row.id === selectedLocalId.value)) {
      selectedLocalId.value = null;
    }
  } catch {
    localError.value = "無法連接本機分析服務。請先啟動 Python 服務。";
  }
}

async function toggleAnalysis() {
  showAnalysis.value = !showAnalysis.value;
  if (!showAnalysis.value) selectedLocalId.value = null;
  if (showAnalysis.value) await loadAnalysis();
}

async function analysisUpdated() {
  await Promise.all([loadAnalysis(), load()]);
}

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    entries.value = await loadCatalog();
  } catch {
    entries.value = [];
    loadError.value = "比賽目錄讀取失敗。請確認已匯入的前端資料後再試一次。";
  } finally {
    loading.value = false;
  }
}

async function toggleImport() {
  showImport.value = !showImport.value;
  if (!showImport.value) return;
  importLoading.value = true;
  importError.value = "";
  try {
    candidates.value = await listLocalMatches();
  } catch {
    candidates.value = [];
    importError.value = "無法讀取 matches/。請在本機預覽服務中使用匯入功能。";
  } finally {
    importLoading.value = false;
  }
}

async function importCandidate(id: string) {
  importingId.value = id;
  importError.value = "";
  try {
    const matchId = await importLocalMatch(id);
    await load();
    await router.push({ name: "match-review", params: { matchId } });
  } catch (error) {
    importError.value = error instanceof Error ? error.message : "匯入失敗";
  } finally {
    importingId.value = null;
  }
}

onMounted(async () => {
  await nextTick();
  heading.value?.focus();
  await load();
  await loadAnalysis();
});
</script>

<template>
  <div class="matches-page">
    <header class="matches-header">
      <div>
        <span class="section-kicker">Courtline</span>
        <h1 ref="heading" tabindex="-1">Matches</h1>
      </div>
      <div class="matches-header-actions">
        <span class="matches-count" aria-live="polite">{{ loading ? "讀取中" : `${entries.length} 場` }}</span>
        <button type="button" :aria-expanded="showAnalysis" aria-controls="match-analysis-panel" @click="toggleAnalysis">分析比賽</button>
        <button type="button" :aria-expanded="showImport" aria-controls="match-import-panel" @click="toggleImport">匯入比賽</button>
      </div>
    </header>

    <main class="matches-main">
      <div class="matches-intro">
        <h2>選擇比賽</h2>
        <p>開啟已匯入的比賽資料與回看工作區。</p>
      </div>

      <section v-if="showImport" id="match-import-panel" class="match-import-panel" aria-label="從 matches 目錄匯入比賽">
        <div class="match-import-heading"><h2>從 matches/ 匯入</h2><p>選擇已完成分析的比賽；匯入只建立前端資料，不會重新分析影片。</p></div>
        <p v-if="importLoading" role="status">正在掃描比賽資料夾…</p>
        <p v-if="importError" class="error" role="alert">{{ importError }}</p>
        <p v-if="!importLoading && !importError && !candidates.length" class="empty-state">matches/ 內沒有比賽資料夾。</p>
        <ul v-if="!importLoading && candidates.length" class="match-import-list">
          <li v-for="candidate in candidates" :key="candidate.id" class="match-import-row">
            <span><strong>{{ candidate.id }}</strong><small>{{ candidate.available ? "可匯入" : candidate.reason }}</small></span>
            <button type="button" :disabled="!candidate.available || importingId !== null" @click="importCandidate(candidate.id)">{{ importingId === candidate.id ? "匯入中…" : "匯入" }}</button>
          </li>
        </ul>
      </section>

      <section v-if="showAnalysis" id="match-analysis-panel" class="match-analysis-panel" aria-label="本機比賽分析">
        <h2>本機比賽分析</h2>
        <p v-if="localError" class="error" role="alert">{{ localError }} <button type="button" @click="loadAnalysis">重試</button></p>
        <p v-else-if="!localMatches.length" class="empty-state">matches/ 內沒有附影片的比賽。</p>
        <div v-else class="match-analysis-layout">
          <nav v-if="!selectedLocal" aria-label="本機比賽"><button v-for="row in localMatches" :key="row.id" type="button"
            @click="selectedLocalId = row.id">
            <strong>{{ row.id }}</strong><span>{{ row.latestTask ? taskStatusLabel(row.latestTask.status) : ({ completed: "已完成", partial: "部分完成", unanalysed: "尚未分析" }[row.analysisStatus]) }}</span>
          </button></nav>
          <div v-else class="match-analysis-details">
            <button type="button" class="match-analysis-back" @click="selectedLocalId = null">← 更換比賽</button>
            <PipelinePanel :match="selectedLocal"
              :has-review="entries.some(entry => entry.id === `match:${selectedLocalId}`)" @updated="analysisUpdated" />
          </div>
        </div>
      </section>

      <p v-if="loading" class="matches-status" role="status">載入比賽目錄…</p>
      <div v-else-if="loadError" class="error" role="alert">
        {{ loadError }}
        <button type="button" @click="load">重試</button>
      </div>
      <p v-else-if="!entries.length" class="empty-state">
        尚無可用的比賽資料。
      </p>
      <nav v-else class="matches-list" aria-label="比賽目錄">
        <RouterLink
          v-for="entry in entries"
          :key="entry.id"
          class="match-row"
          :to="{
            name: 'match-review',
            params: { matchId: entry.id },
          }"
        >
          <span class="match-row-main">
            <strong>{{ entry.name }}</strong>
            <code>{{ entry.id }}</code>
          </span>
          <span class="match-row-kind">
            {{ entry.kind === "match" ? "已匯入比賽" : "示範資料" }}
          </span>
          <span class="match-row-action" aria-hidden="true">開啟 →</span>
        </RouterLink>
      </nav>
    </main>
  </div>
</template>
