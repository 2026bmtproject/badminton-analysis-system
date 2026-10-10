import type { RallyModel } from "../../domain/models";
import { scoreText } from "../../format";

export type AnalysisContextSummary = {
  /** `previous`: the playhead is in a gap, so the window keeps showing the Rally just played. */
  state: "stroke" | "rally" | "previous" | "empty";
  title: string;
  details: string[];
  status: string;
};

/** The Analysis window's one-line header for the Rally it shows. */
export function analysisContextSummary(
  rally: RallyModel | null,
  previous: boolean,
  activeStrokeIndex: number | null,
): AnalysisContextSummary {
  if (!rally) return { state: "empty", title: "尚未開始", details: [], status: "播放後顯示回合分析" };
  const hits = rally.hits;
  const details = [
    rally.game === null ? null : `第 ${rally.game + 1} 局`,
    // Scoreboard observations are read before the Rally is played.
    rally.score ? `賽前 ${scoreText(rally.score)}` : null,
    hits?.length ? `${hits.length} 拍` : null,
    `${rally.duration.toFixed(1)} 秒`,
  ].filter((item): item is string => Boolean(item));
  const strokePosition = previous ? -1 : hits?.findIndex((stroke) => stroke.eventIndex === activeStrokeIndex) ?? -1;
  return {
    state: previous ? "previous" : strokePosition >= 0 ? "stroke" : "rally",
    title: `片段 ${String(rally.id + 1).padStart(3, "0")}`,
    details,
    status: previous ? "上一回合" : strokePosition >= 0 && hits ? `第 ${strokePosition + 1}/${hits.length} 拍` : "回合中",
  };
}
