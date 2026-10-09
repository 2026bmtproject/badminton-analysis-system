import type { CheerWindowModel } from "../domain/models";
import type { LeadModel, LeadSide } from "./scoreLead";
import { timeToPercent, type TimelineViewport } from "./timeline";

/** A smoothed window at or above this probability counts as cheering. */
export const CHEER_THRESHOLD = 0.5;
/** How many peaks the lane labels, match-wide. */
export const CHEER_PEAK_COUNT = 5;

/** Windows that overlap their neighbour within one segment, in time order, with the 3-window moving average the lane draws. */
export type CheerRun = { segmentIndex: number; windows: CheerWindowModel[]; smoothed: number[] };

/** Break runs wherever source windows do not overlap, including between segments. */
export function cheerRuns(windows: CheerWindowModel[]): CheerRun[] {
  const ordered = [...windows].sort((a, b) =>
    a.segmentIndex - b.segmentIndex || a.start - b.start || a.end - b.end,
  );
  const runs: CheerRun[] = [];
  let run: CheerWindowModel[] = [];
  function flush() {
    if (!run.length) return;
    // The model flips between ~0 and ~1 window to window; averaging with the
    // neighbours keeps a single noisy window from reading as a cheer.
    const smoothed = run.map((_, index) => {
      const near = run.slice(Math.max(0, index - 1), index + 2);
      return near.reduce((sum, window) => sum + window.score, 0) / near.length;
    });
    runs.push({ segmentIndex: run[0]!.segmentIndex, windows: run, smoothed });
    run = [];
  }
  for (const window of ordered) {
    const previous = run.at(-1);
    if (previous && (window.segmentIndex !== previous.segmentIndex || window.start > previous.end + 1e-6)) flush();
    run.push(window);
  }
  flush();
  return runs;
}

export type CheerWaveGeometry = { mid: number; amp: number };
/** `strong` pieces are where the smoothed probability is at or above the threshold. */
export type CheerWavePath = { segmentIndex: number; strong: boolean; d: string };

const round = (value: number) => Math.round(value * 1e4) / 1e4;

type WavePoint = { time: number; score: number };

/** Cut a polyline where it crosses the threshold, interpolating the crossing so both pieces meet on it. */
function thresholdPieces(points: WavePoint[]) {
  const pieces: { strong: boolean; points: WavePoint[] }[] = [];
  let piece = { strong: points[0]!.score >= CHEER_THRESHOLD, points: [points[0]!] };
  for (const point of points.slice(1)) {
    const strong = point.score >= CHEER_THRESHOLD;
    if (strong !== piece.strong) {
      const previous = piece.points.at(-1)!;
      const share = (CHEER_THRESHOLD - previous.score) / (point.score - previous.score);
      const crossing = { time: previous.time + share * (point.time - previous.time), score: CHEER_THRESHOLD };
      piece.points.push(crossing);
      pieces.push(piece);
      piece = { strong, points: [crossing] };
    }
    piece.points.push(point);
  }
  pieces.push(piece);
  return pieces;
}

/**
 * Closed areas mirrored about `mid`: the top edge out to `score × amp` above,
 * the bottom edge as far below, cut into strong and weak pieces at the
 * threshold. x is a Timeline percent and y is in the caller's units. Runs
 * outside the view are skipped.
 */
export function cheerWavePaths(
  runs: CheerRun[],
  view: TimelineViewport,
  { mid, amp }: CheerWaveGeometry,
): CheerWavePath[] {
  const paths: CheerWavePath[] = [];
  for (const run of runs) {
    const first = run.windows[0]!;
    const last = run.windows.at(-1)!;
    if (last.end < view.startSec || first.start > view.endSec) continue;
    // A lone window has no neighbour to slope to, so it spans its own source interval.
    const points = run.windows.length === 1
      ? [{ time: first.start, score: run.smoothed[0]! }, { time: first.end, score: run.smoothed[0]! }]
      : run.windows.map((window, index) => ({ time: window.time, score: run.smoothed[index]! }));
    for (const piece of thresholdPieces(points)) {
      const x = piece.points.map((point) => round(timeToPercent(point.time, view)));
      const top = piece.points.map((point, index) => `${index ? "L" : "M"} ${x[index]} ${round(mid - point.score * amp)}`);
      const bottom = piece.points.map((point, index) => `L ${x[index]} ${round(mid + point.score * amp)}`).reverse();
      paths.push({ segmentIndex: run.segmentIndex, strong: piece.strong, d: `${top.join(" ")} ${bottom.join(" ")} Z` });
    }
  }
  return paths;
}

