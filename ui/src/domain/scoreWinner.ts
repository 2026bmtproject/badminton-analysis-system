import type { ScoreModel } from "./models";

/** Only an explicit within-rally transition can establish a scoring player. */
export function winnerFromTransition(
  before: ScoreModel | null,
  after: ScoreModel | null,
  identityKnown: boolean,
): "a" | "b" | null {
  if (!identityKnown || !before || !after) return null;
  const a = after[0] - before[0], b = after[1] - before[1];
  if (a === 1 && b === 0) return "a";
  if (a === 0 && b === 1) return "b";
  return null;
}
