<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import PipelinePanel from "../components/PipelinePanel.vue";
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
async function loadAnalysis() {
  const matches = await listPipelineMatches();
  match.value = matches.find(row => row.id === rawId.value) ?? null;
  error.value = match.value ? "" : "找不到此比賽的本機分析資料。";
}
/** The catalog only names the match and says whether it has a Review, so it is read when either can change. */
async function loadReview() {
  const review = (await loadCatalog()).find(row => row.id === `match:${rawId.value}`);
  hasReview.value = Boolean(review);
  name.value = review?.name ?? rawId.value;
}
async function load() {
  try { await Promise.all([loadAnalysis(), loadReview()]); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取分析資料"; }
  finally { loading.value = false; }
}
async function poll() {
  try { await loadAnalysis(); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取分析資料"; }
}
watch(rawId, () => { loading.value = true; void load(); });
onMounted(() => { void load(); timer = setInterval(() => { void poll(); }, 10000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <main class="section-page analysis-page">
    <header class="section-page-header"><div><span class="section-kicker">分析設定</span><h1>{{ name }}</h1></div></header>
    <p v-if="loading" role="status">載入中…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }} <button type="button" @click="load">重試</button></p>
    <PipelinePanel v-else-if="match" :match="match" :has-review="hasReview" @updated="load" />
  </main>
</template>
