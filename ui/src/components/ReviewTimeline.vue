<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import type {
  CommentaryEventModel,
  MatchModel,
  RallyModel,
  StrokeModel,
} from "../domain/models";
import {
  formatPreciseTime,
  formatTime,
  formatTimelineAxisTime,
  playerName,
} from "../format";
import {
  edgeLabelSide,
  nearestTemporalMark,
  timeFromClientX,
} from "../interaction/temporalInteraction";
import {
  fitViewport,
  timeToPercent,
  timelineItems,
  visibleRallies,
} from "../temporal/timeline";
import { cheerCurvePaths } from "../temporal/cheerCurve";
import type { TimelineFit, TimelineViewport } from "../temporal/timeline";
import TimelineLane from "./timeline/TimelineLane.vue";
import type { TimelineMode } from "../state/workspaceLayout";
import { activeRallyAt } from "../temporal/activeContext";
import {
  playbackFollowViewport,
  viewportContainsTime,
} from "../temporal/timelineFollow";
import {
  centerTimelineViewport,
  panTimelineViewport,
  zoomTimelineViewport,
} from "../temporal/timelineNavigation";
import {
  timelineHoverPreview,
  type TimelineHoverMark,
} from "./timeline/timelinePreview";
import {
  exactScoreRally,
  scoreLaneRally,
} from "./timeline/timelineScoreInteraction";
import {
  resolveTimelineDensity,
  timelineTicks,
  type PanelDensity,
} from "../presentation/workspaceDensity";

type TrackKey =
  | "rally"
  | "score"
  | "stroke"
  | "cheer"
  | "highlight"
  | "commentary";
const trackLabels: Record<TrackKey, string> = {
  rally: "片段",
  score: "比分",
  stroke: "擊球",
  cheer: "歡呼訊號",
  highlight: "精華分數",
  commentary: "賽評",
};
const trackOrder: TrackKey[] = [
  "rally",
  "score",
  "stroke",
  "cheer",
  "highlight",
  "commentary",
];
const trackStageKeys: Record<TrackKey, string | null> = {
  rally: null,
  score: "scores",
  stroke: "events",
  cheer: "audio_signals",
  highlight: "highlights",
  commentary: "commentary",
};
const LENS_DURATION_MS = 220;
const SCORE_LABEL_MIN_GAP_PX = 132;
const SEMANTIC_LABEL_TARGET_WIDTH_PX = 132;
const INSPECTION_EDGE_PADDING_PX = 64;
const SEMANTIC_REVEAL_START_MULTIPLIER = 4;
const SEMANTIC_REVEAL_END_MULTIPLIER = 1.75;
const SEMANTIC_LABEL_EDGE_PERCENT = 8;
const STROKE_LANE_BASE_HEIGHT_PX = 38;
const STROKE_TRACK_BASE_HEIGHT_PX = 28;
const STROKE_DETAIL_HEIGHT_PX = 54;
const SIGNAL_LANE_HEIGHT_PX = 72;
const SIGNAL_LANE_MARGIN_PX = 8;
const props = withDefaults(
  defineProps<{
    model: MatchModel;
    selectedId: number | null;
    selectedStrokeIndex: number | null;
    time: number;
    activeId: number | null;
    activeStrokeIndex?: number | null;
    scoreContextId?: number | null;
    showHeader?: boolean;
    timelineMode?: TimelineMode | "all";
    compactRail?: boolean;
  }>(),
  {
    timelineMode: "all",
    activeStrokeIndex: null,
    scoreContextId: null,
    showHeader: true,
    compactRail: false,
  },
);
const emit = defineEmits<{
  rally: [rally: RallyModel];
  stroke: [stroke: StrokeModel];
  commentary: [comment: CommentaryEventModel, rally: RallyModel];
  rallyAt: [rally: RallyModel, timeSec: number];
  seek: [timeSec: number];
  inspect: [timeSec: number | null];
  expand: [];
}>();
const filters = reactive<Record<TrackKey, boolean>>({
  rally: true,
  score: true,
  stroke: true,
  cheer: true,
  highlight: true,
  commentary: true,
});
const density = ref<PanelDensity>(props.compactRail ? "rail" : "full");
const modeShows = (track: TrackKey) =>
  props.compactRail
    ? track === "rally"
    : props.timelineMode === "all" || props.timelineMode === track;
