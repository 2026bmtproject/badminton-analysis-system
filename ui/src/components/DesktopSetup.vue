<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useDesktopStatus } from "../composables/useDesktopStatus";

const { status, error: statusError, refresh } = useDesktopStatus();
const error = ref("");
const choosing = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
const canChange = computed(() => !status.value?.service?.port ||
  (status.value.service.connected && status.value.service.health?.activeTask === null));
const service = computed(() => status.value?.service ?? null);
const serviceDetail = computed(() => {
  const value = service.value;
  if (!value?.connected) return "";
  return [value.port ? `埠 ${value.port}` : "", value.owned ? "由本程式啟動" : "使用既有的服務"].filter(Boolean).join(" · ");
});
const fields = computed(() => [
  { kind: "Matches" as const, title: "Matches 資料夾", value: status.value?.settings.matchesDir, fallback: "尚未選擇",
    description: "比賽影片與分析結果所在的資料夾。每個資料夾有自己的任務記錄與回看，切換後會改用該資料夾的那一份。" },
  { kind: "Backend" as const, title: "Python 專案", value: status.value?.settings.backendDir, fallback: "尚未選擇",
    description: "執行分析的 Python checkout，須包含 pyproject.toml、modules/local_tasks/service.py，並已用 uv sync 準備好 .venv。" },
  { kind: "Uv" as const, title: "uv 執行檔", value: status.value?.settings.uvBinary, fallback: "uv",
    description: "啟動分析服務用的 uv。保留 uv 表示使用 PATH 上的版本。" },
]);

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
  <div class="desktop-setup">
    <p class="setting-service" :data-connected="Boolean(service?.connected)" role="status">
      <span class="setting-service-dot" aria-hidden="true" />{{ service?.connected ? "分析服務已連線" : "分析服務未連線" }}
      <span v-if="serviceDetail" class="setting-service-detail">{{ serviceDetail }}</span>
    </p>
    <p v-if="error || statusError || status?.error" class="setting-validation" role="alert">{{ error || statusError || status?.error }}</p>
    <p v-if="service?.warning" class="setting-validation setting-validation--warning">{{ service.warning }}</p>
    <p v-if="!canChange" class="setting-item-note">分析執行中或 worker 狀態未確認，暫時不能切換目錄。關閉視窗後分析會在背景繼續。</p>
    <div v-for="field in fields" :key="field.kind" class="setting-item">
      <h3 class="setting-item-title">{{ field.title }}</h3>
      <p class="setting-item-description">{{ field.description }}</p>
      <div class="setting-item-control">
        <span class="setting-path" :data-empty="!field.value">{{ field.value || field.fallback }}</span>
        <button type="button" class="button-secondary" :disabled="choosing || !canChange" :aria-label="`選擇${field.title}`" @click="choose(field.kind)">瀏覽…</button>
      </div>
    </div>
    <div class="setting-item">
      <h3 class="setting-item-title">使用者資料位置</h3>
      <p class="setting-item-description">任務記錄、已發布的回看與影片登錄，依 matches 資料夾分開存放。</p>
      <div class="setting-item-control"><span class="setting-path">{{ status?.dataDir || "讀取中" }}</span></div>
    </div>
  </div>
</template>
