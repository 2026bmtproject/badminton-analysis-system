import type { RallyModel } from "../domain/models";
import { GAME_LABEL_WIDTH_PX, type LeadGame, type LeadModel, type LeadSide } from "./scoreLead";
import { timeToPercent, type TimelineViewport } from "./timeline";

/** Who took the point; null when the scoreboard does not say. */
export type RallyWinner = LeadSide | null;

export type RallyOutcome = { rally: RallyModel; game: number | null; winner: RallyWinner };

/** `game`: the next Rally opens a new game; `interval`: the 11-point technical interval; `long`: anything else over the threshold. */
export type RallyBreakKind = "game" | "interval" | "long";
export type RallyBreak = { kind: RallyBreakKind; afterRallyId: number; start: number; end: number };

export type RallyLaneModel = {
  breaks: RallyBreak[];
  /** 95th-percentile duration; bars reach full height here and longer ones are capped. */
  durationCap: number;
};

/** The technical interval is 60 s; anything shorter after 10→11 is just play resuming. */
export const INTERVAL_MIN_GAP_SEC = 30;
/** Normal gaps on real matches run 13–25 s; past this a replay, challenge or stoppage happened. */
export const LONG_GAP_SEC = 45;
const INTERVAL_POINTS = 11;
const DURATION_CAP_QUANTILE = 0.95;
/** Shortest bar as a share of full height, so a 4 s rally is still visible. */
export const MIN_BAR_FRACTION = 0.2;

/** Rallies of one game in start order; the lead model owns game grouping. */
function gameRallies(game: LeadGame) {
  return [...game.steps.map((step) => step.rally), ...game.gaps.map((gap) => gap.rally)].sort(
    (a, b) => a.start - b.start || a.id - b.id,
  );
}

/**
 * A Rally's score is the pre-Rally score, so its winner is the side the next
 * Rally of the same game shows exactly one point ahead. The final Rally of a
 * game takes the game winner the lead model already resolved. Multi-point
 * segments stay unresolved.
 */
function gameOutcomes(game: LeadGame): RallyOutcome[] {
  const rallies = gameRallies(game);
  return rallies.map((rally, index) => {
    const next = rallies[index + 1];
    let winner: RallyWinner = null;
    if (rally.multi) winner = null;
    else if (!next) winner = game.winner;
    else if (rally.score && next.score) {
      const a = next.score[0] - rally.score[0];
      const b = next.score[1] - rally.score[1];
      winner = a === 1 && b === 0 ? "a" : a === 0 && b === 1 ? "b" : null;
    }
    return { rally, game: game.game, winner };
  });
}

/** Every Rally in start order with its game and winner; Rallies outside any scored game get neither. */
export function rallyOutcomes(rallies: RallyModel[], lead: LeadModel): RallyOutcome[] {
  const known = new Map(lead.games.flatMap(gameOutcomes).map((outcome) => [outcome.rally.id, outcome]));
  return [...rallies]
    .sort((a, b) => a.start - b.start || a.id - b.id)
    .map((rally) => known.get(rally.id) ?? { rally, game: null, winner: null });
}

/** The winner of one Rally, resolving only the game that holds it. */
export function rallyWinner(lead: LeadModel, rallyId: number): RallyWinner {
  for (const game of lead.games) {
    if (![...game.steps, ...game.gaps].some((entry) => entry.rally.id === rallyId)) continue;
    return gameOutcomes(game).find((outcome) => outcome.rally.id === rallyId)?.winner ?? null;
  }
  return null;
}

function quantile(values: number[], q: number) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)]!;
}

export function rallyLaneModel(rallies: RallyModel[], lead: LeadModel): RallyLaneModel {
  const outcomes = rallyOutcomes(rallies, lead);
  const breaks: RallyBreak[] = [];
  outcomes.forEach((current, index) => {
    const next = outcomes[index + 1];
    if (!next) return;
    const start = current.rally.end;
    const end = next.rally.start;
    const gap = end - start;
    const before = current.rally.score;
    const after = next.rally.score;
    let kind: RallyBreakKind | null = null;
    if (current.game !== null && next.game !== null && current.game !== next.game) kind = "game";
    else if (
      current.game === next.game &&
      before &&
      after &&
      Math.max(...before) === INTERVAL_POINTS - 1 &&
      Math.max(...after) === INTERVAL_POINTS &&
      gap > INTERVAL_MIN_GAP_SEC
    ) kind = "interval";
    else if (gap > LONG_GAP_SEC) kind = "long";
    if (kind) breaks.push({ kind, afterRallyId: current.rally.id, start, end });
  });
  return {
    breaks,
    durationCap: quantile(rallies.map((rally) => rally.duration), DURATION_CAP_QUANTILE),
  };
}

