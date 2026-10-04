<script setup lang="ts">
import { computed, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import type { LocationQueryRaw } from "vue-router";
import type { MatchModel, RallyModel } from "../../domain/models";
import { formatTime, scoreText } from "../../format";
import {
  browseRallies,
  normalizeRallyBrowseQuery,
  rallyGames,
  type CommentaryFilter,
} from "../../rallies/rallyBrowse";
import { hitStatus } from "../../review";
import AppIcon from "../ui/AppIcon.vue";

const props = defineProps<{ model: MatchModel }>();
const route = useRoute();
const router = useRouter();
const normalized = computed(() =>
  normalizeRallyBrowseQuery(route.query, props.model),
);
const games = computed(() => rallyGames(props.model));
const rallies = computed(() =>
  browseRallies(props.model, normalized.value.state),
);

function sameQuery(query: LocationQueryRaw) {
  const current = Object.fromEntries(
    Object.entries(route.query).filter(([, value]) => value !== undefined),
  );
  return JSON.stringify(current) === JSON.stringify(query);
}

watch(
  normalized,
  (value) => {
    if (!sameQuery(value.query)) void router.replace({ query: value.query });
  },
  { immediate: true },
);

function replaceQuery(
  change: Partial<{ game: string; commentary: CommentaryFilter; sort: string }>,
) {
  const next: LocationQueryRaw = { ...normalized.value.query, ...change };
  for (const [key, value] of Object.entries(next)) {
    if (!value || value === "all" || value === "time") delete next[key];
  }
  void router.replace({ query: next });
}

function rallyScore(rally: RallyModel) {
  if (rally.score) return scoreText(rally.score);
  return props.model.states.scores?.status === "error" ? "比分資料讀取失敗"
    : props.model.states.scores?.status === "stale" ? "比分資料已過期" : "比分未提供";
}

function commentaryLabel(rally: RallyModel) {
  if (rally.commentary.status === "available") return "賽評可用";
  if (rally.commentary.status === "unsupported") return "賽評不支援";
  if (props.model.states.commentary?.status === "stale" ||
    props.model.states.commentary_segments?.status === "stale") return "賽評資料已過期";
  return props.model.states.commentary?.status === "error" ||
    props.model.states.commentary_segments?.status === "error"
    ? "賽評讀取失敗"
    : "賽評未提供";
}
</script>

<template>
  <section class="rallies-browser" aria-labelledby="rallies-heading">
    <header class="rallies-page-heading">
      <div>
        <span class="section-kicker">Rallies</span>
        <h2 id="rallies-heading">片段導覽</h2>
      </div>
      <span>{{ rallies.length }} / {{ model.rallies.length }}</span>
    </header>

    <div class="rally-browse-controls" aria-label="片段篩選與排序">
      <label v-if="games.length > 1">
        局數
        <select
          :value="
            normalized.state.game === null
              ? 'all'
              : String(normalized.state.game)
          "
          @change="
            replaceQuery({ game: ($event.target as HTMLSelectElement).value })
          "
        >
          <option value="all">全部</option>
          <option v-for="game in games" :key="game" :value="game">
            第 {{ game + 1 }} 局
          </option>
        </select>
      </label>
      <label>
        賽評
        <select
          :value="normalized.state.commentary"
          @change="
            replaceQuery({
              commentary: ($event.target as HTMLSelectElement)
                .value as CommentaryFilter,
            })
          "
        >
          <option value="all">全部</option>
          <option value="available">可用</option>
          <option value="unavailable">不可用</option>
        </select>
      </label>
      <label>
        排序
        <select
          :value="normalized.state.sort"
          @change="
            replaceQuery({ sort: ($event.target as HTMLSelectElement).value })
          "
        >
          <option value="time">比賽順序</option>
          <option v-if="model.capabilities.highlight" value="highlight">
            精華分數
          </option>
          <option v-if="model.capabilities.cheer" value="cheer">歡呼</option>
        </select>
      </label>
    </div>

    <div class="rallies-records">
      <RouterLink
        v-for="rally in rallies"
        :key="rally.id"
        class="rally-record"
        :data-rally-id="rally.id"
        :to="{
          name: 'match-review',
          params: { matchId: $route.params.matchId },
          query: { segment: String(rally.id) },
        }"
      >
        <strong class="rally-record-id">{{
          String(rally.id + 1).padStart(3, "0")
        }}</strong>
        <span class="rally-record-context">
          <b>{{
            rally.game === null ? "局數未提供" : `第 ${rally.game + 1} 局`
          }}</b>
          <span>{{ rallyScore(rally) }}</span>
        </span>
        <span>{{ formatTime(rally.start) }}–{{ formatTime(rally.end) }}</span>
        <span>{{ rally.duration.toFixed(2) }} 秒</span>
        <span>{{
          hitStatus(model.states.events?.status, rally.hits?.length ?? null)
        }}</span>
        <span v-if="model.capabilities.highlight || model.capabilities.cheer" class="rally-record-signals">
          <span v-if="model.capabilities.highlight">精華 {{ rally.highlight?.toFixed(3) ?? "未提供" }}</span>
          <span v-if="model.capabilities.cheer">歡呼 {{ rally.audio?.confidence.toFixed(2) ?? "未提供" }}</span>
        </span>
        <span>{{ commentaryLabel(rally) }}</span>
        <AppIcon name="chevron-right" />
      </RouterLink>
      <p v-if="!rallies.length" class="empty-state">沒有符合目前條件的片段。</p>
    </div>
  </section>
</template>
