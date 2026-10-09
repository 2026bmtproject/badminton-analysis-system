import type { CheerWindowModel, RallyModel } from "../../domain/models";
import { formatPreciseTime, formatTime, playerName, scoreText } from "../../format";
import { RALLY_BREAK_LABELS, highlightRanks, rallyWinner, type RallyBreak } from "../../temporal/rallyOutcome";
import { leadEntryAt, type LeadModel } from "../../temporal/scoreLead";
import type { TimelineFit } from "../../temporal/timeline";
import { doubleClickFit } from "../../temporal/timelineNavigation";

export type TimelineHoverMark = {
  /** `break` ids the Rally a break follows. */
  kind: "rally" | "break" | "score" | "stroke" | "commentary" | "cheer" | "cheer-window";
  id: number | string;
};

/** `hint` is a muted last line naming what a double-click does here. */
export type TimelineHoverPreview = { title: string; lines: string[]; hint?: string };

export type LeadPreviewContext = { model: LeadModel; players: { a: string; b: string } };

/** Worded from the double-click outcome itself, so the hint never promises an action that does nothing. */
export function doubleClickHint(fit: TimelineFit | "custom", rallyAtPoint: RallyModel | null) {
  const next = doubleClickFit(fit, rallyAtPoint);
  if (!next) return null;
  return next.fit === "rally" ? "雙擊放大片段" : "雙擊返回全場";
}

/** A position with no mode preview still gets a hint-only tooltip. */
export function withDoubleClickHint(
  preview: TimelineHoverPreview | null,
  hint: string | null,
): TimelineHoverPreview | null {
  if (!hint) return preview;
  return { ...(preview ?? { title: "", lines: [] }), hint };
}

export function rallyBreakPreview(item: RallyBreak): TimelineHoverPreview {
  return {
    title: RALLY_BREAK_LABELS[item.kind],
    lines: [`${(item.end - item.start).toFixed(1)} 秒`],
  };
}

function shorten(text: string, limit: number) {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function rallyIndex(rally: RallyModel) {
  return `片段 ${String(rally.id + 1).padStart(3, "0")}`;
}

function leadPreview(rally: RallyModel, lead: LeadPreviewContext): TimelineHoverPreview | null {
  const entry = leadEntryAt(lead.model, rally.start);
  if (!entry || entry.rally.id !== rally.id) return null;
  const name = (side: "a" | "b") => playerName(lead.players[side], side.toUpperCase());
  if (!("score" in entry)) {
    return { title: "比分未觀測", lines: [formatPreciseTime(rally.start), rallyIndex(rally)] };
  }
  const lines = [
    entry.lead === 0 ? "平手" : `${name(entry.lead > 0 ? "a" : "b")} 領先 ${Math.abs(entry.lead)}`,
  ];
  for (const point of entry.points) lines.push(`${name(point.side)} ${point.match ? "賽點" : "局點"}`);
  if (entry.leadChange) lines.push(`領先易主：${name(entry.leadChange)} 反超`);
  lines.push(`第 ${entry.game + 1} 局 · ${rallyIndex(rally)}`);
  if (entry.uncertain) lines.push("局數待確認");
  return { title: `比分 ${scoreText(entry.score)}`, lines };
}

export function timelineHoverPreview(
  mark: TimelineHoverMark | null,
  rallies: RallyModel[],
  cheerWindows: CheerWindowModel[] = [],
  lead?: LeadPreviewContext,
): TimelineHoverPreview | null {
  if (!mark) return null;
  if (mark.kind === "cheer-window") {
    const window = cheerWindows[Number(mark.id)];
    return window ? { title: formatTime(window.time), lines: [`Cheer probability: ${window.score.toFixed(2)}`] } : null;
  }
  if (mark.kind === "rally") {
    const rally = rallies.find((item) => item.id === mark.id);
    if (!rally) return null;
    const lines = [`${rally.duration.toFixed(2)} 秒`];
    if (rally.hits !== null) lines.push(`${rally.hits.length} 拍`);
    if (rally.score) lines.push(`比分 ${scoreText(rally.score)}`);
    if (rally.multi && rally.subScores.length)
      lines.push(`多筆比分 ${rally.subScores.map(scoreText).join(" → ")}`);
    // Review reasons: a raw recogniser note can run long, so only its start is shown.
    if (rally.scoreIssue !== undefined) lines.push(`比分無法辨識：${shorten(rally.scoreIssue, 60)}`);
    if (rally.gameConflict !== undefined) lines.push(rally.gameConflict);
    // The lane itself stays neutral; who took the point is shown only on request.
    const winner = lead ? rallyWinner(lead.model, rally.id) : null;
    if (winner && lead) lines.push(`得分：${playerName(lead.players[winner], winner.toUpperCase())}`);
    // A ranking score, not a probability: only the place is meaningful.
    const ranks = highlightRanks(rallies);
    const rank = ranks.get(rally.id);
    if (rank !== undefined) lines.push(`精華排名 #${rank} / ${ranks.size}`);
    return { title: `片段 ${String(rally.id + 1).padStart(3, "0")}`, lines };
  }
  if (mark.kind === "score") {
    const rally = rallies.find((item) => item.id === mark.id);
    if (rally && lead) return leadPreview(rally, lead);
    if (!rally?.score) return null;
    return {
      title: `比分 ${scoreText(rally.score)}`,
      lines: [formatPreciseTime(rally.end), `片段 ${String(rally.id + 1).padStart(3, "0")}`],
    };
  }
  if (mark.kind === "stroke") {
    const stroke = rallies.flatMap((rally) => rally.hits ?? []).find((item) => item.eventIndex === mark.id);
    if (!stroke) return null;
    const lines = [formatPreciseTime(stroke.time)];
    if (stroke.type) lines.push(stroke.type);
    if (stroke.player) lines.push(playerName(stroke.player));
    return { title: `第 ${stroke.ordinal} 拍`, lines };
  }
  if (mark.kind === "commentary") {
    const [rallyId, strokeIndex] = String(mark.id).split(":").map(Number);
    const comment = rallies
      .find((rally) => rally.id === rallyId)
      ?.commentary.events.find((item) => item.strokeIndex === strokeIndex);
    if (!comment) return null;
    return {
      title: "賽評",
      lines: [formatPreciseTime(comment.timeSec), shorten(comment.text, 72)],
    };
  }
  const rally = rallies.find((item) => item.id === mark.id);
  if (!rally) return null;
  if (mark.kind === "cheer" && rally.audio) {
    return {
      title: "歡呼訊號",
      lines: [`片段 ${String(rally.id + 1).padStart(3, "0")}`, `信號 ${rally.audio.confidence.toFixed(2)}`],
    };
  }
  return null;
}
