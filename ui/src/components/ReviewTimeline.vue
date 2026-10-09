<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
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
import {
  leadChartMarks,
  leadDomain,
  leadEntryAt,
  leadGamePaths,
  leadGridlines,
  leadY,
  scoreLeadModel,
} from "../temporal/scoreLead";
import type { TimelineFit, TimelineViewport } from "../temporal/timeline";
import TimelineLane from "./timeline/TimelineLane.vue";
import type { TimelineMode } from "../state/workspaceLayout";
import { activeRallyAt } from "../temporal/activeContext";
import {
  centeredFollowViewport,
  playbackFollowViewport,
  shouldCenterFollow,
} from "../temporal/timelineFollow";
import {
  doubleClickFit,
  hoverBandRally,
  panTimelineViewport,
  zoomTimelineViewport,
} from "../temporal/timelineNavigation";
import {
  doubleClickHint,
  timelineHoverPreview,
  withDoubleClickHint,
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
const LENS_DURATION_MS = 220;
/** Wheel navigation pauses playback follow; it resumes once the wheel is idle this long. */
const FOLLOW_RESUME_DELAY_MS = 2000;
const SEMANTIC_LABEL_TARGET_WIDTH_PX = 84;
const INSPECTION_EDGE_PADDING_PX = 64;
const SEMANTIC_REVEAL_START_MULTIPLIER = 4;
const SEMANTIC_REVEAL_END_MULTIPLIER = 1.75;
const SEMANTIC_LABEL_EDGE_PERCENT = 8;
const STROKE_LANE_BASE_HEIGHT_PX = 38;
const STROKE_TRACK_BASE_HEIGHT_PX = 28;
const STROKE_DETAIL_HEIGHT_PX = 32;
const SIGNAL_LANE_HEIGHT_PX = 72;
const SIGNAL_LANE_MARGIN_PX = 8;
const props = withDefaults(
  defineProps<{
    model: MatchModel;
    selectedId: number | null;
    selectedStrokeIndex: number | null;
    time: number;
    /** Playing state; the playhead is held centred only while playing. */
    playing?: boolean;
    /** Frame-accurate media time for smooth centred scrolling; falls back to `time`. */
    clock?: () => number;
    activeId: number | null;
    activeStrokeIndex?: number | null;
    scoreContextId?: number | null;
    timelineMode?: TimelineMode | "all";
  }>(),
  {
    timelineMode: "all",
    playing: false,
    activeStrokeIndex: null,
    scoreContextId: null,
  },
);
const emit = defineEmits<{
  rally: [rally: RallyModel];
  stroke: [stroke: StrokeModel];
  commentary: [comment: CommentaryEventModel, rally: RallyModel];
  rallyAt: [rally: RallyModel, timeSec: number];
  seek: [timeSec: number];
  inspect: [timeSec: number | null];
}>();
const density = ref<PanelDensity>("full");
const modeShows = (track: TrackKey) =>
  props.timelineMode === "all" || props.timelineMode === track;
const fit = ref<TimelineFit | "custom">("match");
const renderViewport = ref<TimelineViewport>(
  fitViewport("match", props.model.duration, null),
);
const lensProgress = ref(0);
const lensActive = ref(false);
const manualNavigation = ref(false);
const centerFollowing = computed(() =>
  shouldCenterFollow({
    playing: props.playing,
    fit: fit.value,
    manualNavigation: manualNavigation.value,
    lensActive: lensActive.value,
  }),
);
/** Clock time sampled each follow frame, so the playhead moves in step with the scrolling content. */
const displayTime = ref(props.time);
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
  withDoubleClickHint(
    timelineHoverPreview(hoveredMark.value, props.model.rallies, props.model.cheerTimeline, {
      model: leadModel.value,
      players: props.model.players,
    }),
    inspectionTime.value === null
      ? null
      : doubleClickHint(fit.value, activeRallyAt(props.model.rallies, inspectionTime.value)),
  ),
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
let followResumeTimer: ReturnType<typeof setTimeout> | undefined;
let followFrame: number | undefined;
let followSpanSec = 0;
let followTransition: { from: TimelineViewport; startedAt: number } | null = null;
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
  stopCenterFollow();
  clearTimeout(followResumeTimer);
  if (inspectionFrame !== undefined) cancelAnimationFrame(inspectionFrame);
});
const selectedRally = computed(
  () =>
    props.model.rallies.find((rally) => rally.id === props.selectedId) ?? null,
);
watch([() => props.selectedId, () => props.model.duration], (id) => {
  if (id[0] === null && fit.value === "rally") {
    setFit("match");
    return;
  }
  if (fit.value === "custom") {
    scheduleInspectionRestore();
    return;
  }
  cancelLens();
  lensProgress.value = fit.value === "rally" ? 1 : 0;
  if (centerFollowing.value) {
    // Explicit selection re-zooms to the new rally, gliding there instead of flashing its exact span.
    startCenterFollow(authoritativeViewport(fit.value).durationSec);
    return;
  }
  renderViewport.value = authoritativeViewport(fit.value);
  scheduleInspectionRestore();
});
watch(
  () => props.time,
  (timeSec) => {
    if (lensActive.value || centerFollowing.value) return;
    const next = playbackFollowViewport(
      manualNavigation.value,
      timeSec,
      renderViewport.value,
      props.model.duration,
    );
    if (next !== renderViewport.value) renderViewport.value = next;
  },
  { flush: "sync" },
);
watch(centerFollowing, (active) => {
  if (active) startCenterFollow(renderViewport.value.durationSec);
  else stopCenterFollow();
});
watch(
  () => props.model.scenario,
  () => {
    cancelLens();
    stopCenterFollow();
    fit.value = "match";
    manualNavigation.value = false;
    renderViewport.value = fitViewport("match", props.model.duration, null);
    lensProgress.value = 0;
    clearInspection();
  },
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
    props.model.duration,
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
/** Built from every Rally, not the visible ones, so the scale stays match-wide while zooming. */
const leadModel = computed(() => scoreLeadModel(props.model.rallies));
const leadDomainValue = computed(() => leadDomain(leadModel.value.maxAbsLead));
const leadPaths = computed(() =>
  leadModel.value.games.map((game) =>
    leadGamePaths(game, renderViewport.value, leadDomainValue.value),
  ),
);
const leadMarks = computed(() =>
  leadChartMarks(
    leadModel.value,
    renderViewport.value,
    leadDomainValue.value,
    trackWidth.value,
  ),
);
const leadGrid = computed(() =>
  leadGridlines(leadDomainValue.value).flatMap((lead) => [
    leadY(lead, leadDomainValue.value),
    leadY(-lead, leadDomainValue.value),
  ]),
);
/** Shared by every mode: the rally a double-click would focus, positioned in SVG units so it never snaps against the lanes. */
const hoverBand = computed(() => {
  const rally = hoverBandRally(props.model.rallies, inspectionTime.value, {
    fit: fit.value,
    lensActive: lensActive.value,
    centerFollowing: centerFollowing.value,
  });
  if (!rally) return null;
  const left = position(rally.start);
  return { left, width: Math.max(0, position(rally.end) - left) };
});
const leadFocusBands = computed(() => {
  const bands: { state: "selected" | "active"; left: number; width: number }[] = [];
  const add = (state: (typeof bands)[number]["state"], rallyId: number | null | undefined) => {
    const rally = props.model.rallies.find((item) => item.id === rallyId);
    const entry = rally ? leadEntryAt(leadModel.value, rally.start) : null;
    if (!entry || entry.rally.id !== rallyId) return;
    const left = position(entry.start);
    bands.push({ state, left, width: Math.max(0, position(entry.end) - left) });
  };
  add("selected", props.selectedId);
  if (props.scoreContextId !== props.selectedId) add("active", props.scoreContextId);
  return bands;
});
const leadDescription = computed(
  () =>
    `領先折線圖：中線為平手，往上為 ${playerName(props.model.players.a, "A")} 領先，往下為 ${playerName(props.model.players.b, "B")} 領先`,
);
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
  modeShows("cheer") && capability("cheer"),
);
const cheerPaths = computed(() =>
  cheerCurvePaths(props.model.cheerTimeline ?? [], renderViewport.value),
);
const detailOpacity = computed(() => semanticRevealProgress.value);
const position = (timeSec: number) =>
  timeToPercent(timeSec, renderViewport.value);
