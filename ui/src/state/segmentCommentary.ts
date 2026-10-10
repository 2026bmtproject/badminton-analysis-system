import { computed, ref, watch, type Ref } from "vue";
import type { MatchModel, RallyModel } from "../domain/models";
import { useTaskFeed } from "../composables/useTaskFeed";
import { isActiveTask, startSegmentCommentary, type PipelineTask } from "../data/pipelineTasks";

/** What the Analysis window can do about one Rally's missing commentary. */
export type CommentaryRequestState =
  /** No local analysis behind this Review (a fixture), so there is nothing to ask. */
  | { kind: "none" }
  | { kind: "ready" }
  | { kind: "blocked"; reason: string }
  | { kind: "starting" }
  | { kind: "running" }
  | { kind: "publishing" }
  | { kind: "failed"; error: string };

/** Review stages the commentary stage depends on, by their Review export names. */
const COMMENTARY_INPUTS: Record<string, string> = {
  segments: "片段",
  events: "擊球偵測",
  strokes: "球種辨識",
  scores: "比分辨識",
  identity: "球員身分",
};

const UNSUPPORTED_REASONS: Record<string, string> = {
  player_identity_unavailable: "此片段缺少可用的球員身分，無法產生賽評。",
  multiple_recovered_rallies_unsupported: "此片段含多個回合，暫不支援賽評。",
};

export function unsupportedCommentaryReason(reason: string | undefined) {
  return UNSUPPORTED_REASONS[reason ?? ""] ?? "此片段目前不支援賽評。";
}

/** Stage errors arrive as English diagnostics; the common ones read better in plain Chinese. */
function taskError(task: PipelineTask) {
  const error = task.error ?? "";
  const unsupported = /unsupported: (\w+)/.exec(error)?.[1];
  if (unsupported) return unsupportedCommentaryReason(unsupported);
  return error || "賽評產生失敗";
}

/**
 * Generates commentary for one Rally from the Review, and reloads the Review in place once any task of this
 * match has published new results, so the window shows them without losing the playhead.
 */
export function useSegmentCommentary(model: Ref<MatchModel | null>, refreshMatch: () => Promise<void>) {
  const { tasks, active, offline, reviewSync, refresh } = useTaskFeed();
  const matchId = computed(() => model.value?.source?.matchId ?? null);
  const starting = ref<number | null>(null);
  const startError = ref<{ rallyId: number; message: string } | null>(null);

  const missingInputs = computed(() => {
    const states = model.value?.states ?? {};
    return Object.entries(COMMENTARY_INPUTS)
      .filter(([name]) => states[name]?.status !== "available" && states[name]?.status !== "unknown")
      .map(([, label]) => label);
  });

  /** Newest task generating this Rally; tasks arrive newest first. */
  function taskFor(rallyId: number) {
    return tasks.value.find((task) => task.matchId === matchId.value && task.segmentIndex === rallyId) ?? null;
  }

  function state(rally: RallyModel): CommentaryRequestState {
    if (rally.commentary.status === "unsupported")
      return { kind: "blocked", reason: unsupportedCommentaryReason(rally.commentary.unsupportedReason) };
    if (!matchId.value) return { kind: "none" };
    if (starting.value === rally.id) return { kind: "starting" };
    const task = taskFor(rally.id);
    if (task && isActiveTask(task)) return { kind: "running" };
    if (task?.status === "succeeded" && reviewSync.value[task.id]?.state === "publishing") return { kind: "publishing" };
    if (startError.value?.rallyId === rally.id) return { kind: "failed", error: startError.value.message };
    if (task && (task.status === "failed" || task.status === "interrupted") && rally.commentary.status !== "available")
      return { kind: "failed", error: taskError(task) };
    if (offline.value) return { kind: "blocked", reason: "分析服務未連線，暫時無法產生賽評。" };
    if (missingInputs.value.length)
      return { kind: "blocked", reason: `需要先完成${missingInputs.value.join("、")}。` };
    if (active.value) return { kind: "blocked", reason: "另一個分析正在執行，完成後才能產生賽評。" };
    return { kind: "ready" };
  }

  async function request(rally: RallyModel) {
    if (!matchId.value || starting.value !== null) return;
    starting.value = rally.id;
    startError.value = null;
    try {
      await startSegmentCommentary(matchId.value, rally.id);
      await refresh();
    } catch (cause) {
      startError.value = { rallyId: rally.id, message: cause instanceof Error ? cause.message : "無法開始產生賽評" };
    } finally {
      starting.value = null;
    }
  }

  // Publishes that finished before this Review opened are already in the loaded model.
  const reloaded = new Set(Object.keys(reviewSync.value).filter((id) => reviewSync.value[id]?.state === "done"));
  watch(
    () => tasks.value
      .filter((task) => task.matchId === matchId.value && reviewSync.value[task.id]?.state === "done" && !reloaded.has(task.id))
      .map((task) => task.id),
    (published) => {
      if (!published.length) return;
      published.forEach((id) => reloaded.add(id));
      void refreshMatch().catch(() => { /* the Review keeps its current data; reopening it reloads */ });
    },
  );

  return { state, request };
}
