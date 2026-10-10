<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, watchEffect } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useRoute } from "vue-router";
import AppIcon from "./components/ui/AppIcon.vue";
import { useDesktopStatus } from "./composables/useDesktopStatus";
import { useTaskFeed } from "./composables/useTaskFeed";
import { taskProgress } from "./data/pipelineTasks";
import { matchTitle, windowContext, windowTitle } from "./state/windowTitle";

const route = useRoute();
// Connection only shows when it is lost: a permanent green dot said nothing, and pages that need the service
// already explain when it is missing. This covers the ones that do not, such as Review.
const { active, unseenFailure, offline } = useTaskFeed();
const desktop = useDesktopStatus();
const activePercent = computed(() => active.value ? Math.round(taskProgress(active.value).fraction * 100) : 0);
const tasksTitle = computed(() => unseenFailure.value ? "任務：上一個分析沒有完成" : "任務");
const title = computed(() => windowTitle(windowContext(route.name, matchTitle.value)));
const matchesDir = computed(() => desktop.status.value?.settings.matchesDir ?? null);
const matchesFolder = computed(() => matchesDir.value?.split(/[\\/]/).filter(Boolean).pop() ?? null);
const environmentLink = { name: "settings", hash: "#analysis-environment" } as const;

// Electron shows the document title on the taskbar; the custom title bar repeats it.
watchEffect(() => { document.title = title.value; });
// The content area scrolls by itself, as VS Code's editor does, so a navigation resets it rather than the window.
const content = ref<HTMLElement | null>(null);
watch(() => route.fullPath, () => {
  if (route.hash) document.getElementById(route.hash.slice(1))?.scrollIntoView({ block: "start" });
  else content.value?.scrollTo({ top: 0 });
}, { flush: "post" });
const refreshDesktop = () => { void desktop.refresh(); };
onMounted(() => { refreshDesktop(); window.addEventListener("focus", refreshDesktop); });
onUnmounted(() => window.removeEventListener("focus", refreshDesktop));
</script>

<template>
  <div class="desktop-shell" :class="{ 'desktop-shell--titlebar': desktop.available }">
    <header v-if="desktop.available" class="title-bar">
      <span class="title-bar-brand" aria-hidden="true">B</span>
      <span class="title-bar-title">{{ title }}</span>
    </header>
    <nav class="desktop-nav" aria-label="主要導覽">
      <RouterLink :to="{ name: 'matches' }" title="比賽庫" aria-label="比賽庫" :class="{ 'is-active': route.path.startsWith('/matches') || route.path.startsWith('/analysis') }"><AppIcon name="library" :size="24" /></RouterLink>
      <RouterLink class="desktop-nav-tasks" :to="{ name: 'tasks' }" :title="tasksTitle" :aria-label="tasksTitle" active-class="is-active"><AppIcon name="tasks" :size="24" />
        <span v-if="unseenFailure" class="nav-task-alert" aria-hidden="true" /></RouterLink>
      <RouterLink class="desktop-nav-settings" :to="{ name: 'settings' }" title="設定" aria-label="設定" active-class="is-active"><AppIcon name="settings" :size="24" /></RouterLink>
    </nav>
    <div ref="content" class="desktop-content"><RouterView /></div>
    <footer class="status-bar" aria-label="狀態列">
      <RouterLink v-if="matchesFolder" class="status-bar-item" :to="environmentLink" :title="`matches 資料夾：${matchesDir}`"><AppIcon name="folder" :size="14" />{{ matchesFolder }}</RouterLink>
      <RouterLink v-if="offline" class="status-bar-item status-bar-item--error" :to="environmentLink" title="分析服務未連線，查看設定"><AppIcon name="alert" :size="14" />分析服務未連線</RouterLink>
      <RouterLink v-if="active" class="status-bar-item" :to="{ name: 'tasks' }" :title="`任務：${active.matchId} 分析中 ${activePercent}%`" role="status"><AppIcon class="status-bar-spin" name="refresh" :size="14" />{{ active.matchId }} 分析中 {{ activePercent }}%</RouterLink>
    </footer>
  </div>
</template>
