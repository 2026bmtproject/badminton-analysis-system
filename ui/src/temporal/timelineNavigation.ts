import type { RallyModel } from "../domain/models";
import { activeRallyAt } from "./activeContext";
import { timeToPercent, type TimelineFit, type TimelineViewport } from "./timeline";

/** 500 ms remains well above one video frame and avoids meaningless sub-frame zoom. */
export const MIN_TIMELINE_VIEWPORT_SECONDS = 0.5;

export function constrainTimelineViewport(
  startSec: number,
  durationSec: number,
  totalDurationSec: number,
  minimumDurationSec = MIN_TIMELINE_VIEWPORT_SECONDS,
): TimelineViewport {
  const total = Math.max(0.001, Number.isFinite(totalDurationSec) ? totalDurationSec : 0.001);
  const minimum = Math.min(total, Math.max(0.001, minimumDurationSec));
  const duration = Math.min(
    total,
    Math.max(minimum, Number.isFinite(durationSec) ? durationSec : total),
  );
  const maximumStart = Math.max(0, total - duration);
  const start = Math.min(
    maximumStart,
    Math.max(0, Number.isFinite(startSec) ? startSec : 0),
  );
  return { startSec: start, endSec: start + duration, durationSec: duration };
}

export function zoomTimelineViewport(
  current: TimelineViewport,
  scale: number,
  anchorTimeSec: number,
  totalDurationSec: number,
) {
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  const anchorFraction = Math.min(
    1,
    Math.max(0, (anchorTimeSec - current.startSec) / Math.max(0.001, current.durationSec)),
  );
  const nextDuration = constrainTimelineViewport(
    0,
    current.durationSec * safeScale,
    totalDurationSec,
  ).durationSec;
  const nextStart = anchorTimeSec - nextDuration * anchorFraction;
  return constrainTimelineViewport(nextStart, nextDuration, totalDurationSec);
}

export function panTimelineViewport(
  current: TimelineViewport,
  deltaSec: number,
  totalDurationSec: number,
) {
  return constrainTimelineViewport(
    current.startSec + (Number.isFinite(deltaSec) ? deltaSec : 0),
    current.durationSec,
    totalDurationSec,
  );
}

/**
 * Double-click toggles rally focus. Inside a focused rally any double-click
 * returns to the match; elsewhere it focuses the rally under the pointer, and
 * a custom zoom over a gap resets to the match.
 */
export function doubleClickFit<T>(
  fit: TimelineFit | "custom",
  rallyAtPoint: T | null,
): { fit: TimelineFit; rally: T | null } | null {
  if (fit === "rally") return { fit: "match", rally: null };
  if (rallyAtPoint) return { fit: "rally", rally: rallyAtPoint };
  return fit === "custom" ? { fit: "match", rally: null } : null;
}

/**
 * The rally a double-click would focus, shown as a band under the pointer.
 * Hidden inside a focused rally and mid-lens. While playback holds the
 * playhead centred it stays, following whatever rally slides under the pointer.
 */
export function hoverBandRally<T extends Pick<RallyModel, "start" | "end">>(
  rallies: readonly T[],
  timeSec: number | null,
  view: { fit: TimelineFit | "custom"; lensActive: boolean },
): T | null {
  if (timeSec === null || view.fit === "rally" || view.lensActive) return null;
  return activeRallyAt(rallies, timeSec);
}

export type FocusBand = { state: "selected" | "active"; left: number; width: number };

/**
 * Bands behind the selected and the playing Rally, in Timeline percent. Each
 * lane says what span a Rally covers on it. The playing band is left out
 * while the playing Rally is the selected one.
 */
export function focusBands(
  selectedId: number | null | undefined,
  activeId: number | null | undefined,
  spanOf: (id: number) => { start: number; end: number } | null,
  view: TimelineViewport,
): FocusBand[] {
  const bands: FocusBand[] = [];
  const add = (state: FocusBand["state"], id: number | null | undefined) => {
    const span = id === null || id === undefined ? null : spanOf(id);
    if (!span) return;
    const left = timeToPercent(span.start, view);
    bands.push({ state, left, width: Math.max(0, timeToPercent(span.end, view) - left) });
  };
  add("selected", selectedId);
  if (activeId !== selectedId) add("active", activeId);
  return bands;
}
