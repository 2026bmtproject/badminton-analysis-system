import type { RallyModel } from "../domain/models";

export type TimelineViewport = {
  startSec: number;
  endSec: number;
  durationSec: number;
};

export type TimelineFit = "match" | "rally";

export function matchViewport(durationSec: number): TimelineViewport {
  return viewport(0, durationSec);
}

export function rallyViewport(rally: RallyModel): TimelineViewport {
  return viewport(rally.start, rally.end);
}

export function fitViewport(
  fit: TimelineFit,
  durationSec: number,
  selectedRally: RallyModel | null,
): TimelineViewport {
  return fit === "rally" && selectedRally
    ? rallyViewport(selectedRally)
    : matchViewport(durationSec);
}

function viewport(startSec: number, endSec: number): TimelineViewport {
  return {
    startSec,
    endSec,
    durationSec: Math.max(0.001, endSec - startSec),
  };
}

export function timeToPercent(timeSec: number, view: TimelineViewport): number {
  return ((timeSec - view.startSec) / view.durationSec) * 100;
}

export function visibleRallies(rallies: RallyModel[], view: TimelineViewport) {
  return rallies.filter(
    (rally) => rally.end >= view.startSec && rally.start <= view.endSec,
  );
}
