import { inject, provide, shallowRef, type InjectionKey } from "vue";
import type { MatchModel } from "../domain/models";
import {
  useReviewWorkspace,
  type PlayerController,
} from "./useReviewWorkspace";

export function createMatchContext() {
  const model = shallowRef<MatchModel | null>(null);
  const player = shallowRef<PlayerController | null>(null);
  const workspace = useReviewWorkspace(model, player);
  let refresh: () => Promise<void> = async () => {
    throw new Error("Match refresh is not available");
  };
  const setRefreshMatch = (next: () => Promise<void>) => {
    refresh = next;
  };
  const refreshMatch = () => refresh();
  return { model, player, workspace, refreshMatch, setRefreshMatch };
}

export type MatchContext = ReturnType<typeof createMatchContext>;

const matchContextKey: InjectionKey<MatchContext> = Symbol("match-context");

export function provideMatchContext(context: MatchContext) {
  provide(matchContextKey, context);
}

export function useMatchContext(): MatchContext {
  const context = inject(matchContextKey);
  if (!context) throw new Error("Review requires a loaded MatchShell");
  return context;
}
