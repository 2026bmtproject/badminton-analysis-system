import type { RallyModel } from "../../domain/models";
import { formatPreciseTime, playerName, scoreText } from "../../format";

export type TimelineHoverMark = {
  kind: "rally" | "score" | "stroke" | "commentary" | "cheer" | "highlight";
  id: number | string;
};

export type TimelineHoverPreview = { title: string; lines: string[] };

export function timelineHoverPreview(
  mark: TimelineHoverMark | null,
  rallies: RallyModel[],
): TimelineHoverPreview | null {
  if (!mark) return null;
  if (mark.kind === "rally") {
    const rally = rallies.find((item) => item.id === mark.id);
    if (!rally) return null;
    const lines = [`${rally.duration.toFixed(2)} 秒`];
    if (rally.hits !== null) lines.push(`${rally.hits.length} 拍`);
    if (rally.score) lines.push(`比分 ${scoreText(rally.score)}`);
    return { title: `片段 ${String(rally.id + 1).padStart(3, "0")}`, lines };
  }
  if (mark.kind === "score") {
    const rally = rallies.find((item) => item.id === mark.id);
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
    const shortText = comment.text.length > 72 ? `${comment.text.slice(0, 71)}…` : comment.text;
    return {
      title: "賽評",
      lines: [formatPreciseTime(comment.timeSec), shortText],
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
  if (mark.kind === "highlight" && rally.highlight !== null) {
    return {
      title: "精華分數",
      lines: [`片段 ${String(rally.id + 1).padStart(3, "0")}`, rally.highlight.toFixed(3)],
    };
  }
  return null;
}
