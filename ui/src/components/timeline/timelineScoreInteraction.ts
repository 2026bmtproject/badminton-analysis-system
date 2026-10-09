import type { RallyModel } from "../../domain/models";
import { nearestTemporalMark } from "../../interaction/temporalInteraction";
import { leadEntryAt, type LeadModel } from "../../temporal/scoreLead";
import type { TimelineViewport } from "../../temporal/timeline";

export function exactScoreRally(
  model: LeadModel,
  rallyId: number,
): RallyModel | null {
  if (!Number.isInteger(rallyId)) return null;
  for (const game of model.games) {
    const step = game.steps.find((item) => item.rally.id === rallyId);
    if (step) return step.rally;
  }
  return null;
}

/** The lead lane holds each observation until the next Rally starts, so a time resolves to the step covering it. */
export function scoreLaneRally(
  model: LeadModel,
  exactRallyId: number | null,
  timeSec: number,
  viewport: TimelineViewport,
  width: number,
): RallyModel | null {
  if (exactRallyId !== null) return exactScoreRally(model, exactRallyId);
  const entry = leadEntryAt(model, timeSec);
  if (entry) return entry.rally;
  return (
    nearestTemporalMark(
      model.games.flatMap((game) => game.steps),
      timeSec,
      (step) => step.start,
      viewport,
      width,
    )?.rally ?? null
  );
}