/** Bar height as a share of full height: sqrt keeps short rallies readable, the cap keeps one marathon from flattening the rest. */
export function rallyBarFraction(duration: number, cap: number) {
  if (!(cap > 0)) return 1;
  return Math.min(1, Math.max(MIN_BAR_FRACTION, Math.sqrt(Math.max(0, duration) / cap)));
}

/**
 * Highlight places, best first; equal scores share a place (1, 2, 2, 4).
 * Rallies without a highlight score are left unranked.
 */
export function highlightRanks(rallies: RallyModel[]): Map<number, number> {
  const scored = rallies
    .filter((rally): rally is RallyModel & { highlight: number } => rally.highlight !== null)
    .sort((a, b) => b.highlight - a.highlight || a.id - b.id);
  const ranks = new Map<number, number>();
  scored.forEach((rally, index) => {
    const previous = scored[index - 1];
    ranks.set(rally.id, previous && previous.highlight === rally.highlight ? ranks.get(previous.id)! : index + 1);
  });
  return ranks;
}

export function breakAt(model: RallyLaneModel, timeSec: number) {
  return model.breaks.find((item) => timeSec >= item.start && timeSec < item.end) ?? null;
}

export const RALLY_BREAK_LABELS: Record<RallyBreakKind, string> = {
  game: "局間休息",
  interval: "技術暫停",
  long: "長間隔",
};

export type RallyLaneMarks = {
  /**
   * `wide`: a zoomed-in bar that would read as a solid slab, drawn as a tint instead.
   * `splits`: where a multi-point segment's score changed, in percent; empty until the bar has room.
   */
  bars: { rallyId: number; x: number; width: number; fraction: number; wide: boolean; splits: number[] }[];
  /** Rallies whose score or game needs review, at the Rally centre. */
  flags: { rallyId: number; x: number }[];
};

const MIN_BAR_PX = 1.5;
const WIDE_BAR_PX = 48;
/** Below this a split line would just blot out a sliver of bar. */
const SPLIT_MIN_BAR_PX = 12;

/** A score the scoreboard could not read, or a game the sources disagree on. */
export function needsReview(rally: RallyModel) {
  return rally.scoreIssue !== undefined || rally.gameConflict !== undefined;
}

/**
 * Discrete marks in Timeline percent space. Neutral by design: nothing here
 * says who won a point, so the lane never spoils a match being watched.
 * Breaks are not drawn either; hovering a gap names it.
 */
export function rallyLaneMarks(
  model: RallyLaneModel,
  rallies: RallyModel[],
  view: TimelineViewport,
  trackWidth: number,
): RallyLaneMarks {
  const percent = (timeSec: number) => timeToPercent(timeSec, view);
  const px = (percentValue: number) => (percentValue / 100) * trackWidth;
  const visible = (start: number, end: number) => end >= view.startSec && start <= view.endSec;
  const minBarPercent = (MIN_BAR_PX / Math.max(1, trackWidth)) * 100;
  const marks: RallyLaneMarks = { bars: [], flags: [] };
  for (const rally of rallies) {
    if (!visible(rally.start, rally.end)) continue;
    const x = percent(rally.start);
    const barPx = px(percent(rally.end) - x);
    marks.bars.push({
      rallyId: rally.id,
      x,
      width: Math.max(minBarPercent, percent(rally.end) - x),
      fraction: rallyBarFraction(rally.duration, model.durationCap),
      wide: barPx > WIDE_BAR_PX,
      splits:
        rally.multi && barPx >= SPLIT_MIN_BAR_PX
          ? rally.splits.filter((timeSec) => timeSec > rally.start && timeSec < rally.end).map(percent)
          : [],
    });
    if (needsReview(rally)) marks.flags.push({ rallyId: rally.id, x: percent((rally.start + rally.end) / 2) });
  }
  return marks;
}

