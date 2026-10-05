<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { useRoute } from "vue-router";
import AppIcon from "./components/ui/AppIcon.vue";

const connected = ref<boolean | null>(null);
const route = useRoute();
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
      <RouterLink :to="{ name: 'tasks' }" title="任務" aria-label="任務" active-class="is-active"><AppIcon name="tasks" :size="21" /><span>任務</span></RouterLink>
      <RouterLink :to="{ name: 'settings' }" title="設定" aria-label="設定" active-class="is-active"><AppIcon name="settings" :size="21" /><span>設定</span></RouterLink>
      <RouterLink class="desktop-nav-status" :to="{ name: 'settings' }" :title="connected ? '分析服務已連線' : '分析服務未連線'" :aria-label="connected ? '分析服務已連線，查看設定' : '分析服務未連線，查看設定'"><span class="connection-dot" :class="{ online: connected }" /></RouterLink>
    </nav>
    <div class="desktop-content"><RouterView /></div>
  </div>
</template>
