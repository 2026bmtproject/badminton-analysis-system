import type { MatchModel, RallyModel, StrokeModel } from "../domain/models";

export function canonicalInteger(value: unknown): number | null {
  if (typeof value !== "string" || !/^(0|[1-9]\d*)$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function resolveRouteRally(model: MatchModel, value: unknown) {
  const id = canonicalInteger(value);
  return id === null
    ? null
    : (model.rallies.find((rally) => rally.id === id) ?? null);
}

export function resolveRouteStroke(rally: RallyModel, value: unknown) {
  const eventIndex = canonicalInteger(value);
  return eventIndex === null
    ? null
    : (rally.hits?.find((stroke) => stroke.eventIndex === eventIndex) ?? null);
}

export function findStrokeTarget(
  model: MatchModel,
  eventIndex: number | null,
): { rally: RallyModel; stroke: StrokeModel } | null {
  if (eventIndex === null) return null;
  for (const rally of model.rallies) {
    const stroke = rally.hits?.find((item) => item.eventIndex === eventIndex);
    if (stroke) return { rally, stroke };
  }
  return null;
}