/** Room one "014" index needs; strides only ever use these steps so labels land on round numbers. */
const INDEX_SLOT_PX = 32;
const INDEX_STRIDES = [1, 2, 5];
/** Half a label plus breathing room, so a label pinned at the view edge is never clipped. */
const INDEX_EDGE_PX = 14;
/** Half of an arrowed "‹ 014" plus breathing room. */
const PINNED_EDGE_PX = 22;

/** `pinned`: the selected Rally lies off this edge of the view; its number waits there with an arrow. */
export type RallyIndexLabel = {
  rallyId: number;
  x: number;
  text: string;
  selected: boolean;
  pinned: "start" | "end" | null;
};

/**
 * Rally numbers for a zoomed-in lane. The stride depends on zoom alone, never
 * on scroll position, so labels do not flicker while playback scrolls. Each
 * label is locked to its Rally's centre and pinned inside the visible part
 * only once that centre leaves the view. The selected Rally is always
 * numbered, pinned to the nearer edge when it is out of view (a playing
 * lens spends whole gaps with no Rally in it), and other labels yield to it
 * and to game labels.
 */
export function rallyIndexLabels(
  rallies: RallyModel[],
  view: TimelineViewport,
  trackWidth: number,
  selectedId: number | null,
  gameLabels: { x: number }[],
): RallyIndexLabel[] {
  if (!rallies.length) return [];
  const pxPerSec = trackWidth / view.durationSec;
  const first = rallies[0]!;
  const last = rallies.at(-1)!;
  const spacingPx =
    rallies.length > 1 ? ((last.start - first.start) / (rallies.length - 1)) * pxPerSec : Number.POSITIVE_INFINITY;
  const stride = INDEX_STRIDES.find((step) => step * spacingPx >= INDEX_SLOT_PX) ?? null;
  const edgeSec = INDEX_EDGE_PX / pxPerSec;
  const anchor = (rally: RallyModel) => {
    const low = Math.max(rally.start, view.startSec + edgeSec);
    const high = Math.min(rally.end, view.endSec - edgeSec);
    const centre = (rally.start + rally.end) / 2;
    return low <= high ? Math.min(high, Math.max(low, centre)) : centre;
  };
  const inView = (timeSec: number) => timeSec >= view.startSec && timeSec <= view.endSec;
  const label = (rally: RallyModel, selected: boolean): RallyIndexLabel => ({
    rallyId: rally.id,
    x: timeToPercent(anchor(rally), view),
    text: String(rally.id + 1).padStart(3, "0"),
    selected,
    pinned: null,
  });
  const selectedRally = rallies.find((rally) => rally.id === selectedId);
  const labels: RallyIndexLabel[] = [];
  if (selectedRally) {
    const selected = label(selectedRally, true);
    const pinnedEdgeSec = PINNED_EDGE_PX / pxPerSec;
    if (selectedRally.end < view.startSec)
      labels.push({ ...selected, x: timeToPercent(view.startSec + pinnedEdgeSec, view), text: `‹ ${selected.text}`, pinned: "start" });
    else if (selectedRally.start > view.endSec)
      labels.push({ ...selected, x: timeToPercent(view.endSec - pinnedEdgeSec, view), text: `${selected.text} ›`, pinned: "end" });
    else labels.push(selected);
  }
  if (stride === null) return labels;
  // Rally spacing is uneven, so the stride alone does not guarantee room.
  const taken = labels.map((item) => (item.x / 100) * trackWidth);
  const games = gameLabels.map((item) => (item.x / 100) * trackWidth);
  for (const rally of rallies) {
    if (rally.id === selectedId || (rally.id + 1) % stride !== 0) continue;
    const timeSec = anchor(rally);
    if (!inView(timeSec)) continue;
    const px = (timeToPercent(timeSec, view) / 100) * trackWidth;
    if (taken.some((other) => Math.abs(other - px) < INDEX_SLOT_PX)) continue;
    if (games.some((left) => px + INDEX_SLOT_PX / 2 > left && px - INDEX_SLOT_PX / 2 < left + GAME_LABEL_WIDTH_PX)) continue;
    labels.push(label(rally, false));
    taken.push(px);
  }
  return labels;
}