const fit = ref<TimelineFit | "custom">("match");
const renderViewport = ref<TimelineViewport>(
  fitViewport("match", props.model.duration, null),
);
const lensProgress = ref(0);
const lensActive = ref(false);
const manualNavigation = ref(false);
const surface = ref<HTMLElement | null>(null);
let scrubGesture: { pointerId: number; startX: number; track: HTMLElement; moved: boolean } | null = null;
let suppressScrubClick = false;
const root = ref<HTMLElement | null>(null);
const trackWidth = ref(640);
const inspectionTime = ref<number | null>(null);
const inspectionX = ref(0);
const inspectionTrackX = ref(0);
const inspectionTrackWidth = ref(1);
const inspectionClientX = ref(0);
const inspectionClientY = ref(0);
const hoveredMark = ref<TimelineHoverMark | null>(null);
const hoverPreview = computed(() =>
  timelineHoverPreview(hoveredMark.value, props.model.rallies, props.model.cheerTimeline),
);
const hoverTooltipSide = computed(() =>
  typeof window === "undefined"
    ? "center"
    : edgeLabelSide(inspectionClientX.value, window.innerWidth, 150),
);
const zoomLevel = computed(() =>
  Math.max(1, props.model.duration / renderViewport.value.durationSec),
);
const inspectionLabelSide = computed(() => {
  return edgeLabelSide(
    inspectionTrackX.value,
    inspectionTrackWidth.value,
    INSPECTION_EDGE_PADDING_PX,
  );
});
let observer: ResizeObserver | undefined;
let reducedMotionQuery: MediaQueryList | undefined;
let lensFrame: number | undefined;
let inspectionFrame: number | undefined;
let lensRequest = 0;
let lastPointer: {
  clientX: number;
  clientY: number;
  track: HTMLElement;
  exactMark: TimelineHoverMark | null;
} | null = null;
function measureTrackWidth() {
  const track = surface.value?.querySelector<HTMLElement>(
    ".timeline-lane-track",
  );
  if (track)
    trackWidth.value = Math.max(1, track.getBoundingClientRect().width);
}
function measureDensity() {
  const element = root.value;
  if (!element) return;
  density.value = resolveTimelineDensity(
    element.clientWidth,
    element.clientHeight,
    props.compactRail,
  );
}
onMounted(() => {
  observer = new ResizeObserver(() => {
    measureDensity();
    measureTrackWidth();
    scheduleInspectionRestore();
  });
  if (root.value) observer.observe(root.value);
  measureDensity();
  measureTrackWidth();
  reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  reducedMotionQuery.addEventListener("change", handleReducedMotionChange);
});
onBeforeUnmount(() => {
  observer?.disconnect();
  reducedMotionQuery?.removeEventListener("change", handleReducedMotionChange);
  cancelLens();
  if (inspectionFrame !== undefined) cancelAnimationFrame(inspectionFrame);
});
const selectedRally = computed(
  () =>
    props.model.rallies.find((rally) => rally.id === props.selectedId) ?? null,
);
watch([() => props.selectedId, () => props.model.duration], (id) => {
  if (id[0] === null && fit.value === "rally") {
    setFit("match", false);
    return;
  }
  if (fit.value === "custom") {
    scheduleInspectionRestore();
    return;
  }
  manualNavigation.value = false;
  cancelLens();
  renderViewport.value = authoritativeViewport(fit.value);
  lensProgress.value = fit.value === "rally" ? 1 : 0;
  scheduleInspectionRestore();
});
watch(
  () => props.time,
  (timeSec) => {
    if (lensActive.value) return;
    const next = playbackFollowViewport(
      manualNavigation.value,
      timeSec,
      renderViewport.value,
      props.model.duration,
      fit.value === "rally"
        ? activeRallyAt(props.model.rallies, timeSec)
        : null,
    );
    if (next !== renderViewport.value) renderViewport.value = next;
  },
  { flush: "sync" },
);
watch(
  () => props.model.scenario,
  () => {
    cancelLens();
    fit.value = "match";
    manualNavigation.value = false;
    renderViewport.value = fitViewport("match", props.model.duration, null);
    lensProgress.value = 0;
    clearInspection();
  },
);
watch(filters, () => void nextTick(measureTrackWidth), { deep: true });
watch(
  () => props.compactRail,
  () => void nextTick(() => {
    measureDensity();
    measureTrackWidth();
  }),
);
watch(
  () => props.model,
  () => void nextTick(measureTrackWidth),
  { flush: "post" },
);
const rallies = computed(() =>
  visibleRallies(props.model.rallies, renderViewport.value),
);
const layout = computed(() =>
  timelineItems(rallies.value, renderViewport.value),
);
const axisTicks = computed(() =>
  timelineTicks(
    renderViewport.value.startSec,
    renderViewport.value.endSec,
    trackWidth.value,
    density.value,
  ),
);
const strokes = computed(() =>
  rallies.value.flatMap((rally) => rally.hits ?? []),
);
const commentaryEvents = computed(() =>
  rallies.value.flatMap((rally) =>
    rally.commentary.events.map((comment) => ({
      comment,
      rally,
      time: comment.timeSec,
    })),
  ),
);
const scoreRallies = computed(() =>
  rallies.value.filter((rally) => rally.score !== null),
);
const scoreLabelIds = computed(() => {
  if (fit.value === "rally" && lensProgress.value >= 0.98) {
    return new Set(scoreRallies.value.map((rally) => rally.id));
  }

  const candidates = scoreRallies.value.map((rally) => ({
    id: rally.id,
    x: (position(rally.end) / 100) * trackWidth.value,
  }));
  const chosen: typeof candidates = [];
  const fits = (candidate: (typeof candidates)[number]) =>
    chosen.every(
      (item) => Math.abs(item.x - candidate.x) >= SCORE_LABEL_MIN_GAP_PX,
    );
  const add = (candidate?: (typeof candidates)[number]) => {
    if (candidate && !chosen.some((item) => item.id === candidate.id)) {
      if (chosen.length === 0 || fits(candidate)) chosen.push(candidate);
    }
  };

  add(candidates.find((candidate) => candidate.id === props.selectedId));
  add(candidates.find((candidate) => candidate.id === props.activeId));
  for (const candidate of candidates) add(candidate);
  add(candidates.at(-1));

  return new Set(chosen.map((candidate) => candidate.id));
});
const strokeDetailStride = computed(() => {
  if (!showSemanticStrokes.value) return Number.POSITIVE_INFINITY;
  const availableLabels = Math.max(
    1,
    Math.floor(trackWidth.value / SEMANTIC_LABEL_TARGET_WIDTH_PX),
  );
  return Math.max(1, Math.ceil(strokes.value.length / availableLabels));
});
const semanticRevealProgress = computed(() => {
  if (!selectedRally.value) return 0;
  const revealStart =
    selectedRally.value.duration * SEMANTIC_REVEAL_START_MULTIPLIER;
  const revealEnd =
    selectedRally.value.duration * SEMANTIC_REVEAL_END_MULTIPLIER;
  return Math.min(
    1,
    Math.max(
      0,
      (revealStart - renderViewport.value.durationSec) /
        (revealStart - revealEnd),
    ),
  );
});
const showSemanticStrokes = computed(() => semanticRevealProgress.value > 0);
const showOverviewSignals = computed(
  () => fit.value === "match" || lensActive.value || fit.value === "custom",
);
const showCheerCurveLane = computed(() =>
  filters.cheer && modeShows("cheer") && capability("cheer"),
);
const cheerPaths = computed(() =>
  cheerCurvePaths(props.model.cheerTimeline ?? [], renderViewport.value),
);
const detailOpacity = computed(() => semanticRevealProgress.value);
const position = (timeSec: number) =>
  timeToPercent(timeSec, renderViewport.value);
