export type PanelDensity = "full" | "compact" | "rail";

export type TimelineTick = {
  timeSec: number;
  percent: number;
  /** Label translateX in percent of its own width: 0 at the left edge, -50 inside, -100 at the right edge. */
  shift: number;
  opacity: number;
};

export type AdaptivePanelSizes = {
  analysisWidth: number;
  timelineHeight: number;
};

/** Window height (header + body) that fits every timeline lane without scrolling or blank space. */
export const TIMELINE_FIT_HEIGHT_PX = 160;

/** Share of the axis over which an edge label eases from centred to edge-aligned. */
const TICK_EDGE_ALIGN_PERCENT = 7;
/** Labels fade over this distance where the view cuts the match, so scrolling never pops them. */
const TICK_EDGE_FADE_PX = 36;

const NICE_INTERVALS_SEC = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1_800, 3_600,
] as const;

export function resolveTimelineDensity(
  width: number,
  height: number,
  forcedRail = false,
): PanelDensity {
  if (forcedRail) return "rail";
  // 110px holds the tallest lane (cheer curve, zoomed stroke labels) at full density inside the default 160px window.
  if (width < 720 || height < 110) return "compact";
  return "full";
}

export function resolveAnalysisDensity(width: number): Exclude<PanelDensity, "rail"> {
  if (width < 310) return "compact";
  return "full";
}

export function adaptiveDefaultPanelSizes(
  viewportWidth: number,
  viewportHeight: number,
): AdaptivePanelSizes {
  if (viewportWidth > 1_100 && viewportWidth <= 1_400) {
    return viewportHeight <= 800
      ? { analysisWidth: 260, timelineHeight: TIMELINE_FIT_HEIGHT_PX }
      : { analysisWidth: 280, timelineHeight: TIMELINE_FIT_HEIGHT_PX };
  }
  return { analysisWidth: 340, timelineHeight: TIMELINE_FIT_HEIGHT_PX };
}

function tickShift(percent: number) {
  const ease = (distance: number) => Math.min(1, Math.max(0, distance / TICK_EDGE_ALIGN_PERCENT));
  return percent < 50 ? -50 * ease(percent) : -100 + 50 * ease(100 - percent);
}

/**
 * Ticks for the visible range. Label alignment and opacity vary continuously with
 * position, so a scrolling axis glides. Pass `matchDurationSec` to fade labels at
 * edges that cut into the match; edges on the match boundary stay fully opaque.
 */
export function timelineTicks(
  startSec: number,
  endSec: number,
  availableWidth: number,
  density: PanelDensity,
  matchDurationSec?: number,
): TimelineTick[] {
  const duration = Math.max(0, endSec - startSec);
  if (density === "rail" || duration <= 0 || availableWidth <= 0) return [];

  const minimumSpacing = density === "compact" ? 112 : 88;
  const maximumCount = Math.max(2, Math.floor(availableWidth / minimumSpacing));
  const rawInterval = duration / Math.max(1, maximumCount - 1);
  const interval =
    NICE_INTERVALS_SEC.find((candidate) => candidate >= rawInterval) ??
    Math.ceil(rawInterval / 3_600) * 3_600;
  const fadePercent = (TICK_EDGE_FADE_PX / availableWidth) * 100;
  const fadeStart = matchDurationSec !== undefined && startSec > 0.0001;
  const fadeEnd = matchDurationSec !== undefined && endSec < matchDurationSec - 0.0001;
  const tick = (timeSec: number, percent: number): TimelineTick => ({
    timeSec,
    percent,
    shift: tickShift(percent),
    opacity: Math.min(
      1,
      fadeStart ? percent / fadePercent : 1,
      fadeEnd ? (100 - percent) / fadePercent : 1,
    ),
  });
  const ticks: TimelineTick[] = [];
  // Integer multiples keep tick times (and so their render keys) exact while the view scrolls.
  for (let index = Math.ceil(startSec / interval); index * interval <= endSec + 0.0001; index += 1) {
    const timeSec = index * interval;
    ticks.push(tick(timeSec, ((timeSec - startSec) / duration) * 100));
  }

  if (ticks.length < 2) {
    return [
      { timeSec: startSec, percent: 0, shift: 0, opacity: 1 },
      { timeSec: endSec, percent: 100, shift: -100, opacity: 1 },
    ];
  }
  return ticks.slice(0, maximumCount);
}
