<script setup lang="ts">
import { computed, ref } from "vue";
import type {
  EvidenceModel,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../../domain/models";
import {
  formatPreciseTime,
  formatPreciseTimeParts,
  formatTime,
  playerName,
  scoreText,
} from "../../format";
import { hitStatus, relatedCommentaryEvents } from "../../review";
import AppIcon from "../ui/AppIcon.vue";

const props = withDefaults(
  defineProps<{
    model: MatchModel;
    rally: RallyModel;
    activeId: number | null;
    selectedStrokeIndex: number | null;
    page?: boolean;
    showStrokeList?: boolean;
    followPlayback?: boolean;
  }>(),
  { page: false, showStrokeList: true, followPlayback: false },
);
const emit = defineEmits<{
  back: [];
  previous: [];
  next: [];
  stroke: [stroke: StrokeModel];
  previousStroke: [];
  nextStroke: [];
  evidence: [evidence: EvidenceModel];
}>();
const position = computed(() =>
  props.model.rallies.findIndex((item) => item.id === props.rally.id),
);
const selectedStroke = computed(
  () =>
    props.rally.hits?.find(
      (stroke) => stroke.eventIndex === props.selectedStrokeIndex,
    ) ?? null,
);
const selectedStrokePosition = computed(
  () =>
    props.rally.hits?.findIndex(
      (stroke) => stroke.eventIndex === props.selectedStrokeIndex,
    ) ?? -1,
);
const related = computed(() =>
  selectedStroke.value
    ? relatedCommentaryEvents(
        props.rally.commentary.events,
        selectedStroke.value.eventIndex,
      )
    : [],
);
const summary = computed(() => props.rally.commentary.summary);
const commentaryStatus = computed(() => {
  if (props.rally.commentary.status === "unsupported") {
    return (
      {
        player_identity_unavailable: "此片段缺少可用的球員身分，無法提供賽評。",
        multiple_recovered_rallies_unsupported:
          "此片段含多個回合，暫不支援賽評。",
      }[props.rally.commentary.unsupportedReason ?? ""] ??
      "此片段目前不支援賽評。"
    );
  }
  if (
    props.model.states.commentary?.status === "error" ||
    props.model.states.commentary_segments?.status === "error"
  )
    return "部分賽評資料讀取失敗";
  return "此片段尚無賽評";
});
const backButton = ref<HTMLButtonElement | null>(null);
const scroll = ref<HTMLElement | null>(null);
const stageLabels: Record<string, string> = {
  segments: "片段",
  scores: "比分",
  events: "擊球",
  strokes: "球種",
  identity: "球員身分",
  audio_signals: "歡呼訊號",
  highlights: "精華",
  commentary: "賽評",
  commentary_segments: "隨選賽評",
  court: "場地",
  pose: "姿態",
  shuttle: "羽球軌跡",
};
const stageStatuses = {
  available: "可用",
  missing: "未提供",
  error: "讀取失敗",
  stale: "輸入已過期",
  unknown: "舊資料（指紋未知）",
} as const;

function focusBack() {
  backButton.value?.focus();
}
function resetScroll() {
  if (scroll.value) scroll.value.scrollTop = 0;
}
defineExpose({ focusBack, resetScroll });

function status(name: string) {
  const state = props.model.states[name];
  return state?.status === "error"
    ? "讀取失敗"
    : state?.status === "stale"
      ? "資料已過期"
    : state?.status === "missing"
      ? "未提供"
      : "此片段未提供";
}
function gameSourceLabel(source: RallyModel["gameSource"]) {
  return source === "identity" ? "身分推導" : "比分資料";
}
function openEvidence(evidence: EvidenceModel) {
  emit("evidence", evidence);
}
function evidenceNavigable(evidence: EvidenceModel) {
  return (
    evidence.eventIndex !== null &&
    props.model.rallies.some((rally) =>
      rally.hits?.some((stroke) => stroke.eventIndex === evidence.eventIndex),
    )
  );
}
</script>

<template>
  <div class="rally-detail" :class="{ 'rally-detail--page': page }">
    <nav v-if="!page && !followPlayback" class="detail-nav" aria-label="片段切換">
      <button ref="backButton" @click="emit('back')">
        <AppIcon name="arrow-left" />返回清單
      </button>
      <div>
        <button :disabled="position === 0" @click="emit('previous')">
          <AppIcon name="chevron-left" />上一段
        </button>
        <button
          :disabled="position === model.rallies.length - 1"
          @click="emit('next')"
        >
          下一段<AppIcon name="chevron-right" />
        </button>
      </div>
    </nav>
    <div ref="scroll" class="detail-scroll">
      <header v-if="!followPlayback" class="detail-title">
        <div>
          <span class="detail-kicker">片段分析</span>
          <h2>
            <span>片段</span>
            <span class="detail-rally-index">{{
              String(rally.id + 1).padStart(3, "0")
            }}</span>
          </h2>
        </div>
        <span>{{ activeId === rally.id ? "目前播放" : "已選取" }}</span>
      </header>

      <section class="score-section" :class="{ 'score-section--contextual': followPlayback }">
        <div v-if="!followPlayback">
          <small>片段比分觀察</small
          ><strong
            v-if="rally.score"
            class="score-state detail-score"
            :aria-label="scoreText(rally.score)"
          >
            <span>{{ rally.score[0] }}</span>
            <span class="score-state-divider" aria-hidden="true" />
            <span>{{ rally.score[1] }}</span>
          </strong>
          <strong v-else class="detail-score">{{ status("scores") }}</strong>
        </div>
        <div class="score-names">
          <span
            >{{ playerName(model.players.a, "選手 A") }} /
            {{ playerName(model.players.b, "選手 B") }}</span
          >
          <small
            >{{
              rally.game === null ? "局數未提供" : `第 ${rally.game + 1} 局`
            }}
            · {{ rally.duration.toFixed(2) }} 秒</small
          >
        </div>
      </section>
      <section class="rally-facts" aria-label="片段事實">
        <div>
          <small>時間</small>
          <strong
            >{{ formatTime(rally.start) }}–{{ formatTime(rally.end) }}</strong
          >
        </div>
        <div>
          <small>擊球</small>
          <strong>{{
            hitStatus(model.states.events?.status, rally.hits?.length ?? null)
          }}</strong>
        </div>
      </section>
      <p v-if="rally.gameConflict" class="notice">{{ rally.gameConflict }}</p>
      <p v-if="rally.multi" class="notice">
        此片段有多筆比分觀察 ·
        {{ rally.subScores.map(scoreText).join(" → ") }}；約
        {{ rally.splits.map(formatTime).join("、") }} 變動，非精確回合邊界。
      </p>

      <section
        v-if="model.capabilities.cheer || model.capabilities.highlight"
        class="derived-signals"
        aria-label="片段分析訊號"
      >
        <h3>分析訊號</h3>
        <dl>
          <div v-if="model.capabilities.cheer">
            <dt>歡呼訊號</dt>
            <dd>
              {{ rally.audio ? rally.audio.confidence.toFixed(2) : "未提供" }}
            </dd>
          </div>
          <div v-if="model.capabilities.highlight">
            <dt>精華分數</dt>
            <dd>{{ rally.highlight?.toFixed(3) ?? "未提供" }}</dd>
          </div>
        </dl>
      </section>

      <section
        v-if="rally.commentary.status !== 'available'"
        class="commentary-empty"
        aria-label="賽評狀態"
      >
        <h3>賽評</h3>
        <p>{{ commentaryStatus }}</p>
      </section>
      <section
        v-else-if="!summary"
        class="commentary-empty"
        aria-label="賽評摘要狀態"
      >
        <h3>賽評摘要</h3>
        <p>此片段有賽評事件，但未提供賽評摘要。</p>
      </section>
      <details v-if="summary" class="rally-summary">
        <summary>
          <span>回合摘要</span
          ><span class="summary-preview">{{ summary.text }}</span>
        </summary>
        <p>{{ summary.text }}</p>
        <div
          v-for="evidence in summary.evidence"
          :key="evidence.id"
          class="evidence"
        >
          <button
            v-if="evidenceNavigable(evidence)"
            @click="openEvidence(evidence)"
          >
            {{ evidence.text }}<span aria-hidden="true"> ↗</span>
          </button>
          <span v-else class="warning">{{ evidence.id }} · 無法取得依據</span>
        </div>
      </details>
      <div class="section-heading hit-heading">
        <h3>擊球序列</h3>
        <div class="stroke-navigation" aria-label="逐拍導覽">
          <span>全場時間</span>
          <button
            type="button"
            :disabled="!rally.hits?.length || selectedStrokePosition === 0"
            @click="emit('previousStroke')"
          >
            上一拍
          </button>
          <button
            type="button"
            :disabled="
              !rally.hits?.length ||
              selectedStrokePosition === (rally.hits?.length ?? 0) - 1
            "
            @click="emit('nextStroke')"
          >
            下一拍
          </button>
        </div>
      </div>
      <section v-if="selectedStroke" class="hit-reading">
        <h3>選中擊球</h3>
        <p>
          {{ formatPreciseTime(selectedStroke.time) }} ·
          {{ playerName(selectedStroke.player) }} ·
          {{ selectedStroke.type ?? "球種未提供"
          }}<template v-if="selectedStroke.confidence !== null">
            · 信心 {{ selectedStroke.confidence.toFixed(3) }}</template
          >
        </p>
        <article v-for="comment in related" :key="comment.strokeIndex">
          <small>賽評 · {{ playerName(model.players[comment.player]) }}</small>
          <p>{{ comment.text }}</p>
          <div
            v-for="evidence in comment.evidence"
            :key="evidence.id"
            class="evidence"
          >
            <button
              v-if="evidenceNavigable(evidence)"
              @click="openEvidence(evidence)"
            >
              {{ evidence.text }}<span aria-hidden="true"> ↗</span>
            </button>
            <span v-else class="warning">{{ evidence.id }} · 無法取得依據</span>
          </div>
        </article>
        <p
          v-if="rally.commentary.status === 'available' && !related.length"
          class="secondary"
        >
          此拍沒有賽評。
        </p>
      </section>
      <div v-if="showStrokeList && rally.hits !== null" class="hit-list">
        <template v-for="stroke in rally.hits" :key="stroke.eventIndex">
          <button
            :data-hit="stroke.eventIndex"
            :data-testid="`hit-${stroke.eventIndex}`"
            :aria-pressed="selectedStrokeIndex === stroke.eventIndex"
            :class="{ selected: selectedStrokeIndex === stroke.eventIndex }"
            @click="emit('stroke', stroke)"
          >
            <span class="hit-ordinal">{{
              String(stroke.ordinal).padStart(2, "0")
            }}</span>
            <time
              >{{ formatPreciseTimeParts(stroke.time).whole
              }}<small
                >.{{ formatPreciseTimeParts(stroke.time).fraction }}</small
              ></time
            >
            <strong>{{ stroke.type ?? "球種未提供" }}</strong>
            <span class="hit-player">{{ playerName(stroke.player) }}</span>
          </button>
        </template>
        <p v-if="rally.hits.length === 0" class="empty-state">未偵測到擊球</p>
      </div>
      <p v-else-if="showStrokeList" class="empty-state">
        {{ hitStatus(model.states.events?.status, null) }}
      </p>

      <details class="source-details">
        <summary>資料來源與限制</summary>
        <p v-if="rally.game !== null && rally.gameSource">
          局數來源：{{ gameSourceLabel(rally.gameSource) }}
        </p>
        <p v-if="rally.identity">
          畫面上方：{{
            playerName(model.players[rally.identity.top])
          }}；畫面下方：{{ playerName(model.players[rally.identity.bottom]) }}
        </p>
        <p v-if="rally.audio">
          歡呼訊號：{{ rally.audio.confidence }}；相對強度：{{
            rally.audio.intensity === null
              ? "不適用（null）"
              : rally.audio.intensity
          }}；訊號窗數：{{ rally.audio.windowCount }}
        </p>
        <p>精華分數：{{ rally.highlight ?? status("highlights") }}</p>
        <p v-for="(state, name) in model.states" :key="name">
          {{ stageLabels[name] ?? name }}：{{ stageStatuses[state.status]
          }}{{ state.message ? `（${state.message}）` : "" }}
        </p>
      </details>
    </div>
  </div>
</template>
