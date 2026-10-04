import type { CheerWindowModel } from "../domain/models";
import { timeToPercent, type TimelineViewport } from "./timeline";

export type CheerCurvePath = { segmentIndex: number; d: string };

/** Use the Timeline viewport for x; keep 0 and 1 inside the visible SVG stroke. */
export function cheerCurvePoint(window: CheerWindowModel, view: TimelineViewport) {
  return {
    x: timeToPercent(window.time, view),
    y: 96 - window.score * 92,
  };
}

/** Break paths wherever source windows do not overlap, including between segments. */
export function cheerCurvePaths(windows: CheerWindowModel[], view: TimelineViewport): CheerCurvePath[] {
  const ordered = [...windows].sort((a, b) =>
    a.segmentIndex - b.segmentIndex || a.start - b.start || a.end - b.end,
  );
  const paths: CheerCurvePath[] = [];
  let run: CheerWindowModel[] = [];
  function flush() {
    if (!run.length) return;
    const first = run[0]!;
    const points = run.map((window) => cheerCurvePoint(window, view));
    const d = points.length === 1
      ? `M ${timeToPercent(first.start, view)} ${points[0]!.y} L ${timeToPercent(first.end, view)} ${points[0]!.y}`
      : points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
    paths.push({ segmentIndex: first.segmentIndex, d });
    run = [];
  }
  for (const window of ordered) {
    const previous = run.at(-1);
    if (previous && (window.segmentIndex !== previous.segmentIndex || window.start > previous.end + 1e-6)) flush();
    run.push(window);
  }
  flush();
  return paths;
}
