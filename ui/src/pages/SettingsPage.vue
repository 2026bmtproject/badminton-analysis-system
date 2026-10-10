<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import DesktopSetup from "../components/DesktopSetup.vue";
import {
  MAX_FULLSCREEN_IDLE_SEC, MIN_FULLSCREEN_IDLE_SEC, constrainFullscreenIdleSec, resetWorkspaceWindows, useWorkspaceLayout,
} from "../state/workspaceLayout";

const desktopAvailable = Boolean(window.badmintonDesktop);
const { layout } = useWorkspaceLayout();
const sections = [
  { id: "analysis-environment", title: "分析環境" },
  { id: "review-workspace", title: "回看工作區" },
] as const;
const current = ref<string>(sections[0].id);
const idleChoices = Array.from({ length: MAX_FULLSCREEN_IDLE_SEC - MIN_FULLSCREEN_IDLE_SEC + 1 }, (_, i) => MIN_FULLSCREEN_IDLE_SEC + i);
const idleSeconds = computed({
  get: () => layout.fullscreenIdleSec === null ? "never" : String(layout.fullscreenIdleSec),
  set: (value: string) => { layout.fullscreenIdleSec = value === "never" ? null : constrainFullscreenIdleSec(Number(value)); },
});
const reset = ref(false);
function resetWindows() {
  resetWorkspaceWindows(layout);
  reset.value = true;
}

/*
 * The contents list marks the section the user picked, then follows scrolling: the last section whose heading has
 * passed the top third of the window, or the last one once the page is scrolled to its end. A page shorter than the
 * window never scrolls, so the pick alone has to move the mark.
 */
const route = useRoute();
const page = ref<HTMLElement | null>(null);
function picked() {
  const id = route.hash.slice(1);
  if (sections.some(section => section.id === id)) current.value = id;
}
/** Picking the section already in the URL changes no route, so the click itself scrolls and marks it. */
function pick(id: string) {
  current.value = id;
  document.getElementById(id)?.scrollIntoView({ block: "start" });
}
function track() {
  const scroller = page.value?.parentElement;
  if (scroller && scroller.scrollTop > 0 && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2) {
    current.value = sections[sections.length - 1].id;
    return;
  }
  const line = window.innerHeight / 3;
  current.value = sections.reduce<string>((found, section) => {
    const top = document.getElementById(section.id)?.getBoundingClientRect().top ?? Infinity;
    return top <= line ? section.id : found;
  }, sections[0].id);
}
watch(() => route.hash, picked);
// The shell's content area scrolls, not the window; scroll events do not bubble, so listen while capturing.
onMounted(() => { track(); picked(); document.addEventListener("scroll", track, { capture: true, passive: true }); });
onUnmounted(() => document.removeEventListener("scroll", track, { capture: true }));
</script>

<template>
  <main ref="page" class="section-page settings-page">
    <header class="section-page-header"><h1>設定</h1></header>
    <div class="settings-layout">
      <nav class="settings-toc" aria-label="設定分類">
        <RouterLink v-for="section in sections" :key="section.id" :to="{ hash: `#${section.id}` }" :aria-current="current === section.id ? 'location' : undefined" @click="pick(section.id)">{{ section.title }}</RouterLink>
      </nav>
      <div class="settings-sections">
        <section id="analysis-environment" class="settings-section" aria-labelledby="analysis-environment-title">
          <h2 id="analysis-environment-title">分析環境</h2>
          <DesktopSetup v-if="desktopAvailable" />
          <div v-else class="setting-item">
            <h3 class="setting-item-title">瀏覽器開發模式</h3>
            <p class="setting-item-description">matches 資料夾、Python 專案與 uv 在啟動本機服務時以環境變數指定：<code>BADMINTON_MATCHES_DIR</code>、<code>BADMINTON_BACKEND_DIR</code>、<code>UV_BINARY</code>。</p>
          </div>
        </section>
        <section id="review-workspace" class="settings-section" aria-labelledby="review-workspace-title">
          <h2 id="review-workspace-title">回看工作區</h2>
          <div class="setting-item">
            <label class="setting-item-title" for="setting-idle">全螢幕閒置後隱藏視窗</label>
            <p class="setting-item-description">全螢幕時，滑鼠與鍵盤靜止多久後隱藏時間軸與分析視窗。也可在全螢幕視窗的設定中調整。</p>
            <div class="setting-item-control">
              <select id="setting-idle" v-model="idleSeconds">
                <option v-for="sec in idleChoices" :key="sec" :value="String(sec)">{{ sec }} 秒</option>
                <option value="never">不隱藏</option>
              </select>
            </div>
          </div>
          <div class="setting-item">
            <h3 class="setting-item-title">視窗位置與大小</h3>
            <p class="setting-item-description">時間軸與分析欄的寬高，以及全螢幕視窗的位置、大小與透明度，全部回到預設。</p>
            <div class="setting-item-control">
              <button type="button" class="button-secondary" @click="resetWindows">回復預設</button>
              <span v-if="reset" class="setting-item-note" role="status">已回復預設</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  </main>
</template>
