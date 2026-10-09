import { computed, ref, watch, type Ref } from "vue";
import type {
  CommentaryEventModel,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../domain/models";
import {
  rallyAtOrBefore,
  resolveActiveMatchContext,
} from "../temporal/activeContext";

export { activeRallyAt } from "../temporal/activeContext";

export type PlayerController = { seek: (timeSec: number) => void };

/** A seek that reports within this distance of its target has landed, even if it snapped outside the Rally. */
const LANDING_TOLERANCE_SEC = 0.5;

export function useReviewWorkspace<T extends PlayerController>(
  model: Ref<MatchModel | null>,
  player: Ref<T | null>,
) {
  const currentTimeSec = ref(0);
  const selectedRallyIndex = ref<number | null>(null);
  const selectedStrokeIndex = ref<number | null>(null);
  const playing = ref(false);

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

  /** A selection whose seek has not reached its Rally yet; the playhead is still elsewhere. */
  let selectionLanding = false;
  let landingTimeSec = 0;
  /** Rally that owns the playhead: the one playing, or the last one played during a gap. */
  let lastOwnerId: number | null = null;
  watch(
    model,
    () => {
      currentTimeSec.value = 0;
      selectionLanding = false;
      lastOwnerId = null;
      clearSelection();
      player.value?.seek(0);
    },
    { flush: "sync" },
  );

  const ownerRally = computed(() =>
    model.value && !model.value.layoutOnly
      ? rallyAtOrBefore(model.value.rallies, currentTimeSec.value)
      : null,
  );

  function followOwner() {
    const owner = ownerRally.value?.id ?? null;
    if (owner === null || selectedRallyIndex.value === null || owner === selectedRallyIndex.value) return;
    selectedRallyIndex.value = owner;
    selectedStrokeIndex.value = null;
  }

  // While playing, the selection follows the Rally that owns the playhead, so
  // it never lingers on a Rally already left behind, including after a seek
  // into a far gap. A paused scrub never re-targets it (that would re-zoom a
  // Rally lens mid-drag); pressing play catches up. A selection whose seek is
  // still landing is never mistaken for being left behind.
  watch(
    currentTimeSec,
    (timeSec) => {
      const owner = ownerRally.value?.id ?? null;
      const ownerChanged = owner !== lastOwnerId;
      lastOwnerId = owner;
      if (selectionLanding) {
        if (owner === selectedRallyIndex.value || Math.abs(timeSec - landingTimeSec) <= LANDING_TOLERANCE_SEC)
          selectionLanding = false;
        return;
      }
      if (ownerChanged && playing.value) followOwner();
    },
    { flush: "sync" },
  );
  watch(
    playing,
    (isPlaying) => {
      if (isPlaying && !selectionLanding) followOwner();
    },
    { flush: "sync" },
  );

  function markSelected(rallyId: number | null, timeSec = 0) {
    selectedRallyIndex.value = rallyId;
    landingTimeSec = timeSec;
    selectionLanding =
      rallyId !== null &&
      ownerRally.value?.id !== rallyId &&
      Math.abs(currentTimeSec.value - timeSec) > LANDING_TOLERANCE_SEC;
  }

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
    markSelected(rally.id, timeSec);
    selectedStrokeIndex.value = null;
    seek(timeSec);
  }

  function selectStroke(stroke: StrokeModel) {
    markSelected(
      model.value?.rallies.find((rally) =>
        rally.hits?.some((item) => item.eventIndex === stroke.eventIndex),
      )?.id ?? selectedRallyIndex.value,
      stroke.time,
    );
    selectedStrokeIndex.value = stroke.eventIndex;
    seek(stroke.time);
  }

  function selectCommentary(comment: CommentaryEventModel, rally: RallyModel) {
    const stroke = rally.hits?.find(
      (item) => item.eventIndex === comment.strokeIndex,
    );
    markSelected(rally.id, stroke?.time ?? comment.timeSec);
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
    markSelected(null);
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
    const owner = ownerRally.value;
    const ownerIndex = owner ? rallies.findIndex((rally) => rally.id === owner.id) : -1;
    const selectedIndex = rallies.findIndex((rally) => rally.id === selectedRallyIndex.value);
    // Step from the selection while it is current or its seek is still
    // landing; once the playhead has moved on, step from the playhead. In the
    // gap after Rally k, `[` returns to k and `]` goes to k + 1.
    const selectionCurrent =
      selectedIndex >= 0 && (selectionLanding || owner?.id === selectedRallyIndex.value);
    const insideOwner = owner !== null && currentTimeSec.value < owner.end;
    const target = selectionCurrent
      ? selectedIndex + direction
      : direction > 0
        ? ownerIndex + 1
        : insideOwner
          ? ownerIndex - 1
          : ownerIndex;
    const nextIndex = Math.max(0, Math.min(rallies.length - 1, target));
    selectRally(rallies[nextIndex]);
    return true;
  }

  return {
    currentTimeSec,
    playing,
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
