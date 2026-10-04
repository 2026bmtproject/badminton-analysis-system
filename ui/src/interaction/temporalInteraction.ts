import type { TimelineViewport } from "../temporal/timeline";

const DEFAULT_MARK_HIT_TOLERANCE_PX = 7;

export function clampFraction(value: number) {
  return Math.min(1, Math.max(0, value));
}

export function timeFromClientX(
  clientX: number,
  left: number,
  width: number,
  viewport: TimelineViewport,
) {
  const fraction = clampFraction((clientX - left) / Math.max(1, width));
  return viewport.startSec + viewport.durationSec * fraction;
}

export function percentOfDuration(timeSec: number, durationSec: number) {
  if (!(durationSec > 0)) return 0;
  return clampFraction(timeSec / durationSec) * 100;
}

export type EdgeLabelSide = "start" | "center" | "end";

export function edgeLabelSide(
  offsetPx: number,
  widthPx: number,
  edgePaddingPx: number,
): EdgeLabelSide {
  if (offsetPx < edgePaddingPx) return "start";
  if (widthPx - offsetPx < edgePaddingPx) return "end";
  return "center";
}

export function nearestTemporalMark<T>(
  items: T[],
  timeSec: number,
  readTime: (item: T) => number,
  viewport: TimelineViewport,
  width: number,
  tolerancePx = DEFAULT_MARK_HIT_TOLERANCE_PX,
) {
  if (!items.length || width <= 0 || viewport.durationSec <= 0) return null;
  const toleranceSec = (tolerancePx / width) * viewport.durationSec;
  let nearest: T | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const item of items) {
    const nextDistance = Math.abs(readTime(item) - timeSec);
    if (nextDistance <= toleranceSec && nextDistance < distance) {
      nearest = item;
      distance = nextDistance;
    }
  }
  return nearest;
}