const playheadFraction = computed(() =>
  Math.min(1, Math.max(0, position(props.time) / 100)),
);
const playheadVisible = computed(() =>
  viewportContainsTime(renderViewport.value, props.time),
);
const capability = (track: TrackKey) =>
  ({
    rally: true,
    score: props.model.capabilities.score,
    stroke: props.model.capabilities.stroke,
    cheer: props.model.capabilities.cheer,
    highlight: props.model.capabilities.highlight,
    commentary: props.model.capabilities.commentary,
  })[track];
function fitOnlyUnavailable(track: TrackKey) {
  return fit.value === "rally" && track === "highlight";
}
function filterDisabled(track: TrackKey) {
  return !capability(track) || fitOnlyUnavailable(track);
}
function filterStatus(track: TrackKey) {
  if (!capability(track)) {
    const stage = trackStageKeys[track];
    const statuses = [stage ? props.model.states[stage]?.status : undefined,
      track === "commentary" ? props.model.states.commentary_segments?.status : undefined];
    if (statuses.includes("stale")) return "資料已過期";
    return statuses.includes("error") ? "讀取失敗" : "未提供";
  }
  return fitOnlyUnavailable(track) ? "僅全場" : null;
}
function authoritativeViewport(next: TimelineFit) {
  return fitViewport(next, props.model.duration, selectedRally.value);
}
function cancelLens() {
  lensRequest += 1;
  if (lensFrame !== undefined) cancelAnimationFrame(lensFrame);
  lensFrame = undefined;
  lensActive.value = false;
}
function cubicBezierCoordinate(t: number, p1: number, p2: number) {
  const inverse = 1 - t;
  return 3 * inverse * inverse * t * p1 + 3 * inverse * t * t * p2 + t ** 3;
}
function cubicBezierSlope(t: number, p1: number, p2: number) {
  const inverse = 1 - t;
  return (
    3 * inverse * inverse * p1 +
    6 * inverse * t * (p2 - p1) +
    3 * t * t * (1 - p2)
  );
}
function temporalLensEase(progress: number) {
  const clamped = Math.min(1, Math.max(0, progress));
  let parameter = clamped;
  for (let iteration = 0; iteration < 5; iteration += 1) {
    const slope = cubicBezierSlope(parameter, 0.2, 0);
    if (Math.abs(slope) < 0.0001) break;
    parameter -= (cubicBezierCoordinate(parameter, 0.2, 0) - clamped) / slope;
    parameter = Math.min(1, Math.max(0, parameter));
  }
  return cubicBezierCoordinate(parameter, 0, 1);
}
function interpolateViewport(
  from: TimelineViewport,
  to: TimelineViewport,
  progress: number,
): TimelineViewport {
  const startSec = from.startSec + (to.startSec - from.startSec) * progress;
  const endSec = from.endSec + (to.endSec - from.endSec) * progress;
  return {
    startSec,
    endSec,
    durationSec: Math.max(0.001, endSec - startSec),
  };
}
function applyLensEndpoint(target: TimelineViewport, targetProgress: number) {
  renderViewport.value = target;
  lensProgress.value = targetProgress;
  lensFrame = undefined;
  lensActive.value = false;
  requestAnimationFrame(restoreInspection);
}
function handleReducedMotionChange(event: MediaQueryListEvent) {
  if (!event.matches) return;
  cancelLens();
  if (fit.value === "custom") return;
  applyLensEndpoint(
    authoritativeViewport(fit.value),
    fit.value === "rally" ? 1 : 0,
  );
}
function setFit(next: TimelineFit, manual = true) {
  const anchor = selectedRally.value ?? activeRallyAt(props.model.rallies, props.time);
  if (next === "rally" && !anchor) return;
  if (next === fit.value) return;
  const from = { ...renderViewport.value };
  const fromProgress = lensProgress.value;
  fit.value = next;
  manualNavigation.value = manual && next === "rally";
  const target = fitViewport(next, props.model.duration, anchor);
  const targetProgress = next === "rally" ? 1 : 0;
  clearInspection(false);
  cancelLens();
  if (reducedMotionQuery?.matches) {
    applyLensEndpoint(target, targetProgress);
    return;
  }

  const request = ++lensRequest;
  const startedAt = performance.now();
  lensActive.value = true;
  const step = (now: number) => {
    if (request !== lensRequest) return;
    const elapsed = Math.min(1, (now - startedAt) / LENS_DURATION_MS);
    const eased = temporalLensEase(elapsed);
    renderViewport.value = interpolateViewport(from, target, eased);
    lensProgress.value = fromProgress + (targetProgress - fromProgress) * eased;
    if (elapsed < 1) {
      lensFrame = requestAnimationFrame(step);
      return;
    }
    applyLensEndpoint(target, targetProgress);
  };
  lensFrame = requestAnimationFrame(step);
}
function returnToPlayback() {
  manualNavigation.value = false;
  if (fit.value === "custom") {
    renderViewport.value = centerTimelineViewport(
      renderViewport.value,
      props.time,
      props.model.duration,
    );
  } else {
    const active = activeRallyAt(props.model.rallies, props.time);
    const forcedOutside = {
      ...renderViewport.value,
      startSec: props.time + 1,
      endSec: props.time + 1,
    };
    renderViewport.value = playbackFollowViewport(
      false,
      props.time,
      forcedOutside,
      props.model.duration,
      active,
    );
  }
}
function panTimeline(event: WheelEvent) {
  if (event.shiftKey || lensActive.value) return;
  event.preventDefault();
  cancelLens();
  manualNavigation.value = true;
  fit.value = "custom";
  const track = surface.value?.querySelector<HTMLElement>(".timeline-lane-track");
  const width = Math.max(1, track?.getBoundingClientRect().width ?? trackWidth.value);
  if (event.ctrlKey || event.metaKey) {
    const rect = track?.getBoundingClientRect() ?? surface.value?.getBoundingClientRect();
    if (!rect) return;
    const anchor = timeFromClientX(event.clientX, rect.left, rect.width, renderViewport.value);
    const scale = Math.exp(event.deltaY * 0.0015);
    renderViewport.value = zoomTimelineViewport(
      renderViewport.value,
      scale,
      anchor,
      props.model.duration,
    );
  } else {
    const pixelDelta = event.deltaX || event.deltaY;
    const deltaSec = (pixelDelta / width) * renderViewport.value.durationSec;
    renderViewport.value = panTimelineViewport(
      renderViewport.value,
      deltaSec,
      props.model.duration,
    );
  }
  scheduleInspectionRestore();
}
function quickFit(event: MouseEvent) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (target.closest(".timeline-lane-track,button,input,select,summary,a")) return;
  const active = activeRallyAt(props.model.rallies, props.time);
  if (active) {
    cancelLens();
    fit.value = "rally";
    manualNavigation.value = true;
    renderViewport.value = fitViewport("rally", props.model.duration, active);
    lensProgress.value = 1;
    scheduleInspectionRestore();
  } else {
    setFit("match", false);
  }
}
function clearInspection(forgetPointer = true) {
  if (forgetPointer) lastPointer = null;
  if (inspectionFrame !== undefined) cancelAnimationFrame(inspectionFrame);
  inspectionFrame = undefined;
  inspectionTime.value = null;
  hoveredMark.value = null;
  emit("inspect", null);
}
function resolveHoveredMark(
  kind: TrackKey | undefined,
  timeSec: number,
  width: number,
) {
  if (kind === "rally") {
    const rally = activeRallyAt(props.model.rallies, timeSec);
    return rally ? { kind, id: rally.id } : null;
  }
  if (kind === "score") {
    const rally = nearestTemporalMark(
      scoreRallies.value,
      timeSec,
      (item) => item.end,
      renderViewport.value,
      width,
    );
    return rally ? { kind, id: rally.id } : null;
  }
  if (kind === "stroke") {
    const stroke = nearestTemporalMark(
      strokes.value,
      timeSec,
      (item) => item.time,
      renderViewport.value,
      width,
    );
    return stroke ? { kind, id: stroke.eventIndex } : null;
  }
  if (kind === "cheer") {
    const windows = props.model.cheerTimeline ?? [];
    let nearestIndex = -1;
    let distance = Number.POSITIVE_INFINITY;
    windows.forEach((window, index) => {
      if (timeSec < window.start || timeSec > window.end) return;
      const next = Math.abs(window.time - timeSec);
      if (next < distance) { nearestIndex = index; distance = next; }
    });
    return nearestIndex < 0 ? null : { kind: "cheer-window" as const, id: nearestIndex };
  }
  if (kind === "highlight") {
    const candidates = rallies.value.filter((item) => item.highlight !== null);
    const rally = nearestTemporalMark(
      candidates,
      timeSec,
      (item) => (item.start + item.end) / 2,
      renderViewport.value,
      width,
    );
    return rally ? { kind, id: rally.id } : null;
  }
  if (kind === "commentary") {
    const item = nearestTemporalMark(
      commentaryEvents.value,
      timeSec,
      (candidate) => candidate.time,
      renderViewport.value,
      width,
    );
    return item
      ? { kind, id: `${item.rally.id}:${item.comment.strokeIndex}` }
      : null;
  }
  return null;
}
function exactMarkFromTarget(target: Element): TimelineHoverMark | null {
  const marker = target.closest<HTMLElement>(
    "[data-timeline-kind][data-timeline-id]",
  );
  if (!marker) return null;
  const kind = marker.dataset.timelineKind;
  const id = marker.dataset.timelineId;
  if (kind === "score" && id !== undefined) {
    const rally = exactScoreRally(scoreRallies.value, Number(id));
    return rally ? { kind, id: rally.id } : null;
  }
  return null;
}
function updateInspection(
  clientX: number,
  track: HTMLElement,
  exactMark: TimelineHoverMark | null = null,
) {
  if (lensActive.value || !surface.value) {
    clearInspection(false);
    return;
  }
  const trackRect = track.getBoundingClientRect();
  const surfaceRect = surface.value.getBoundingClientRect();
  const timeSec = timeFromClientX(
    clientX,
    trackRect.left,
    trackRect.width,
    renderViewport.value,
  );
  inspectionTime.value = timeSec;
  inspectionX.value = Math.min(
    surfaceRect.width,
    Math.max(0, clientX - surfaceRect.left),
  );
  inspectionTrackWidth.value = Math.max(1, trackRect.width);
  inspectionTrackX.value = Math.min(
    inspectionTrackWidth.value,
    Math.max(0, clientX - trackRect.left),
  );
  inspectionClientX.value = clientX;
  inspectionClientY.value = trackRect.top;
  const lane = track.closest<HTMLElement>(".timeline-lane");
  hoveredMark.value =
    exactMark ??
    resolveHoveredMark(
      lane?.dataset.kind as TrackKey | undefined,
      timeSec,
      trackRect.width,
    );
  emit("inspect", timeSec);
}
function restoreInspection() {
  if (lastPointer?.track.isConnected) {
    const target = document.elementFromPoint(
      lastPointer.clientX,
      lastPointer.clientY,
    );
    updateInspection(
      lastPointer.clientX,
      lastPointer.track,
      target instanceof Element ? exactMarkFromTarget(target) : null,
    );
  } else if (inspectionTime.value !== null) {
    clearInspection(false);
  }
}
function scheduleInspectionRestore() {
  if (!lastPointer?.track.isConnected || inspectionFrame !== undefined) return;
  inspectionFrame = requestAnimationFrame(() => {
    inspectionFrame = undefined;
    restoreInspection();
  });
}
function inspectPointer(event: PointerEvent) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const track = target.closest<HTMLElement>(".timeline-lane-track");
  if (!track) {
    clearInspection();
    return;
  }
  lastPointer = {
    clientX: event.clientX,
    clientY: event.clientY,
    track,
    exactMark: exactMarkFromTarget(target),
  };
  if (inspectionFrame !== undefined) return;
  inspectionFrame = requestAnimationFrame(() => {
    inspectionFrame = undefined;
    if (lastPointer)
      updateInspection(
        lastPointer.clientX,
        lastPointer.track,
        lastPointer.exactMark,
      );
  });
}
function clickTimeline(event: MouseEvent) {
  if (suppressScrubClick) { suppressScrubClick = false; return; }
  if (lensActive.value) return;
  if (props.compactRail) {
    emit("expand");
    return;
  }
  const target = event.target;
  if (!(target instanceof Element)) return;
  if (target.closest("button,input,select,summary,a")) return;
  const track = target.closest<HTMLElement>(".timeline-lane-track");
  const lane = track?.closest<HTMLElement>(".timeline-lane");
  if (!track || !lane) return;
  const timeSec = timeFromClientX(
    event.clientX,
    track.getBoundingClientRect().left,
    track.getBoundingClientRect().width,
    renderViewport.value,
  );
  const kind = lane.dataset.kind as TrackKey | undefined;
  if (kind === "rally") {
    const rally = activeRallyAt(props.model.rallies, timeSec);
    if (rally) {
      emit(
        "rallyAt",
        rally,
        Math.min(rally.end, Math.max(rally.start, timeSec)),
      );
    } else emit("seek", timeSec);
    return;
  }
  if (kind === "score") {
    const rally = scoreLaneRally(
      scoreRallies.value,
      null,
      timeSec,
      renderViewport.value,
      track.clientWidth,
    );
    if (rally) emit("rally", rally);
    else emit("seek", timeSec);
    return;
  }
  if (kind === "stroke") {
    const eventIndex = target.closest<HTMLElement>(
      ".stroke-tick,.stroke-marker",
    )?.dataset.eventIndex;
    const stroke =
      eventIndex === undefined
        ? nearestTemporalMark(
            strokes.value,
            timeSec,
            (item) => item.time,
            renderViewport.value,
            track.clientWidth,
          )
        : strokes.value.find((item) => item.eventIndex === Number(eventIndex));
    if (stroke) emit("stroke", stroke);
    else emit("seek", timeSec);
    return;
  }
  if (kind === "cheer") {
    emit("seek", timeSec);
    return;
  }
  if (kind === "highlight") {
    const candidates = rallies.value.filter((item) => item.highlight !== null);
    const markerId =
      target.closest<HTMLElement>(".signal-block")?.dataset.rallyId;
    const rally =
      markerId === undefined
        ? nearestTemporalMark(
            candidates,
            timeSec,
            (item) => (item.start + item.end) / 2,
            renderViewport.value,
            track.clientWidth,
          )
        : candidates.find((item) => item.id === Number(markerId));
    if (rally) {
      emit("rally", rally);
    } else emit("seek", timeSec);
    return;
  }
  emit("seek", timeSec);
}
function scrubTime(clientX: number, track: HTMLElement) {
  const bounds = track.getBoundingClientRect();
  emit("seek", timeFromClientX(clientX, bounds.left, bounds.width, renderViewport.value));
}
function beginScrub(event: PointerEvent) {
  if (event.button !== 0 || props.compactRail || lensActive.value || !(event.target instanceof Element)) return;
  if (event.target.closest("button,input,select,summary,a")) return;
  const track = event.target.closest<HTMLElement>(".timeline-lane-track");
  if (!track || !surface.value) return;
  suppressScrubClick = false;
  scrubGesture = { pointerId: event.pointerId, startX: event.clientX, track, moved: false };
  surface.value.setPointerCapture(event.pointerId);
  scrubTime(event.clientX, track);
}
function moveScrub(event: PointerEvent) {
  if (!scrubGesture || event.pointerId !== scrubGesture.pointerId) return;
  if (Math.abs(event.clientX - scrubGesture.startX) > 3) scrubGesture.moved = true;
  scrubTime(event.clientX, scrubGesture.track);
}
function endScrub(event: PointerEvent) {
  if (!scrubGesture || event.pointerId !== scrubGesture.pointerId) return;
  suppressScrubClick = scrubGesture.moved;
  scrubGesture = null;
  if (surface.value?.hasPointerCapture(event.pointerId)) surface.value.releasePointerCapture(event.pointerId);
}
function selectScoreMarker(rallyId: number) {
  const rally = exactScoreRally(scoreRallies.value, rallyId);
  if (rally) emit("rally", rally);
}
function showScoreLabel(id: number) {
  return scoreLabelIds.value.has(id);
}
function showStrokeDetail(index: number) {
  return index % strokeDetailStride.value === 0;
}
function strokeDetailSide(timeSec: number) {
  const percent = position(timeSec);
  return percent < SEMANTIC_LABEL_EDGE_PERCENT
    ? "start"
    : percent > 100 - SEMANTIC_LABEL_EDGE_PERCENT
      ? "end"
      : "center";
}
function scoreLabelSide(rally: RallyModel) {
  const anchorX = (position(rally.end) / 100) * trackWidth.value;
  const scoreText = rally.score ? `${rally.score[0]}${rally.score[1]}` : "";
  const estimatedLabelWidth = Math.max(30, scoreText.length * 7 + 8);
  const requiredInset = estimatedLabelWidth / 2 + 4;
  if (anchorX < requiredInset) return "start";
  if (trackWidth.value - anchorX < requiredInset) return "end";
  return "center";
}
const timelineStyle = computed(() => ({
  "--lens-detail-progress": lensProgress.value,
  "--lens-detail-opacity": detailOpacity.value,
  "--lens-overview-opacity": showCheerCurveLane.value ? 1 : 1 - lensProgress.value,
  "--lens-stroke-height":
    STROKE_LANE_BASE_HEIGHT_PX +
    STROKE_DETAIL_HEIGHT_PX * lensProgress.value +
    "px",
  "--lens-stroke-track-height":
    STROKE_TRACK_BASE_HEIGHT_PX +
    STROKE_DETAIL_HEIGHT_PX * lensProgress.value +
    "px",
  "--lens-signal-height":
    SIGNAL_LANE_HEIGHT_PX * (showCheerCurveLane.value ? 1 : 1 - lensProgress.value) + "px",
  "--lens-signal-margin":
    SIGNAL_LANE_MARGIN_PX * (showCheerCurveLane.value ? 1 : 1 - lensProgress.value) + "px",
}));
</script>

