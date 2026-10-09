<script setup lang="ts">
import DesktopSetup from "../components/DesktopSetup.vue";
const desktopAvailable = Boolean(window.badmintonDesktop);
</script>

<template>
  <main class="section-page settings-page">
    <header class="section-page-header"><div><h1>設定</h1></div></header>
    <section class="console-panel settings-summary">
      <h2>分析環境</h2>
      <p>桌面版沿用現有的 Python checkout 與 uv。若服務未連線，請展開進階設定檢查路徑。</p>
      <details :open="Boolean($route.query.advanced)">
        <summary>進階／開發環境</summary>
        <DesktopSetup v-if="desktopAvailable" />
        <p v-else class="secondary">目前是瀏覽器開發模式。matches 目錄、Python 服務與 uv 請在啟動本機服務時設定。</p>
      </details>
    </section>
  </main>
</template>
