<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import PipelinePanel from "../components/PipelinePanel.vue";
import AppIcon from "../components/ui/AppIcon.vue";
import { loadCatalog } from "../data/matchRepository";
import { listPipelineMatches, type LocalAnalysisMatch } from "../data/pipelineTasks";

const route = useRoute();
const match = ref<LocalAnalysisMatch | null>(null);
const name = ref("");
const hasReview = ref(false);
const error = ref("");
const loading = ref(true);
const rawId = computed(() => String(route.params.matchId ?? ""));
let timer: ReturnType<typeof setInterval> | undefined;
async function load() {
  try {
    const [matches, catalog] = await Promise.all([listPipelineMatches(), loadCatalog()]);
    match.value = matches.find(row => row.id === rawId.value) ?? null;
    const review = catalog.find(row => row.id === `match:${rawId.value}`);
    hasReview.value = Boolean(review);
    name.value = review?.name ?? rawId.value;
    error.value = match.value ? "" : "找不到此比賽的本機分析資料。";
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取分析資料"; }
  finally { loading.value = false; }
}
watch(rawId, () => { loading.value = true; void load(); });
onMounted(() => { void load(); timer = setInterval(() => { void load(); }, 10000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <main class="section-page analysis-page">
    <header class="section-page-header"><div><RouterLink class="back-link" :to="{ name: 'matches' }"><AppIcon name="arrow-left" /> 比賽庫</RouterLink><span class="section-kicker">分析設定</span><h1>{{ name }}</h1><p>選擇分析項目、檢查計畫，再開始執行。</p></div><RouterLink v-if="hasReview" class="button-secondary" :to="{ name: 'match-review', params: { matchId: `match:${rawId}` } }">開啟回看</RouterLink></header>
    <p v-if="loading" role="status">載入中…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }} <button type="button" @click="load">重試</button></p>
    <PipelinePanel v-else-if="match" :match="match" :has-review="hasReview" :open-court="route.query.court === '1'" @updated="load" />
  </main>
</template>
