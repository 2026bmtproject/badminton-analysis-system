<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { loadCatalog } from "../data/matchRepository";
import type { CatalogEntry } from "../domain/models";

const entries = ref<CatalogEntry[]>([]);
const loading = ref(true);
const loadError = ref("");
const heading = ref<HTMLHeadingElement | null>(null);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    entries.value = await loadCatalog();
  } catch {
    entries.value = [];
    loadError.value = "比賽目錄讀取失敗。請確認已匯入的前端資料後再試一次。";
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await nextTick();
  heading.value?.focus();
  await load();
});
</script>

<template>
  <div class="matches-page">
    <header class="matches-header">
      <div>
        <span class="section-kicker">Courtline</span>
        <h1 ref="heading" tabindex="-1">Matches</h1>
      </div>
      <span class="matches-count" aria-live="polite">
        {{ loading ? "讀取中" : `${entries.length} 場` }}
      </span>
    </header>

    <main class="matches-main">
      <div class="matches-intro">
        <h2>選擇比賽</h2>
        <p>開啟已匯入的比賽資料與回看工作區。</p>
      </div>

      <p v-if="loading" class="matches-status" role="status">載入比賽目錄…</p>
      <div v-else-if="loadError" class="error" role="alert">
        {{ loadError }}
        <button type="button" @click="load">重試</button>
      </div>
      <p v-else-if="!entries.length" class="empty-state">
        尚無可用的比賽資料。
      </p>
      <nav v-else class="matches-list" aria-label="比賽目錄">
        <RouterLink
          v-for="entry in entries"
          :key="entry.id"
          class="match-row"
          :to="{
            name: 'match-review',
            params: { matchId: entry.id },
          }"
        >
          <span class="match-row-main">
            <strong>{{ entry.name }}</strong>
            <code>{{ entry.id }}</code>
          </span>
          <span class="match-row-kind">
            {{ entry.kind === "match" ? "已匯入比賽" : "示範資料" }}
          </span>
          <span class="match-row-action" aria-hidden="true">開啟 →</span>
        </RouterLink>
      </nav>
    </main>
  </div>
</template>
