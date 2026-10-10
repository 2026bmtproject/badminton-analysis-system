<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useRoute } from "vue-router";
import AppIcon from "./components/ui/AppIcon.vue";
import { useTaskFeed } from "./composables/useTaskFeed";
import { taskProgress } from "./data/pipelineTasks";

const connected = ref<boolean | null>(null);
const route = useRoute();
const { active, unseenFailure } = useTaskFeed();
const activePercent = computed(() => active.value ? Math.round(taskProgress(active.value).fraction * 100) : 0);
const tasksTitle = computed(() => {
  if (active.value) return `任務：${active.value.matchId} 分析中 ${activePercent.value}%`;
  return unseenFailure.value ? "任務：上一個分析沒有完成" : "任務";
});
let timer: ReturnType<typeof setInterval> | undefined;
async function checkService() {
  try {
    if (window.badmintonDesktop) {
      connected.value = Boolean((await window.badmintonDesktop.status()).service?.connected);
    } else {
      const response = await fetch("/api/pipeline/stages", { cache: "no-store" });
      connected.value = response.ok;
    }
  } catch { connected.value = false; }
}
onMounted(() => { void checkService(); timer = setInterval(() => { void checkService(); }, 8000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <div class="desktop-shell">
    <nav class="desktop-nav" aria-label="主要導覽">
      <div class="desktop-brand" title="羽球分析系統" aria-label="羽球分析系統">B</div>
      <RouterLink :to="{ name: 'matches' }" title="比賽庫" aria-label="比賽庫" :class="{ 'is-active': route.path.startsWith('/matches') || route.path.startsWith('/analysis') }"><AppIcon name="library" :size="21" /><span>比賽庫</span></RouterLink>
      <RouterLink class="desktop-nav-tasks" :to="{ name: 'tasks' }" :title="tasksTitle" :aria-label="tasksTitle" active-class="is-active"><AppIcon name="tasks" :size="21" /><span>任務</span>
        <svg v-if="active" class="nav-task-ring" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" /><circle class="nav-task-ring-fill" cx="8" cy="8" r="6" pathLength="100" :stroke-dasharray="`${activePercent} 100`" /></svg>
        <span v-else-if="unseenFailure" class="nav-task-alert" aria-hidden="true" /></RouterLink>
      <RouterLink :to="{ name: 'settings' }" title="設定" aria-label="設定" active-class="is-active"><AppIcon name="settings" :size="21" /><span>設定</span></RouterLink>
      <RouterLink class="desktop-nav-status" :to="{ name: 'settings' }" :title="connected ? '分析服務已連線' : '分析服務未連線'" :aria-label="connected ? '分析服務已連線，查看設定' : '分析服務未連線，查看設定'"><span class="connection-dot" :class="{ online: connected }" /></RouterLink>
    </nav>
    <div class="desktop-content"><RouterView /></div>
  </div>
</template>
