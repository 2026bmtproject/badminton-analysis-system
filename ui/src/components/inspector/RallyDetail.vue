<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type {
  EvidenceModel,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../../domain/models";
import { formatPreciseTime, formatTime, playerName, scoreText } from "../../format";
import { hitStatus } from "../../review";
import type { CommentaryRequestState } from "../../state/segmentCommentary";
import { strokeFamily, strokeGlyph } from "../../temporal/strokeRhythm";
import AppIcon from "../ui/AppIcon.vue";

const props = defineProps<{
  model: MatchModel;
  rally: RallyModel;
  /** The playhead has left this Rally; it stays on show until the next one starts. */
  previous: boolean;
  selectedStrokeIndex: number | null;
  commentaryRequest: CommentaryRequestState;
  strokesCollapsed: boolean;
}>();
const emit = defineEmits<{
  strokesCollapsed: [value: boolean];
  stroke: [stroke: StrokeModel];
  evidence: [evidence: EvidenceModel];
  requestCommentary: [rally: RallyModel];
}>();

/** Below this the classifier's shot label is a guess; it is shown, but marked as one. */
const LOW_CONFIDENCE = 0.5;
/** A highlight badge marks only the top tenth of the match's Rallies. */
const HIGHLIGHT_SHARE = 0.1;

const scroll = ref<HTMLElement | null>(null);
const summary = computed(() =>
  props.rally.commentary.status === "available" ? props.rally.commentary.summary : null,
);
const commentByStroke = computed(
  () => new Map(props.rally.commentary.events.map((event) => [event.strokeIndex, event])),
);
const ordinalByEvent = computed(
  () => new Map((props.rally.hits ?? []).map((stroke) => [stroke.eventIndex, stroke.ordinal])),
);
/** The summary's grounding, as the shots it cites; facts that are not a shot of this Rally are not linkable. */
const summaryRefs = computed(() => {
  const seen = new Set<number>();
  return (summary.value?.evidence ?? []).flatMap((evidence) => {
    const ordinal = evidence.eventIndex === null ? undefined : ordinalByEvent.value.get(evidence.eventIndex);
    if (ordinal === undefined || seen.has(ordinal)) return [];
    seen.add(ordinal);
    return [{ evidence, ordinal }];
  }).sort((a, b) => a.ordinal - b.ordinal);
});

const highlightRank = computed(() => {
  const own = props.rally.highlight;
  if (own === null) return null;
  const ranked = props.model.rallies.filter((rally) => rally.highlight !== null);
  const rank = 1 + ranked.filter((rally) => rally.highlight! > own).length;
  return rank <= Math.max(1, Math.ceil(ranked.length * HIGHLIGHT_SHARE)) ? { rank, total: ranked.length } : null;
});
const stageNames: Record<string, string> = {
  events: "擊球", strokes: "球種", identity: "球員身分", court: "場地", pose: "姿態",
  commentary: "賽評", commentary_segments: "單回合賽評",
};
const dataIssues = computed(() =>
  Object.entries(stageNames).flatMap(([name, label]) => {
    const status = props.model.states[name]?.status;
    return status === "error" ? [`${label}讀取失敗`] : status === "stale" ? [`${label}已過期`] : [];
  }),
);

function playerSide(stroke: StrokeModel) {
  return stroke.hitter ?? "unknown";
}
function lowConfidence(stroke: StrokeModel) {
  return stroke.type !== null && stroke.confidence !== null && stroke.confidence < LOW_CONFIDENCE;
}

/** Holds the playing shot at the middle of the list, so the list moves past it; nothing outside the window scrolls. */
watch(
  () => [props.selectedStrokeIndex, props.strokesCollapsed] as const,
  async ([index, collapsed], previous) => {
    if (index === null || collapsed) return;
    await nextTick();
    const container = scroll.value;
    const row = container?.querySelector<HTMLElement>(`[data-hit="${index}"]`)?.closest("li");
    if (!container || !row) return;
    const box = container.getBoundingClientRect();
    const rowBox = row.getBoundingClientRect();
    const offset = rowBox.top + rowBox.height / 2 - (box.top + box.height / 2);
    // Following play glides from shot to shot; landing on the list (remount, reopening) jumps straight there.
    const following = previous !== undefined && previous[1] === collapsed;
    container.scrollTo({ top: container.scrollTop + offset, behavior: following ? "smooth" : "auto" });
  },
  // A remount (switching back from the court tab) or reopening the list lands on the playing shot too.
  { flush: "post", immediate: true },
);
watch(() => props.rally.id, () => { if (scroll.value) scroll.value.scrollTop = 0; });
</script>

<template>
  <div class="rally-detail" :class="{ 'rally-detail--previous': previous }">
    <div class="detail-head">
      <ul v-if="highlightRank || rally.multi || rally.gameConflict || rally.scoreIssue || dataIssues.length" class="rally-flags" aria-label="回合標記">
        <li v-if="highlightRank" class="rally-flag rally-flag--highlight" :title="`全場 ${highlightRank.total} 個片段中排第 ${highlightRank.rank}`">精華第 {{ highlightRank.rank }} 名</li>
        <li v-if="rally.multi" class="rally-flag rally-flag--warning" :title="`比分依序為 ${rally.subScores.map(scoreText).join(' → ')}，約在 ${rally.splits.map(formatTime).join('、')} 變動`">含多個回合</li>
        <li v-if="rally.gameConflict" class="rally-flag rally-flag--warning" :title="rally.gameConflict">局數待確認</li>
        <li v-if="rally.scoreIssue" class="rally-flag rally-flag--warning" :title="rally.scoreIssue">比分待確認</li>
        <li v-if="dataIssues.length" class="rally-flag rally-flag--warning" :title="dataIssues.join('、')">資料不完整</li>
      </ul>

      <section class="commentary-block" aria-label="賽評">
        <blockquote v-if="summary" class="commentary-summary">
          <p>{{ summary.text }}</p>
          <p v-if="summaryRefs.length" class="commentary-refs">
            <span>依據</span>
            <button v-for="item in summaryRefs" :key="item.ordinal" type="button" :title="item.evidence.text" @click="emit('evidence', item.evidence)">#{{ item.ordinal }}</button>
          </p>
        </blockquote>
        <template v-else-if="rally.commentary.status !== 'available'">
          <div v-if="commentaryRequest.kind === 'ready' || commentaryRequest.kind === 'starting'" class="commentary-request">
            <p>這回合還沒有賽評。</p>
            <button type="button" class="button-primary" :disabled="commentaryRequest.kind === 'starting'" @click="emit('requestCommentary', rally)">
              {{ commentaryRequest.kind === "starting" ? "送出中…" : "產生這回合的賽評" }}
            </button>
            <small>約使用 3–4 次 Gemini 請求</small>
          </div>
          <p v-else-if="commentaryRequest.kind === 'running'" class="commentary-pending" role="status">
            <span class="loading-indicator" aria-hidden="true" />正在產生賽評，可以繼續觀看，完成後會自動顯示。
          </p>
          <p v-else-if="commentaryRequest.kind === 'publishing'" class="commentary-pending" role="status">
            <span class="loading-indicator" aria-hidden="true" />正在更新回看…
          </p>
          <div v-else-if="commentaryRequest.kind === 'failed'" class="commentary-request">
            <p class="error" role="alert">{{ commentaryRequest.error }}</p>
            <button type="button" class="button-secondary" @click="emit('requestCommentary', rally)">重試</button>
          </div>
          <p v-else-if="commentaryRequest.kind === 'blocked'" class="commentary-note">{{ commentaryRequest.reason }}</p>
          <p v-else class="commentary-note">此回合尚無賽評。</p>
        </template>
      </section>

      <button v-if="rally.hits?.length" type="button" class="hit-list-toggle" :aria-expanded="!strokesCollapsed" aria-controls="analysis-hit-list" @click="emit('strokesCollapsed', !strokesCollapsed)">
        <AppIcon :name="strokesCollapsed ? 'chevron-right' : 'chevron-down'" />
        <span>擊球序列</span>
        <small>{{ rally.hits.length }} 拍</small>
      </button>
    </div>
    <!-- Only the shots scroll; the flags, commentary and the list's fold stay put above them. -->
    <div ref="scroll" class="detail-scroll">
      <ol v-if="rally.hits?.length" v-show="!strokesCollapsed" id="analysis-hit-list" class="hit-list" aria-label="擊球序列">
        <li v-for="stroke in rally.hits" :key="stroke.eventIndex" :class="{ 'hit-item--selected': selectedStrokeIndex === stroke.eventIndex }">
          <button
            type="button"
            class="hit-row"
            :data-hit="stroke.eventIndex"
            :data-testid="`hit-${stroke.eventIndex}`"
            :data-family="strokeFamily(stroke.type)"
            :aria-pressed="selectedStrokeIndex === stroke.eventIndex"
            :title="formatPreciseTime(stroke.time)"
            @click="emit('stroke', stroke)"
          >
            <span class="hit-ordinal">{{ String(stroke.ordinal).padStart(2, "0") }}</span>
            <span class="hit-player" :data-side="playerSide(stroke)"><span class="hit-player__name">{{ playerName(stroke.player) }}</span></span>
            <strong class="hit-type" :class="{ 'hit-type--uncertain': lowConfidence(stroke) }">
              <i aria-hidden="true">{{ strokeGlyph(stroke.type) }}</i>{{ stroke.type ?? "球種未知" }}<template v-if="lowConfidence(stroke)">?</template>
            </strong>
          </button>
          <p v-if="commentByStroke.get(stroke.eventIndex)" class="hit-comment">{{ commentByStroke.get(stroke.eventIndex)!.text }}</p>
        </li>
      </ol>
      <p v-else class="empty-state">{{ hitStatus(model.states.events?.status, rally.hits?.length ?? null) }}</p>
    </div>
  </div>
</template>