const playheadFraction = computed(() =>
  Math.min(
    1,
    Math.max(0, position(centerFollowing.value ? displayTime.value : props.time) / 100),
  ),
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
function resumeFollow() {
  clearTimeout(followResumeTimer);
  followResumeTimer = undefined;
  manualNavigation.value = false;
}
function readClock() {
  return props.clock?.() ?? props.time;
}
/** Holds the playhead centred at a fixed span, first gliding there from the current view. */
function startCenterFollow(spanSec: number) {
  stopCenterFollow();
  followSpanSec = spanSec;
  displayTime.value = readClock();
  clearInspection();
  followTransition = reducedMotionQuery?.matches
    ? null
    : { from: { ...renderViewport.value }, startedAt: performance.now() };
  followFrame = requestAnimationFrame(followCenter);
}
function stopCenterFollow() {
  if (followFrame !== undefined) cancelAnimationFrame(followFrame);
  followFrame = undefined;
  followTransition = null;
}
function followCenter(now: number) {
  followFrame = undefined;
  if (!centerFollowing.value) return;
  const timeSec = readClock();
  displayTime.value = timeSec;
  const target = centeredFollowViewport(timeSec, followSpanSec, props.model.duration);
  if (followTransition) {
    const elapsed = Math.min(1, Math.max(0, now - followTransition.startedAt) / LENS_DURATION_MS);
    // The target keeps moving with playback, so the glide re-aims at it every frame.
    renderViewport.value = interpolateViewport(followTransition.from, target, temporalLensEase(elapsed));
    if (elapsed >= 1) followTransition = null;
  } else renderViewport.value = target;
  followFrame = requestAnimationFrame(followCenter);
}
function setFit(next: TimelineFit, focus: RallyModel | null = null) {
  const anchor = focus ?? selectedRally.value ?? activeRallyAt(props.model.rallies, props.time);
  if (next === "rally" && !anchor) return;
  if (next === fit.value) return;
  const from = { ...renderViewport.value };
  const fromProgress = lensProgress.value;
  fit.value = next;
  resumeFollow();
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
function pauseFollow() {
  manualNavigation.value = true;
  clearTimeout(followResumeTimer);
  followResumeTimer = setTimeout(resumeFollow, FOLLOW_RESUME_DELAY_MS);
}
/** Freezes centred scrolling under the pointer so clicks, drags and double-clicks land where aimed. */
function holdFollowForPointer() {
  if (props.playing && fit.value !== "match") pauseFollow();
}
function panTimeline(event: WheelEvent) {
  if (event.shiftKey || lensActive.value) return;
  event.preventDefault();
  cancelLens();
  pauseFollow();
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
async function toggleFitOnDoubleClick(event: MouseEvent) {
  if (lensActive.value) return;
  const track = surface.value?.querySelector<HTMLElement>(".timeline-lane-track");
  if (!track) return;
  const bounds = track.getBoundingClientRect();
  const timeSec = timeFromClientX(event.clientX, bounds.left, bounds.width, renderViewport.value);
  const next = doubleClickFit(fit.value, activeRallyAt(props.model.rallies, timeSec));
  if (!next) return;
  if (next.rally && next.rally.id !== props.selectedId) {
    emit("rallyAt", next.rally, Math.min(next.rally.end, Math.max(next.rally.start, timeSec)));
    // Let the selection watcher settle before the lens starts, or it would cancel the animation.
    await nextTick();
  }
  setFit(next.fit, next.rally);
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
    const rally = scoreLaneRally(
      leadModel.value,
      null,
      timeSec,
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
    const rally = exactScoreRally(leadModel.value, Number(id));
    return rally ? { kind, id: rally.id } : null;
  }
  return null;
}
function updateInspection(
  clientX: number,
  track: HTMLElement,
  exactMark: TimelineHoverMark | null = null,
) {
  if (lensActive.value || centerFollowing.value || !surface.value) {
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
  // Content slides under a still pointer while centred, so a hover readout would only flicker.
  if (centerFollowing.value) return;
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
      leadModel.value,
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
  holdFollowForPointer();
  if (event.button !== 0 || lensActive.value || !(event.target instanceof Element)) return;
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
  holdFollowForPointer();
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
  const rally = exactScoreRally(leadModel.value, rallyId);
  if (rally) emit("rally", rally);
}
function scoreMarkHovered(rallyId: number) {
  return hoveredMark.value?.kind === "score" && hoveredMark.value.id === rallyId;
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
    :data-density="density"
    :aria-busy="lensActive"
    :style="timelineStyle"
  >
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
      @dblclick="toggleFitOnDoubleClick"
      @wheel="panTimeline"
    >
      <!-- First in the surface so every lane paints over it; SVG units keep it
           from snapping to whole pixels against the SVG lanes. -->
      <svg v-if="hoverBand" class="timeline-hover-band" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <rect :x="hoverBand.left" y="0" :width="hoverBand.width" height="100" />
      </svg>
      <section v-if="modeShows('rally') || modeShows('score')" class="timeline-band timeline-band--match" aria-label="比賽">
        <header class="timeline-band-label">
          <strong>比賽</strong>
        </header>
        <div class="timeline-band-tracks">
          <TimelineLane
            v-if="modeShows('rally')"
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
            v-if="modeShows('score') && capability('score')"
            kind="score"
            :description="leadDescription"
          >
            <template #label>
              <span class="lead-legend">
                <span class="lead-legend__side" data-side="a" :title="playerName(model.players.a, 'A')"><i aria-hidden="true" />A</span>
                <span class="lead-legend__title">領先</span>
                <span class="lead-legend__side" data-side="b" :title="playerName(model.players.b, 'B')"><i aria-hidden="true" />B</span>
              </span>
            </template>
            <!-- Everything that is not text is drawn in SVG: HTML boxes snap to whole
                 pixels and would crawl against the line while playback scrolls. -->
            <svg class="lead-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
              <template v-for="band in leadFocusBands" :key="`focus-${band.state}`">
                <rect class="lead-focus" :data-state="band.state" :x="band.left" y="0" :width="band.width" height="100" />
                <line class="lead-focus-edge" :data-state="band.state" :x1="band.left" :x2="band.left" y1="0" y2="100" />
                <line v-if="band.state === 'selected'" class="lead-focus-edge" data-state="selected" :x1="band.left + band.width" :x2="band.left + band.width" y1="0" y2="100" />
              </template>
              <line v-for="separator in leadMarks.separators" :key="`separator-${separator.game}`" class="lead-separator" :x1="separator.x" :x2="separator.x" y1="0" y2="100" />
              <line v-for="y in leadGrid" :key="`grid-${y}`" class="lead-chart__grid" x1="0" x2="100" :y1="y" :y2="y" />
              <line class="lead-chart__zero" x1="0" x2="100" y1="50" y2="50" />
              <template v-for="paths in leadPaths" :key="`lead-${paths.game}`">
                <path v-if="paths.areaA" class="lead-chart__area" data-side="a" :d="paths.areaA" />
                <path v-if="paths.areaB" class="lead-chart__area" data-side="b" :d="paths.areaB" />
                <path v-if="paths.dashed" class="lead-chart__gap" :d="paths.dashed" />
                <path v-if="paths.line" class="lead-chart__line" :d="paths.line" />
              </template>
            </svg>
            <svg class="lead-marks" aria-hidden="true">
              <circle v-for="mark in leadMarks.uncertain" :key="`uncertain-${mark.rallyId}`" class="lead-uncertain" :cx="mark.x + '%'" :cy="mark.y + '%'" r="3.5" />
              <svg
                v-for="change in leadMarks.changes"
                :key="`change-${change.rallyId}`"
                class="lead-change"
                :class="{ hovered: scoreMarkHovered(change.rallyId) }"
                :data-side="change.side"
                :x="change.x + '%'"
                y="50%"
                overflow="visible"
              >
                <circle r="4" />
              </svg>
            </svg>
            <span
              v-for="label in leadMarks.labels"
              :key="`game-${label.game}`"
              class="lead-game-label"
              :style="{ left: label.x + '%' }"
              aria-hidden="true"
            >{{ label.text }}</span>
            <!-- Positioned by its own composited transform in track-width units: a
                 1px divider placed with `left` snaps to whole pixels and flickers
                 against the digits, which glide by subpixels. -->
            <span
              v-for="chip in leadMarks.chips"
              :key="`chip-${chip.rallyId}`"
              class="score-state timeline-score-state lead-chip"
              :data-half="chip.half"
              :style="{ transform: `translateX(calc(${chip.x}cqw - 50%))` }"
              aria-hidden="true"
            >
              <span>{{ chip.score[0] }}</span>
              <span class="score-state-divider" />
              <span>{{ chip.score[1] }}</span>
            </span>
            <span
              v-for="peak in leadMarks.peaks"
              :key="`peak-${peak.rallyId}`"
              class="lead-peak"
              :data-direction="peak.direction"
              :data-align="peak.align"
              :style="{ left: peak.x + '%', top: peak.y + '%' }"
              aria-hidden="true"
            >{{ peak.text }}</span>
            <button
              v-for="change in leadMarks.changes"
              :key="`change-${change.rallyId}`"
              type="button"
              class="lead-mark lead-mark--change"
              data-timeline-kind="score"
              :data-timeline-id="change.rallyId"
              :style="{ left: change.x + '%' }"
              :aria-label="`片段 ${String(change.rallyId + 1).padStart(3, '0')}，領先易主，${playerName(model.players[change.side], change.side.toUpperCase())} 反超`"
              @click.stop="selectScoreMarker(change.rallyId)"
            />
          </TimelineLane>
        </div>
      </section>

      <section v-if="modeShows('stroke') || modeShows('commentary')" class="timeline-band timeline-band--stroke" aria-label="擊球">
        <header class="timeline-band-label">
          <strong>擊球</strong>
        </header>
        <div class="timeline-band-tracks">
          <TimelineLane
            v-if="modeShows('stroke') && capability('stroke')"
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
                <span
                  v-if="showStrokeDetail(index)"
                  class="stroke-marker-detail"
                  :data-side="strokeDetailSide(stroke.time)"
                >
                  <strong>{{ stroke.type ?? "球種未提供" }}</strong>
                  <small>{{ playerName(stroke.player) }}</small>
                </span>
              </button>
            </template>
          </TimelineLane>
          <TimelineLane
            v-if="modeShows('commentary') && capability('commentary')"
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
          (showOverviewSignals && modeShows('highlight') && capability('highlight'))
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
            v-if="showOverviewSignals && modeShows('highlight') && capability('highlight')"
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
      <div class="time-axis">
        <span v-for="tick in axisTicks" :key="tick.timeSec" :style="{ left: tick.percent + '%', transform: `translateX(${tick.shift}%)`, opacity: tick.opacity }">{{
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
        <strong v-if="hoverPreview.title">{{ hoverPreview.title }}</strong>
        <span v-for="line in hoverPreview.lines" :key="line">{{ line }}</span>
        <span v-if="hoverPreview.hint" class="timeline-hover-tooltip__hint">{{ hoverPreview.hint }}</span>
      </div>
    </Teleport>
  </section>
</template>
