export type PanelDensity = "full" | "compact" | "rail";

export type TimelineTick = {
  timeSec: number;
  percent: number;
  edge: "start" | "center" | "end";
};

export type AdaptivePanelSizes = {
  analysisWidth: number;
  timelineHeight: number;
};

const NICE_INTERVALS_SEC = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1_800, 3_600,
] as const;

export function resolveTimelineDensity(
  width: number,
  height: number,
  forcedRail = false,
): PanelDensity {
  if (forcedRail) return "rail";
  if (width < 720 || height < 190) return "compact";
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
      ? { analysisWidth: 260, timelineHeight: 164 }
      : { analysisWidth: 280, timelineHeight: 180 };
  }
  if (viewportWidth >= 1_440 && viewportHeight >= 850) {
    return { analysisWidth: 340, timelineHeight: 240 };
  }
  return { analysisWidth: 340, timelineHeight: 200 };
}

export function timelineTicks(
  startSec: number,
  endSec: number,
  availableWidth: number,
  density: PanelDensity,
): TimelineTick[] {
  const duration = Math.max(0, endSec - startSec);
  if (density === "rail" || duration <= 0 || availableWidth <= 0) return [];

  const minimumSpacing = density === "compact" ? 112 : 88;
  const maximumCount = Math.max(2, Math.floor(availableWidth / minimumSpacing));
  const rawInterval = duration / Math.max(1, maximumCount - 1);
  const interval =
    NICE_INTERVALS_SEC.find((candidate) => candidate >= rawInterval) ??
    Math.ceil(rawInterval / 3_600) * 3_600;
  const first = Math.ceil(startSec / interval) * interval;
  const ticks: TimelineTick[] = [];

  for (let timeSec = first; timeSec <= endSec + 0.0001; timeSec += interval) {
    const percent = ((timeSec - startSec) / duration) * 100;
    ticks.push({
      timeSec,
      percent,
      edge: percent < 7 ? "start" : percent > 93 ? "end" : "center",
    });
  }

  if (ticks.length < 2) {
    return [
      { timeSec: startSec, percent: 0, edge: "start" },
      { timeSec: endSec, percent: 100, edge: "end" },
    ];
  }
  return ticks.slice(0, maximumCount);
}