<template>
  <section
    ref="root"
    class="intelligence-timeline"
    aria-label="比賽時間軸"
    :data-fit="fit"
    :data-lens-active="lensActive"
    :data-lens-progress="lensProgress.toFixed(4)"
    :data-viewport-start="renderViewport.startSec"
    :data-viewport-end="renderViewport.endSec"
    :data-zoom-level="zoomLevel.toFixed(3)"
    :data-compact-rail="compactRail"
    :data-density="density"
    :aria-busy="lensActive"
    :style="timelineStyle"
  >
    <header v-if="!compactRail" class="timeline-header" :class="{ 'timeline-header--compact': !showHeader }">
      <div v-if="showHeader">
        <h2>{{ fit === "rally" ? "片段時間軸" : "比賽時間軸" }}</h2>
      </div>
      <span
        >{{ rallies.length }} 段 ·
        {{ formatTime(renderViewport.durationSec) }}<template v-if="zoomLevel > 1.05"> · {{ zoomLevel.toFixed(1) }}×</template></span
      >
      <div class="timeline-controls">
        <div class="fit-controls">
          <button
            v-if="density === 'full'"
            type="button"
            :aria-pressed="fit === 'match'"
            @click="setFit('match', true)"
          >
            全場
          </button>
          <button
            type="button"
            :aria-pressed="fit === 'rally'"
            :disabled="!selectedRally"
            :title="selectedRally ? '聚焦目前選取片段' : '請先選取片段'"
            @click="setFit('rally', true)"
          >
            片段
          </button>
        </div>
        <details v-if="timelineMode === 'all' && density === 'full'">
          <summary>顯示軌道</summary>
          <div class="track-filters">
            <label v-for="track in trackOrder" :key="track"
              ><input
                v-model="filters[track]"
                type="checkbox"
                :disabled="filterDisabled(track)"
              />{{ trackLabels[track]
              }}<span v-if="filterStatus(track)"
                >（{{ filterStatus(track) }}）</span
              ></label
            >
          </div>
        </details>
        <button v-if="manualNavigation || !playheadVisible" type="button" class="return-to-live" @click="returnToPlayback">回到目前</button>
      </div>
    </header>
    <div
      ref="surface"
      class="timeline-surface"
      :style="{ '--playhead-fraction': playheadFraction }"
      @pointerdown="beginScrub"
      @pointermove="inspectPointer($event); moveScrub($event)"
      @pointerup="endScrub"
      @pointercancel="endScrub"
      @pointerleave="clearInspection()"
      @click="clickTimeline"
      @dblclick="quickFit"
      @wheel="panTimeline"
    >
      <button v-if="compactRail" type="button" class="timeline-rail-expand" @click.stop="emit('expand')">展開時間軸</button>
      <section v-if="modeShows('rally') || modeShows('score')" class="timeline-band timeline-band--match" aria-label="比賽">
        <header class="timeline-band-label">
          <strong>比賽</strong>
        </header>
        <div class="timeline-band-tracks">
          <TimelineLane
            v-if="filters.rally && modeShows('rally')"
            kind="rally"
            label="片段"
            description="分析片段軌道"
          >
            <template v-for="item in layout.items" :key="item.id">
              <span
                class="rally-block"
                :class="{
                  selected: selectedId === item.id,
                  active: activeId === item.id,
                  hovered:
                    hoveredMark?.kind === 'rally' && hoveredMark.id === item.id,
                }"
                :style="{ left: item.left + '%', width: item.width + '%' }"
                :data-rally-id="item.id"
                aria-hidden="true"
              >
                <span
                  v-if="
                    fit === 'rally' && selectedId === item.id && !lensActive
                  "
                  class="rally-block-locator"
                  aria-hidden="true"
                >
                  <span class="rally-registration-index rally-block-index">{{
                    String(item.id + 1).padStart(3, "0")
                  }}</span>
                </span>
              </span>
            </template>
          </TimelineLane>
          <TimelineLane
            v-if="filters.score && modeShows('score') && capability('score')"
            kind="score"
            label="比分"
            description="片段比分觀察軌道"
          >
            <span
              v-for="rally in scoreRallies"
              :key="`score-${rally.id}`"
              class="score-marker"
              :class="{
                selected: selectedId === rally.id,
                  active: scoreContextId === rally.id,
                hovered:
                  hoveredMark?.kind === 'score' && hoveredMark.id === rally.id,
              }"
              :style="{ left: position(rally.end) + '%' }"
              :data-rally-id="rally.id"
            >
              <button
                v-if="showScoreLabel(rally.id) && rally.score"
                type="button"
                class="score-marker__target"
                data-timeline-kind="score"
                :data-timeline-id="rally.id"
                :aria-label="`片段 ${String(rally.id + 1).padStart(3, '0')}，比分觀察 ${rally.score[0]}:${rally.score[1]}`"
                @click.stop="selectScoreMarker(rally.id)"
              >
                <span
                  class="score-state timeline-score-state"
                  :data-side="scoreLabelSide(rally)"
                  aria-hidden="true"
                >
                  <span>{{ rally.score[0] }}</span>
                  <span class="score-state-divider" />
                  <span>{{ rally.score[1] }}</span>
                </span>
              </button>
            </span>
          </TimelineLane>
        </div>
      </section>

      <section v-if="modeShows('stroke') || modeShows('commentary')" class="timeline-band timeline-band--stroke" aria-label="擊球">
        <header class="timeline-band-label">
          <strong>擊球</strong>
        </header>
        <div class="timeline-band-tracks">
          <TimelineLane
            v-if="filters.stroke && modeShows('stroke') && capability('stroke')"
            kind="stroke"
            description="擊球事件軌道"
          >
            <template
              v-for="(stroke, index) in strokes"
              :key="stroke.eventIndex"
            >
              <span
                v-if="!showSemanticStrokes"
                class="stroke-tick"
                :data-event-index="stroke.eventIndex"
                :class="{
                  selected: selectedStrokeIndex === stroke.eventIndex,
                  active: activeStrokeIndex === stroke.eventIndex,
                  hovered:
                    hoveredMark?.kind === 'stroke' &&
                    hoveredMark.id === stroke.eventIndex,
                }"
                :style="{ left: position(stroke.time) + '%' }"
              />
              <button
                v-else
                class="stroke-marker stroke-marker--semantic"
                :data-event-index="stroke.eventIndex"
                :class="{
                  selected: selectedStrokeIndex === stroke.eventIndex,
                  active: activeStrokeIndex === stroke.eventIndex,
                  hovered:
                    hoveredMark?.kind === 'stroke' &&
                    hoveredMark.id === stroke.eventIndex,
                }"
                :style="{ left: position(stroke.time) + '%' }"
                :aria-label="`第 ${stroke.ordinal} 拍，${playerName(stroke.player)}，${stroke.type ?? '球種未提供'}`"
                @click.stop="emit('stroke', stroke)"
              >
                <span class="stroke-marker-index">{{ stroke.ordinal }}</span>
                <span
                  v-if="showStrokeDetail(index)"
                  class="stroke-marker-detail"
                  :data-side="strokeDetailSide(stroke.time)"
                >
                  <strong>{{ stroke.type ?? "球種未提供" }}</strong>
                  <small
                    >{{ playerName(stroke.player) }} ·
                    {{ formatPreciseTime(stroke.time) }}</small
                  >
                </span>
              </button>
            </template>
          </TimelineLane>
          <TimelineLane
            v-if="filters.commentary && modeShows('commentary') && capability('commentary')"
            kind="commentary"
            label="賽評"
            description="賽評事件軌道"
          >
            <button
              v-for="item in commentaryEvents"
              :key="`${item.rally.id}-${item.comment.strokeIndex}`"
              class="commentary-marker"
              :class="{
                active: activeId === item.rally.id && activeStrokeIndex === item.comment.strokeIndex,
                hovered: hoveredMark?.kind === 'commentary' && hoveredMark.id === `${item.rally.id}:${item.comment.strokeIndex}`,
              }"
              :style="{ left: position(item.time) + '%' }"
              :aria-label="`賽評事件，${formatPreciseTime(item.comment.timeSec)}`"
              @click.stop="emit('commentary', item.comment, item.rally)"
            >
              <span class="commentary-dot" aria-hidden="true" />
            </button>
          </TimelineLane>
        </div>
      </section>

      <section
        v-if="
          showCheerCurveLane ||
          (showOverviewSignals && filters.highlight && modeShows('highlight') && capability('highlight'))
        "
        class="timeline-band timeline-band--signals"
        aria-label="訊號"
      >
        <header class="timeline-band-label">
          <strong>訊號</strong>
        </header>
        <div class="timeline-band-tracks">
          <TimelineLane
            v-if="showCheerCurveLane"
            kind="cheer"
            label="歡呼"
            description="歡呼機率時間曲線"
          >
            <svg v-if="cheerPaths.length" class="cheer-curve" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <path v-for="(path, index) in cheerPaths" :key="`${path.segmentIndex}-${index}`" :d="path.d" />
            </svg>
            <span v-else class="cheer-curve-empty">無窗口歡呼資料</span>
          </TimelineLane>
          <TimelineLane
            v-if="showOverviewSignals && filters.highlight && modeShows('highlight') && capability('highlight')"
            kind="highlight"
            label="精華"
            description="片段精華屬性軌道"
          >
            <span
              v-for="rally in rallies.filter((item) => item.highlight !== null)"
              :key="`highlight-${rally.id}`"
              class="signal-block highlight-block"
              :class="{
                selected: selectedId === rally.id,
                active: activeId === rally.id,
                hovered:
                  hoveredMark?.kind === 'highlight' &&
                  hoveredMark.id === rally.id,
              }"
              :data-rally-id="rally.id"
              :style="{
                left: position((rally.start + rally.end) / 2) + '%',
                '--signal-opacity': 0.22 + (rally.highlight ?? 0) * 0.78,
              }"
              aria-hidden="true"
            />
          </TimelineLane>
        </div>
      </section>
      <span
        v-if="!model.layoutOnly"
        class="timeline-playhead"
        aria-hidden="true"
      />
      <div
        v-if="inspectionTime !== null && !lensActive"
        class="timeline-inspection"
        :style="{ left: inspectionX + 'px' }"
        aria-hidden="true"
      >
        <span :data-side="inspectionLabelSide">{{
          formatPreciseTime(inspectionTime)
        }}</span>
      </div>
      <div v-if="!compactRail" class="time-axis">
        <span v-for="tick in axisTicks" :key="tick.timeSec" :data-edge="tick.edge" :style="{ left: tick.percent + '%' }">{{
          formatTimelineAxisTime(tick.timeSec, renderViewport.durationSec)
        }}</span>
      </div>
    </div>
    <Teleport to="body">
      <div
        v-if="hoverPreview && inspectionTime !== null && !lensActive"
        class="timeline-hover-tooltip"
        :data-side="hoverTooltipSide"
        :style="{ left: inspectionClientX + 'px', top: inspectionClientY - 8 + 'px' }"
        role="tooltip"
      >
        <strong>{{ hoverPreview.title }}</strong>
        <span v-for="line in hoverPreview.lines" :key="line">{{ line }}</span>
      </div>
    </Teleport>
  </section>
</template>
