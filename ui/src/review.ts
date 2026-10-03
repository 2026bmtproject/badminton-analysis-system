import type { CommentaryEventModel, RallyModel } from "./domain/models";

export type RallySort = "time" | "highlight" | "cheer";

export function rankRallies(
  rallies: RallyModel[],
  sort: RallySort,
): RallyModel[] {
  return [...rallies].sort(
    sort === "time"
      ? (a, b) => a.start - b.start || a.id - b.id
      : (a, b) => {
          const scoreA = sort === "cheer" ? a.audio?.confidence : a.highlight;
          const scoreB = sort === "cheer" ? b.audio?.confidence : b.highlight;
          return (scoreB ?? -1) - (scoreA ?? -1) || a.start - b.start || a.id - b.id;
        },
  );
}

export function relatedCommentaryEvents(
  events: CommentaryEventModel[],
  eventIndex: number,
) {
  return events.filter((event) => event.strokeIndex === eventIndex);
}
export function hitStatus(status: string | undefined, count: number | null) {
  if (status === "error") return "擊球資料讀取失敗";
  if (status === "stale") return "擊球資料已過期";
  if (count === null) return "未提供擊球資料";
  return count === 0 ? "未偵測到擊球" : `${count} 拍`;
}
