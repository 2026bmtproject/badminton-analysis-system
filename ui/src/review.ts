import type { CommentaryEventModel, RallyModel } from "./domain/models";

export type RallySort = "time" | "highlight";

export function rankRallies(
  rallies: RallyModel[],
  sort: RallySort,
): RallyModel[] {
  return [...rallies].sort(
    sort === "highlight"
      ? (a, b) =>
          (b.highlight ?? -1) - (a.highlight ?? -1) ||
          a.start - b.start ||
          a.id - b.id
      : (a, b) => a.start - b.start,
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
  if (count === null) return "未提供擊球資料";
  return count === 0 ? "未偵測到擊球" : `${count} 拍`;
}
