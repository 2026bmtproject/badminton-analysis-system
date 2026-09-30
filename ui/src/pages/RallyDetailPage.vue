<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import ReviewPlayer from "../components/ReviewPlayer.vue";
import ReviewTimeline from "../components/ReviewTimeline.vue";
import RallyDetail from "../components/inspector/RallyDetail.vue";
import RallyCourtMap from "../components/inspector/RallyCourtMap.vue";
import type {
  CommentaryEventModel,
  EvidenceModel,
  RallyModel,
  StrokeModel,
} from "../domain/models";
import { formatTime, scoreText } from "../format";
import {
  findStrokeTarget,
  resolveRouteRally,
  resolveRouteStroke,
} from "../rallies/rallyRoute";
import { hitStatus } from "../review";
import { useMatchContext } from "../state/matchContext";

const context = useMatchContext();
const { model, workspace } = context;
const route = useRoute();
const router = useRouter();
const player = ref<InstanceType<typeof ReviewPlayer> | null>(null);
const strokeRouteError = ref(false);
const match = computed(() => {
  if (!model.value)
    throw new Error("Rally Detail requires a loaded MatchModel");
  return model.value;
});
const rally = computed(() =>
  resolveRouteRally(match.value, route.params.segmentId),
);
const position = computed(() =>
  rally.value
    ? match.value.rallies.findIndex((item) => item.id === rally.value?.id)
    : -1,
);
const previous = computed(() =>
  position.value > 0 ? match.value.rallies[position.value - 1] : null,
);
const next = computed(() =>
  position.value >= 0
    ? (match.value.rallies[position.value + 1] ?? null)
    : null,
);
const playerContext = computed(() => {
  const item = rally.value;
  return item
    ? {
        label: `片段 ${String(item.id + 1).padStart(3, "0")}`,
        score: item.score ? scoreText(item.score) : null,
        meta: `${formatTime(item.start)}–${formatTime(item.end)} · ${hitStatus(match.value.states.events?.status, item.hits?.length ?? null)}`,
      }
    : { label: "片段不存在", score: null, meta: "" };
});

function routeQueryStroke() {
  return Array.isArray(route.query.stroke)
    ? route.query.stroke[0]
    : route.query.stroke;
}
function synchronizeRoute() {
  const target = rally.value;
  if (!target) {
    workspace.clearSelection();
    strokeRouteError.value = false;
    return;
  }
  const queryStroke = routeQueryStroke();
  if (queryStroke === undefined) {
    strokeRouteError.value = false;
    workspace.selectRally(target);
    return;
  }
  const stroke = resolveRouteStroke(target, queryStroke);
  if (!stroke) {
    strokeRouteError.value = true;
    workspace.selectRally(target);
    return;
  }
  strokeRouteError.value = false;
  workspace.selectStroke(stroke);
}
watch(
  [() => route.params.segmentId, () => route.query.stroke, model],
  synchronizeRoute,
  { immediate: true, flush: "post" },
);
watch(
  player,
  (value) => {
    context.player.value = value;
    if (value) synchronizeRoute();
  },
  { flush: "sync" },
);
onBeforeUnmount(() => {
  if (context.player.value === player.value) context.player.value = null;
});

function detailLocation(target: RallyModel, stroke?: StrokeModel) {
  return {
    name: "rally-detail" as const,
    params: { matchId: route.params.matchId, segmentId: target.id },
    query: stroke ? { stroke: String(stroke.eventIndex) } : {},
  };
}
function selectStroke(stroke: StrokeModel) {
  workspace.selectStroke(stroke);
  void router.replace(detailLocation(rally.value!, stroke));
}
function selectCommentary(comment: CommentaryEventModel, owner: RallyModel) {
  const stroke = owner.hits?.find(
    (item) => item.eventIndex === comment.strokeIndex,
  );
  if (stroke) selectStroke(stroke);
}
function moveStroke(direction: -1 | 1) {
  if (!workspace.moveStroke(direction) || !workspace.selectedStroke.value)
    return;
  void router.replace(
    detailLocation(rally.value!, workspace.selectedStroke.value),
  );
}
function openEvidence(evidence: EvidenceModel) {
  const target = findStrokeTarget(match.value, evidence.eventIndex);
  if (!target) return;
  if (target.rally.id === rally.value?.id) selectStroke(target.stroke);
  else void router.push(detailLocation(target.rally, target.stroke));
}
</script>

<template>
  <main class="rally-detail-page">
    <section v-if="!rally" class="route-not-found" role="alert">
      <span class="section-kicker">Rally not found</span>
      <h1>找不到指定片段</h1>
      <p>此比賽沒有 ID 為「{{ route.params.segmentId }}」的片段。</p>
      <RouterLink
        :to="{
          name: 'match-rallies',
          params: { matchId: route.params.matchId },
        }"
        >返回 Rallies</RouterLink
      >
    </section>
    <template v-else>
      <nav class="rally-detail-route-nav" aria-label="片段切換">
        <RouterLink
          :to="{
            name: 'match-rallies',
            params: { matchId: route.params.matchId },
          }"
          >← 返回 Rallies</RouterLink
        >
        <div>
          <RouterLink v-if="previous" :to="detailLocation(previous)"
            >上一段</RouterLink
          >
          <span v-else aria-hidden="true">上一段</span>
          <RouterLink v-if="next" :to="detailLocation(next)">下一段</RouterLink>
          <span v-else aria-hidden="true">下一段</span>
        </div>
      </nav>
      <p
        v-if="strokeRouteError"
        class="notice rally-route-notice"
        role="status"
      >
        指定的擊球不在此片段中。
      </p>
      <div class="rally-detail-macro">
        <section class="rally-detail-media" aria-label="片段影片">
          <ReviewPlayer
            v-if="!match.layoutOnly"
            ref="player"
            :src="match.video"
            :context="playerContext"
            :active-rally="rally"
            :active-stroke="workspace.selectedStroke.value"
            @time="workspace.updateTime"
          />
        </section>
        <RallyCourtMap
          :rally="rally"
          :players="match.players"
          :selected-stroke-index="workspace.selectedStrokeIndex.value"
          :calibration-unconfirmed="match.source?.limitations.some((item) => item.includes('球場為自動校正'))"
          @stroke="selectStroke"
        />
      </div>
      <div class="rally-detail-timeline">
        <ReviewTimeline
          mode="rally-detail"
          :model="match"
          :selected-id="rally.id"
          :selected-stroke-index="workspace.selectedStrokeIndex.value"
          :time="workspace.currentTimeSec.value"
          :active-id="workspace.activeRally.value?.id ?? null"
          @rally="workspace.selectRally"
          @rally-at="workspace.selectRallyAt"
          @stroke="selectStroke"
          @commentary="selectCommentary"
          @seek="workspace.seek"
          @inspect="player?.setInspectionTime($event)"
        />
      </div>
        <RallyDetail
          :model="match"
          :rally="rally"
          :active-id="workspace.activeRally.value?.id ?? null"
          :selected-stroke-index="workspace.selectedStrokeIndex.value"
          page
          :show-stroke-list="false"
          @stroke="selectStroke"
          @evidence="openEvidence"
          @previous-stroke="moveStroke(-1)"
          @next-stroke="moveStroke(1)"
        >
        </RallyDetail>
    </template>
  </main>
</template>
