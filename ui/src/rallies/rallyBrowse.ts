import type { LocationQuery, LocationQueryRaw } from "vue-router";
import type { MatchModel, RallyModel } from "../domain/models";
import { rankRallies, type RallySort } from "../review";

export type CommentaryFilter = "all" | "available" | "unavailable";
export type RallyBrowseState = {
  game: number | null;
  commentary: CommentaryFilter;
  sort: RallySort;
};

export function rallyGames(model: MatchModel) {
  return [...new Set(model.rallies.flatMap((rally) => rally.game ?? []))].sort(
    (a, b) => a - b,
  );
}

export function normalizeRallyBrowseQuery(
  query: LocationQuery,
  model: MatchModel,
): { state: RallyBrowseState; query: LocationQueryRaw } {
  const gameValue = typeof query.game === "string" ? Number(query.game) : NaN;
  const game = rallyGames(model).includes(gameValue) ? gameValue : null;
  const commentary =
    query.commentary === "available" || query.commentary === "unavailable"
      ? query.commentary
      : "all";
  const sort: RallySort =
    query.sort === "highlight" && model.capabilities.highlight
      ? "highlight"
      : query.sort === "cheer" && model.capabilities.cheer
        ? "cheer"
        : "time";
  return {
    state: { game, commentary, sort },
    query: {
      ...(game === null ? {} : { game: String(game) }),
      ...(commentary === "all" ? {} : { commentary }),
      ...(sort === "time" ? {} : { sort }),
    },
  };
}

export function browseRallies(
  model: MatchModel,
  state: RallyBrowseState,
): RallyModel[] {
  const filtered = model.rallies.filter(
    (rally) =>
      (state.game === null || rally.game === state.game) &&
      (state.commentary === "all" ||
        (state.commentary === "available") ===
          (rally.commentary.status === "available")),
  );
  return rankRallies(filtered, state.sort);
}
