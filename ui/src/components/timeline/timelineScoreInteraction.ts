import type { RallyModel } from "../../domain/models";
import { nearestTemporalMark } from "../../interaction/temporalInteraction";
import type { TimelineViewport } from "../../temporal/timeline";

export function exactScoreRally(
  rallies: RallyModel[],
  rallyId: number,
): RallyModel | null {
  if (!Number.isInteger(rallyId)) return null;
  return rallies.find((rally) => rally.id === rallyId) ?? null;
}

export function scoreLaneRally(
  rallies: RallyModel[],
  exactRallyId: number | null,
  timeSec: number,
  viewport: TimelineViewport,
  width: number,
): RallyModel | null {
  if (exactRallyId !== null) return exactScoreRally(rallies, exactRallyId);
  return nearestTemporalMark(
    rallies,
    timeSec,
    (rally) => rally.end,
    viewport,
    width,
  );
}
