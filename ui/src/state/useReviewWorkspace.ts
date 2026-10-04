import { computed, ref, watch, type Ref } from "vue";
import type {
  CommentaryEventModel,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../domain/models";
import { resolveActiveMatchContext } from "../temporal/activeContext";

export { activeRallyAt } from "../temporal/activeContext";

export type PlayerController = { seek: (timeSec: number) => void };

export function useReviewWorkspace<T extends PlayerController>(
  model: Ref<MatchModel | null>,
  player: Ref<T | null>,
) {
  const currentTimeSec = ref(0);
  const selectedRallyIndex = ref<number | null>(null);
  const selectedStrokeIndex = ref<number | null>(null);

  const activeContext = computed(() =>
    resolveActiveMatchContext(model.value, currentTimeSec.value),
  );
  const activeRally = computed(() => activeContext.value.rally);
  const activeStroke = computed(() => activeContext.value.stroke);
  const activeScoreRally = computed(() => activeContext.value.scoreRally);
  const currentScore = computed(() => activeContext.value.score);
  const selectedRally = computed(
    () =>
      model.value?.rallies.find(
        (rally) => rally.id === selectedRallyIndex.value,
      ) ?? null,
  );
  const selectedStroke = computed(
    () =>
      selectedRally.value?.hits?.find(
        (stroke) => stroke.eventIndex === selectedStrokeIndex.value,
      ) ?? null,
  );

  watch(
    model,
    () => {
      currentTimeSec.value = 0;
      clearSelection();
      player.value?.seek(0);
    },
    { flush: "sync" },
  );

  function seek(timeSec: number) {
    if (!Number.isFinite(timeSec) || model.value?.layoutOnly) return;
    player.value?.seek(timeSec);
  }

  function selectRally(rally: RallyModel | number) {
    const selected =
      typeof rally === "number"
        ? model.value?.rallies.find((item) => item.id === rally)
        : rally;
    if (!selected) return;
    selectRallyAt(selected, selected.start);
  }

  function selectRallyAt(rally: RallyModel, timeSec: number) {
    selectedRallyIndex.value = rally.id;
    selectedStrokeIndex.value = null;
    seek(timeSec);
  }

  function selectStroke(stroke: StrokeModel) {
    selectedRallyIndex.value =
      model.value?.rallies.find((rally) =>
        rally.hits?.some((item) => item.eventIndex === stroke.eventIndex),
      )?.id ?? selectedRallyIndex.value;
    selectedStrokeIndex.value = stroke.eventIndex;
    seek(stroke.time);
  }

  function selectCommentary(comment: CommentaryEventModel, rally: RallyModel) {
    selectedRallyIndex.value = rally.id;
    const stroke = rally.hits?.find(
      (item) => item.eventIndex === comment.strokeIndex,
    );
    if (stroke) {
      selectedStrokeIndex.value = stroke.eventIndex;
      seek(stroke.time);
    } else {
      selectedStrokeIndex.value = null;
      seek(comment.timeSec);
    }
  }

  function updateTime(timeSec: number) {
    currentTimeSec.value = timeSec;
  }

  function clearSelection() {
    selectedRallyIndex.value = null;
    selectedStrokeIndex.value = null;
  }

  function moveStroke(direction: -1 | 1) {
    const rally = selectedRally.value ?? activeRally.value;
    const hits = rally?.hits;
    if (!rally || !hits?.length || model.value?.layoutOnly) return false;
    const selectedIndex = hits.findIndex(
      (stroke) => stroke.eventIndex === selectedStrokeIndex.value,
    );
    let next: StrokeModel | undefined;
    if (selectedIndex >= 0) {
      next = hits[selectedIndex + direction];
    } else if (direction < 0) {
      next = [...hits]
        .reverse()
        .find((stroke) => stroke.time < currentTimeSec.value);
    } else {
      next = hits.find((stroke) => stroke.time > currentTimeSec.value);
    }
    if (!next) return false;
    selectStroke(next);
    return true;
  }

  function handleKeyboard(event: KeyboardEvent) {
    if (
      event.shiftKey &&
      (event.code === "ArrowLeft" || event.code === "ArrowRight")
    ) {
      event.preventDefault();
      moveStroke(event.code === "ArrowLeft" ? -1 : 1);
      return true;
    }
    if (event.code === "Escape" && selectedRallyIndex.value !== null) {
      clearSelection();
      return true;
    }
    if (event.code !== "BracketLeft" && event.code !== "BracketRight")
      return false;
    const rallies = model.value?.rallies;
    if (!rallies?.length || model.value?.layoutOnly) return false;
    event.preventDefault();
    const direction = event.code === "BracketLeft" ? -1 : 1;
    const anchor = selectedRallyIndex.value ?? activeRally.value?.id ?? null;
    const currentIndex =
      anchor === null
        ? direction > 0
          ? -1
          : rallies.length
        : rallies.findIndex((rally) => rally.id === anchor);
    const nextIndex = Math.max(
      0,
      Math.min(rallies.length - 1, currentIndex + direction),
    );
    selectRally(rallies[nextIndex]);
    return true;
  }

  return {
    currentTimeSec,
    selectedRallyIndex,
    selectedStrokeIndex,
    activeRally,
    activeStroke,
    activeScoreRally,
    currentScore,
    activeContext,
    selectedRally,
    selectedStroke,
    seek,
    selectRally,
    selectRallyAt,
    selectStroke,
    selectCommentary,
    updateTime,
    clearSelection,
    moveStroke,
    handleKeyboard,
  };
}
