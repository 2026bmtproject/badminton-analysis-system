<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import type { DesktopStatus } from "../desktop";

const status = ref<DesktopStatus | null>(null);
const error = ref("");
const choosing = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
const canChange = computed(() => !status.value?.service?.port ||
  (status.value.service.connected && status.value.service.health?.activeTask === null));

async function refresh() {
  try { status.value = await window.badmintonDesktop!.status(); error.value = ""; }
  catch (cause) { error.value = cause instanceof Error ? cause.message : "無法讀取桌面狀態"; }
}
async function choose(kind: "Matches" | "Backend" | "Uv") {
  choosing.value = true; error.value = "";
  try {
    status.value = await window.badmintonDesktop![`choose${kind}`]();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "無法更新桌面設定";
  } finally { choosing.value = false; }
}
onMounted(() => { void refresh(); timer = setInterval(() => { void refresh(); }, 3000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <section class="desktop-setup" aria-label="桌面設定與連線狀態">
    <div class="desktop-setup-heading"><h2>桌面分析環境</h2>
      <span role="status">{{ status?.service?.connected ? "分析服務已連線" : "分析服務未連線" }}</span></div>
    <p v-if="status?.error || error" class="error" role="alert">{{ error || status?.error }}</p>
    <p v-if="status?.service?.warning" class="desktop-setup-note">{{ status.service.warning }}</p>
    <dl>
      <div><dt>Matches</dt><dd>{{ status?.settings.matchesDir || "尚未選擇" }}</dd></div>
      <div><dt>Python checkout</dt><dd>{{ status?.settings.backendDir || "尚未選擇" }}</dd></div>
      <div><dt>uv</dt><dd>{{ status?.settings.uvBinary || "uv" }}</dd></div>
      <div><dt>使用者資料</dt><dd>{{ status?.dataDir || "讀取中" }}</dd></div>
    </dl>
    <div class="desktop-setup-actions">
      <button type="button" :disabled="choosing || !canChange" @click="choose('Matches')">選擇 matches 資料夾</button>
      <button type="button" :disabled="choosing || !canChange" @click="choose('Backend')">選擇 Python checkout</button>
      <button type="button" :disabled="choosing || !canChange" @click="choose('Uv')">選擇 uv.exe</button>
    </div>
    <p v-if="!canChange" class="desktop-setup-note">分析執行中或 worker 狀態未確認，暫時不能切換目錄。關閉視窗後分析會在背景繼續。</p>
  </section>
</template>
