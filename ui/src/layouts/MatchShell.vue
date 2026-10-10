<script setup lang="ts">
import { computed, nextTick, ref, watch, KeepAlive } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { loadCatalog, loadMatch } from "../data/matchRepository";
import type { CatalogEntry } from "../domain/models";
import { createMatchContext, provideMatchContext } from "../state/matchContext";

const route = useRoute();
const context = createMatchContext();
provideMatchContext(context);

const catalog = ref<CatalogEntry[] | null>(null);
const entry = ref<CatalogEntry | null>(null);
const loading = ref(true);
const loadError = ref("");
const heading = ref<HTMLHeadingElement | null>(null);
let request = 0;
const displayTitle = computed(() => {
  const title = context.model.value?.title ?? entry.value?.name ?? "";
  return !title || /^(?:match:)?yt[_:-]/i.test(title) ? "比賽回看" : title;
});

function routeMatchId(): string | null {
  const value = route.params.matchId;
  return typeof value === "string" && value.length ? value : null;
}

async function loadRequestedMatch() {
  const ticket = ++request;
  const matchId = routeMatchId();
  loading.value = true;
  loadError.value = "";
  entry.value = null;
  context.model.value = null;
  try {
    if (!matchId) throw new Error("invalid match id");
    catalog.value ??= await loadCatalog();
    const selected = catalog.value.find((item) => item.id === matchId);
    if (!selected) {
      loadError.value = `找不到比賽「${matchId}」。`;
      return;
    }
    const model = await loadMatch(selected);
    if (ticket !== request) return;
    entry.value = selected;
    context.model.value = model;
    await nextTick();
    heading.value?.focus();
  } catch {
    if (ticket !== request) return;
    loadError.value = "回看資料讀取失敗。請重新匯入該比賽後再試一次。";
  } finally {
    if (ticket === request) loading.value = false;
  }
}

/** A re-import publishes the Review under a new content-hashed URL, so the catalog is read again first. */
async function refreshMatch() {
  const current = entry.value;
  if (!current) throw new Error("No Match is loaded");
  catalog.value = await loadCatalog();
  const selected = catalog.value.find((item) => item.id === current.id) ?? current;
  const model = await loadMatch(selected, { fresh: true });
  if (entry.value?.id !== current.id) return;
  entry.value = selected;
  context.model.value = model;
}

context.setRefreshMatch(refreshMatch);

watch(() => route.params.matchId, loadRequestedMatch, { immediate: true });
</script>

<template>
  <div class="app-shell match-shell">
    <h1 ref="heading" class="sr-only" tabindex="-1">{{ displayTitle }}</h1>

    <p v-if="loading" class="sr-only" role="status">載入比賽資料…</p>
    <main v-if="loading" class="shell-state" aria-hidden="true">
      <span class="loading-indicator" />
      <strong>載入比賽資料…</strong>
    </main>
    <main v-else-if="loadError" class="shell-error">
      <div class="error" role="alert">
        {{ loadError }}
        <button type="button" @click="loadRequestedMatch">重試</button>
      </div>
      <RouterLink class="match-back-link" :to="{ name: 'matches' }">
        返回比賽庫
      </RouterLink>
    </main>
    <RouterView v-else-if="context.model.value" v-slot="{ Component }">
      <KeepAlive include="ReviewPage"><component :is="Component" /></KeepAlive>
    </RouterView>
  </div>
</template>
