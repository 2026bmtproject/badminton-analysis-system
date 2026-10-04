import type { RallyModel } from "../domain/models";
import type { TimelineViewport } from "./timeline";
import { rallyViewport } from "./timeline";

export function viewportContainsTime(view: TimelineViewport, timeSec: number) {
  return timeSec >= view.startSec && timeSec <= view.endSec;
}

export function playbackFollowViewport(
  manualNavigation: boolean,
  timeSec: number,
  current: TimelineViewport,
  durationSec: number,
  activeRally: RallyModel | null,
): TimelineViewport {
  if (manualNavigation || viewportContainsTime(current, timeSec)) return current;
  if (activeRally) return rallyViewport(activeRally);
  const span = Math.min(durationSec, Math.max(0.001, current.durationSec));
  const startSec = Math.min(Math.max(0, timeSec - span / 2), Math.max(0, durationSec - span));
  return { startSec, endSec: startSec + span, durationSec: span };
}
