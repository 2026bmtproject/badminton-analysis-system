<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useRoute } from "vue-router";
import AppIcon from "./components/ui/AppIcon.vue";
import { useTaskFeed } from "./composables/useTaskFeed";
import { taskProgress } from "./data/pipelineTasks";

const route = useRoute();
// Connection only shows when it is lost: a permanent green dot said nothing, and pages that need the service
// already explain when it is missing. This covers the ones that do not, such as Review.
const { active, unseenFailure, offline } = useTaskFeed();
const activePercent = computed(() => active.value ? Math.round(taskProgress(active.value).fraction * 100) : 0);
const tasksTitle = computed(() => {
  if (active.value) return `任務：${active.value.matchId} 分析中 ${activePercent.value}%`;
  return unseenFailure.value ? "任務：上一個分析沒有完成" : "任務";
});
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
      <RouterLink v-if="offline" class="desktop-nav-offline" active-class="" exact-active-class="" :to="{ name: 'settings', query: { advanced: '1' } }" title="分析服務未連線，查看設定" aria-label="分析服務未連線，查看設定"><AppIcon name="alert" :size="21" /><span>離線</span></RouterLink>
    </nav>
    <div class="desktop-content"><RouterView /></div>
  </div>
</template>
