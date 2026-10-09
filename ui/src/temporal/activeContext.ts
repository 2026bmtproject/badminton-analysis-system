import type { MatchModel, RallyModel, ScoreModel, StrokeModel } from "../domain/models";

export type ActiveMatchContext = {
  rally: RallyModel | null;
  stroke: StrokeModel | null;
  scoreRally: RallyModel | null;
  score: ScoreModel | null;
};

/** Resolve the last rally whose start is not after time using the sorted match index. */
export function rallyAtOrBefore(rallies: RallyModel[], timeSec: number) {
  let low = 0;
  let high = rallies.length - 1;
  let candidate: RallyModel | null = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const rally = rallies[middle]!;
    if (rally.start <= timeSec) {
      candidate = rally;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return candidate;
}

export function activeRallyAt(rallies: RallyModel[], timeSec: number) {
  if (!Number.isFinite(timeSec)) return null;
  const candidate = rallyAtOrBefore(rallies, timeSec);
  // Half-open intervals make an adjacent rally own the exact shared boundary.
  return candidate && timeSec < candidate.end ? candidate : null;
}

export function activeStrokeAt(rally: RallyModel | null, timeSec: number) {
  if (!rally?.hits?.length || !Number.isFinite(timeSec)) return null;
  let active: StrokeModel | null = null;
  for (const stroke of rally.hits) {
    if (stroke.time > timeSec) break;
    active = stroke;
  }
  return active;
}

export function scoreContextAt(rallies: RallyModel[], timeSec: number) {
  const active = activeRallyAt(rallies, timeSec);
  return active?.score ? active : null;
}

export function resolveActiveMatchContext(
  model: MatchModel | null,
  timeSec: number,
): ActiveMatchContext {
  if (!model || model.layoutOnly) {
    return { rally: null, stroke: null, scoreRally: null, score: null };
  }
  const rally = activeRallyAt(model.rallies, timeSec);
  const scoreRally = scoreContextAt(model.rallies, timeSec);
  return {
    rally,
    stroke: activeStrokeAt(rally, timeSec),
    scoreRally,
    // A score is a segment observation with no guaranteed before/after meaning;
    // do not carry it into a gap where that segment is no longer active.
    score: scoreRally?.score ?? null,
  };
}
