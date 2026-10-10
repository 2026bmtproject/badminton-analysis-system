import type { StageState } from "../domain/models";
import type { LocalAnalysisMatch } from "./pipelineTasks";
import { stageLabel } from "./stageLabels";

/** The Review export names its states by artifact alias, not by pipeline stage. */
const ALIAS_STAGES: Record<string, string> = {
  segments: "match_segmentation", scores: "score_recognition", events: "event_detection",
  strokes: "stroke_classification", audio_signals: "audio_highlight", highlights: "highlight_ranking",
  identity: "player_identity", commentary: "commentary", commentary_segments: "commentary",
  court: "court_detection", pose: "pose", shuttle: "shuttle_tracking",
};
export function reviewStateLabel(alias: string): string {
  return stageLabel(ALIAS_STAGES[alias] ?? alias);
}

/** "球員身分對應 已過期，需要重跑": which inputs changed is detail the analysis page plan already shows. */
export function staleStageLine(stage: string): string {
  return `${stageLabel(stage)} 已過期，需要重跑`;
}

/**
 * One line per stale or broken stage, then one line for every result too old to say whether it is current: those
 * are still used, so they read as a note rather than a problem each.
 */
export function reviewIssueLines(states: Record<string, StageState>): string[] {
  const lines: string[] = [];
  const unknown: string[] = [];
  for (const [alias, state] of Object.entries(states)) {
    if (state.status === "stale") lines.push(`${reviewStateLabel(alias)} 已過期，需要重跑`);
    else if (state.status === "error") lines.push(`${reviewStateLabel(alias)} 資料錯誤${state.message ? `（${state.message}）` : ""}`);
    else if (state.status === "unknown") unknown.push(reviewStateLabel(alias));
  }
  if (unknown.length) lines.push(unknownLine(unknown));
  return lines;
}
function unknownLine(labels: string[]) {
  return `舊版結果，無法確認是否最新：${[...new Set(labels)].join("、")}`;
}

export type ReviewSnapshot = { states: Record<string, StageState>; importedAt?: string };

/**
 * What the library says about a match. The analysis folder is the truth about what needs re-running; a Review is a
 * snapshot from its import, so with local data at hand it only adds what the folder cannot know: that the Review
 * itself is behind, or that its own data failed to load.
 */
export function libraryIssueLines(local: LocalAnalysisMatch | null, review: ReviewSnapshot | null): string[] {
  if (!local) return review ? reviewIssueLines(review.states) : [];
  const lines = Object.keys(local.staleStages).map(staleStageLine);
  if (review) {
    const behind = local.resultsUpdatedAt && (!review.importedAt || Date.parse(review.importedAt) < Date.parse(local.resultsUpdatedAt));
    if (behind) lines.push("回看不是最新的分析結果，按「匯入結果」更新");
    else for (const [alias, state] of Object.entries(review.states)) {
      if (state.status === "error") lines.push(`${reviewStateLabel(alias)} 資料錯誤${state.message ? `（${state.message}）` : ""}`);
    }
  }
  if (local.unknownStages.length) lines.push(unknownLine(local.unknownStages.map(stageLabel)));
  return lines;
}
