import type { RallyModel, ScoreModel } from "../../domain/models";
import { formatPreciseTime, scoreText } from "../../format";

export type AnalysisContextSummary = {
  state: "stroke" | "rally" | "gap";
  title: string;
  details: string[];
  status: string;
};

export function analysisContextSummary(
  rally: RallyModel | null,
  activeStrokeIndex: number | null,
  score: ScoreModel | null,
  currentTimeSec: number,
): AnalysisContextSummary {
  const time = formatPreciseTime(currentTimeSec);
  const scoreDetail = score ? `比分 ${scoreText(score)}` : null;
  if (!rally) {
    return {
      state: "gap",
      title: "比賽空檔",
      details: [scoreDetail, time].filter((item): item is string => Boolean(item)),
      status: "等待下一段",
    };
  }
  const strokePosition = rally.hits?.findIndex((stroke) => stroke.eventIndex === activeStrokeIndex) ?? -1;
  const strokeDetail =
    strokePosition >= 0 && rally.hits
      ? `第 ${strokePosition + 1}/${rally.hits.length} 拍`
      : null;
  return {
    state: strokeDetail ? "stroke" : "rally",
    title: `片段 ${String(rally.id + 1).padStart(3, "0")}`,
    details: [strokeDetail, scoreDetail, time].filter((item): item is string => Boolean(item)),
    status: strokeDetail ? "目前擊球" : "片段進行中",
  };
}
