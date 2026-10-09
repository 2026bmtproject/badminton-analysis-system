import type { TimelineFit, TimelineViewport } from "./timeline";
import { constrainTimelineViewport } from "./timelineNavigation";

export function viewportContainsTime(view: TimelineViewport, timeSec: number) {
  return timeSec >= view.startSec && timeSec <= view.endSec;
}

/** Centres `timeSec` at a fixed span; near the match edges the playhead drifts off centre instead. */
export function centeredFollowViewport(
  timeSec: number,
  spanSec: number,
  totalSec: number,
): TimelineViewport {
  return constrainTimelineViewport(timeSec - spanSec / 2, spanSec, totalSec);
}

/** The playhead stays centred only while playing in a zoomed fit the user is not navigating. */
export function shouldCenterFollow(state: {
  playing: boolean;
  fit: TimelineFit | "custom";
  manualNavigation: boolean;
  lensActive: boolean;
}) {
  return state.playing && state.fit !== "match" && !state.manualNavigation && !state.lensActive;
}

/**
 * Brings an off-screen playhead back into view after a discrete jump (keyboard
 * seek, list selection) while centred follow is not running. The zoom is kept.
 */
export function playbackFollowViewport(
  manualNavigation: boolean,
  timeSec: number,
  current: TimelineViewport,
  durationSec: number,
): TimelineViewport {
  if (manualNavigation || viewportContainsTime(current, timeSec)) return current;
  return centeredFollowViewport(timeSec, current.durationSec, durationSec);
}