/** The longest cheer of one Rally; `seconds` runs from the first window centre to the last. */
export type CheerPeak = {
  rank: number;
  segmentIndex: number;
  start: number;
  end: number;
  seconds: number;
};

/**
 * The Rallies with the longest sustained cheer, one peak per Rally, best first.
 * The model saturates near 1 at almost every point's end, so the height of a
 * peak says nothing; how long the crowd stays above the threshold does. Ties
 * go to the louder stretch, then the earlier one.
 */
export function cheerPeaks(runs: CheerRun[], count = CHEER_PEAK_COUNT): CheerPeak[] {
  const best = new Map<number, Omit<CheerPeak, "rank"> & { mean: number }>();
  for (const run of runs) {
    let from = -1;
    run.smoothed.forEach((score, index) => {
      const above = score >= CHEER_THRESHOLD;
      if (above && from < 0) from = index;
      const closes = from >= 0 && (!above || index === run.smoothed.length - 1);
      if (!closes) return;
      const to = above ? index : index - 1;
      const stretch = run.smoothed.slice(from, to + 1);
      const candidate = {
        segmentIndex: run.segmentIndex,
        start: run.windows[from]!.time,
        end: run.windows[to]!.time,
        seconds: run.windows[to]!.time - run.windows[from]!.time,
        mean: stretch.reduce((sum, value) => sum + value, 0) / stretch.length,
      };
      from = -1;
      const current = best.get(run.segmentIndex);
      if (!current || candidate.seconds > current.seconds ||
        (candidate.seconds === current.seconds && candidate.mean > current.mean))
        best.set(run.segmentIndex, candidate);
    });
  }
  return [...best.values()]
    .sort((a, b) => b.seconds - a.seconds || b.mean - a.mean || a.start - b.start)
    .slice(0, count)
    .map(({ mean: _mean, ...peak }, index) => ({ ...peak, rank: index + 1 }));
}

export type CheerPeakLabel = { rank: number; segmentIndex: number; x: number; text: string };

/** Rough widths at the lane's 11px label size, with breathing room between labels. */
const PEAK_LABEL_FULL_PX = 66;
const PEAK_LABEL_SHORT_PX = 20;
const PEAK_LABEL_GAP_PX = 6;

/**
 * Peak labels centred on each visible peak: "#1 片段 047" where there is room,
 * just the place where a better-ranked label crowds it, and dropped when even
 * that would overlap. Only the zoom decides the text, so labels never flicker
 * while playback scrolls.
 */
export function cheerPeakLabels(peaks: CheerPeak[], view: TimelineViewport, trackWidth: number): CheerPeakLabel[] {
  const placed: { left: number; right: number }[] = [];
  const labels: CheerPeakLabel[] = [];
  for (const peak of [...peaks].sort((a, b) => a.rank - b.rank)) {
    // Peaks outside the view still claim their room, so one scrolling away never changes a neighbour's text.
    const x = timeToPercent((peak.start + peak.end) / 2, view);
    const px = (x / 100) * trackWidth;
    const full = `#${peak.rank} 片段 ${String(peak.segmentIndex + 1).padStart(3, "0")}`;
    for (const [text, width] of [[full, PEAK_LABEL_FULL_PX], [`#${peak.rank}`, PEAK_LABEL_SHORT_PX]] as const) {
      const box = { left: px - width / 2 - PEAK_LABEL_GAP_PX, right: px + width / 2 + PEAK_LABEL_GAP_PX };
      if (placed.some((other) => box.left < other.right && other.left < box.right)) continue;
      placed.push(box);
      labels.push({ rank: peak.rank, segmentIndex: peak.segmentIndex, x, text });
      break;
    }
  }
  return labels.filter((label) => label.x >= 0 && label.x <= 100);
}

/**
 * What the score did around a Rally, for reading a cheer against it. Scores
 * are taken before each Rally, so `points` are the game or match points the
 * Rally was played at, and `overtake` is the side whose point in this Rally
 * took the lead, read off the next Rally of the same game.
 */
export function cheerScoreMoments(lead: LeadModel, rallyId: number): {
  points: { side: LeadSide; match: boolean }[];
  overtake: LeadSide | null;
} {
  for (const game of lead.games) {
    if (![...game.steps, ...game.gaps].some((entry) => entry.rally.id === rallyId)) continue;
    // An unobserved next Rally is a gap with no step, so no overtake can be read.
    return {
      points: game.steps.find((step) => step.rally.id === rallyId)?.points ?? [],
      overtake: game.steps.find((step) => step.rally.id === rallyId + 1)?.leadChange ?? null,
    };
  }
  return { points: [], overtake: null };
}
