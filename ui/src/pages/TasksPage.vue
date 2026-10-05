<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { getPipelineLogs, listPipelineTasks, taskStatusLabel, type PipelineTask } from "../data/pipelineTasks";
import { stageLabel } from "../data/stageLabels";

const tasks = ref<PipelineTask[]>([]);
const error = ref("");
const loading = ref(true);
const now = ref(Date.now());
const expanded = ref<string | null>(null);
const logs = ref<Record<string, string[]>>({});
const offsets = ref<Record<string, number>>({});
let timer: ReturnType<typeof setInterval> | undefined;
const ordered = computed(() => [...tasks.value].sort((a, b) => b.createdAt.localeCompare(a.createdAt)));
async function load() {
  try { tasks.value = await listPipelineTasks(); error.value = ""; }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取任務"; }
  finally { loading.value = false; now.value = Date.now(); }
}
function elapsed(task: PipelineTask) {
  if (!task.startedAt) return "尚未開始";
  const end = task.finishedAt ? Date.parse(task.finishedAt) : now.value;
  return `${Math.max(0, Math.floor((end - Date.parse(task.startedAt)) / 1000))} 秒`;
}
async function moreLogs(id: string) {
  try {
    const page = await getPipelineLogs(id, offsets.value[id] ?? 0);
    logs.value[id] = [...(logs.value[id] ?? []), ...page.lines];
    offsets.value[id] = page.nextOffset;
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取記錄"; }
}
function toggleLogs(id: string) {
  expanded.value = expanded.value === id ? null : id;
  if (expanded.value === id && !(id in logs.value)) void moreLogs(id);
}
onMounted(() => { void load(); timer = setInterval(() => { void load(); }, 3000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <main class="section-page tasks-page">
    <header class="section-page-header"><div><span class="section-kicker">執行紀錄</span><h1>任務</h1><p>分析會在關閉頁面後繼續；重新開啟即可查看狀態。</p></div><button class="button-secondary" type="button" @click="load">重新整理</button></header>
    <p v-if="loading" role="status">載入任務中…</p>
    <p v-if="error" class="error" role="alert">{{ error }} <RouterLink :to="{ name: 'settings', query: { advanced: '1' } }">檢查服務設定</RouterLink></p>
    <p v-if="!loading && !error && !ordered.length" class="console-panel empty-state">尚無分析任務。請從比賽庫選擇一場比賽開始。</p>
    <div class="task-list"><article v-for="task in ordered" :key="task.id" class="console-panel task-card">
      <header><div><h2>{{ task.matchId }}</h2><span class="secondary">{{ new Date(task.createdAt).toLocaleString('zh-TW') }} · {{ elapsed(task) }}</span></div><span class="status-chip" :data-status="task.status">{{ taskStatusLabel(task.status) }}</span></header>
      <p v-if="task.currentStage">目前階段：{{ stageLabel(task.currentStage) }}</p>
      <p v-if="task.error" class="error" role="alert">{{ task.error }}<span v-if="task.exitCode !== null">（exit {{ task.exitCode }}）</span></p>
      <ul class="task-stage-list"><li v-for="(stage, name) in task.stageStates" :key="name"><span>{{ stageLabel(String(name)) }}</span><span>{{ taskStatusLabel(stage.status) }}<template v-if="stage.progress !== null"> · {{ Math.round(stage.progress * 100) }}%</template></span></li></ul>
      <footer><RouterLink class="button-secondary" :to="{ name: 'match-analysis', params: { matchId: task.matchId } }">查看分析設定</RouterLink><button type="button" class="button-secondary" :aria-expanded="expanded === task.id" @click="toggleLogs(task.id)">{{ expanded === task.id ? '收合記錄' : '詳細記錄' }}</button></footer>
      <div v-if="expanded === task.id" class="pipeline-logs"><pre>{{ (logs[task.id] ?? []).join('\n') }}</pre><button type="button" class="button-secondary" @click="moreLogs(task.id)">載入更多</button></div>
    </article></div>
  </main>
</template>
