import type { CheerWindowModel, RallyModel } from "../domain/models";
import { highlightRanks } from "./rallyOutcome";
import { GAME_LABEL_WIDTH_PX, type LeadModel, type LeadSide } from "./scoreLead";
import { timeToPercent, type TimelineViewport } from "./timeline";

/** A smoothed window at or above this probability counts as cheering. */
export const CHEER_THRESHOLD = 0.5;
/** How many highlight places the lane marks, match-wide. */
export const HIGHLIGHT_MARK_PLACES = 5;
/** Width of a highlight triangle, matching `.highlight-mark`. */
const HIGHLIGHT_MARK_WIDTH_PX = 18;
/** About two triangles: a game label moved further reads as loose text rather than the separator's. */
const CHEER_LABEL_MAX_SHIFT_PX = 40;

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

/** A highlight place marked on the lane, at the Rally's centre as a Timeline percent. */
export type HighlightMark = { rank: number; rallyId: number; x: number };

/**
 * One mark per Rally in the top highlight places, best first, numbered with the
 * place the Rally tooltip gives, so Rallies tied on a place share its number.
 * Marks centre on the whole Rally and overlap where they crowd instead of
 * moving, so each one always sits over its own Rally.
 */
export function highlightMarks(
  rallies: RallyModel[],
  view: TimelineViewport,
  places = HIGHLIGHT_MARK_PLACES,
): HighlightMark[] {
  const ranks = highlightRanks(rallies);
  return rallies
    .flatMap((rally) => {
      const rank = ranks.get(rally.id);
      if (rank === undefined || rank > places) return [];
      return [{ rank, rallyId: rally.id, x: timeToPercent((rally.start + rally.end) / 2, view) }];
    })
    .filter((mark) => mark.x >= 0 && mark.x <= 100)
    .sort((a, b) => a.rank - b.rank || a.rallyId - b.rallyId);
}

/**
 * Game labels moved right past any highlight triangle in their way. A triangle
 * cannot leave its Rally, so the label gives way, staying on its game's side
 * of the separator; pushed further than CHEER_LABEL_MAX_SHIFT_PX it would
 * float free of the separator, so it is dropped and the separator alone marks
 * the game.
 */
export function cheerGameLabels<T extends { x: number }>(
  labels: T[],
  marks: { x: number }[],
  trackWidth: number,
): T[] {
  if (trackWidth <= 0) return labels;
  const centres = marks.map((mark) => (mark.x / 100) * trackWidth).sort((a, b) => a - b);
  return labels.flatMap((label) => {
    const origin = (label.x / 100) * trackWidth;
    let left = origin;
    for (const centre of centres) {
      if (centre + HIGHLIGHT_MARK_WIDTH_PX / 2 > left && centre - HIGHLIGHT_MARK_WIDTH_PX / 2 < left + GAME_LABEL_WIDTH_PX)
        left = centre + HIGHLIGHT_MARK_WIDTH_PX / 2;
    }
    if (left - origin > CHEER_LABEL_MAX_SHIFT_PX) return [];
    return [{ ...label, x: (left / trackWidth) * 100 }];
  });
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
