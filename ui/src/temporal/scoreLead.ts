import type { RallyModel, ScoreModel } from "../domain/models";
import { timeToPercent, type TimelineViewport } from "./timeline";

export type LeadSide = "a" | "b";

/** One scoreboard observation held from its Rally start until the next Rally of the same game starts. */
export type LeadStep = {
  rally: RallyModel;
  game: number;
  start: number;
  end: number;
  score: ScoreModel;
  /** Player A minus player B; positive means A leads. */
  lead: number;
  /** The game assignment disagrees between sources. */
  uncertain: boolean;
  /** The side that takes the lead from the other side at this observation. */
  leadChange: LeadSide | null;
  points: { side: LeadSide; match: boolean }[];
};

/** A Rally without a scoreboard observation; drawn dashed at the last observed lead. */
export type LeadGap = { rally: RallyModel; start: number; end: number; lead: number };

export type LeadGame = {
  game: number;
  start: number;
  end: number;
  steps: LeadStep[];
  gaps: LeadGap[];
  peak: LeadStep | null;
  /** Known only when the game's final Rally was observed with exactly one side at game point. */
  winner: LeadSide | null;
};

export type LeadModel = { games: LeadGame[]; maxAbsLead: number };

const GAME_POINTS = 21;
const GAME_CAP = 30;
const GAMES_TO_WIN = 2;

function sign(value: number) {
  return value > 0 ? 1 : value < 0 ? -1 : 0;
}

/** Sides that win the game by taking the next point, from one pre-Rally observation. */
export function gamePointSides(score: ScoreModel): LeadSide[] {
  const wins = (own: number, other: number) =>
    own + 1 >= GAME_CAP || (own + 1 >= GAME_POINTS && own + 1 - other >= 2);
  const sides: LeadSide[] = [];
  if (wins(score[0], score[1])) sides.push("a");
  if (wins(score[1], score[0])) sides.push("b");
  return sides;
}

function effectiveGames(rallies: RallyModel[]) {
  const groups: { game: number; rallies: RallyModel[] }[] = [];
  let previousScore: ScoreModel | null = null;
  for (const rally of rallies) {
    const current = groups.at(-1);
    let game = current?.game ?? 0;
    if (rally.game !== null) game = rally.game;
    else if (
      rally.score &&
      previousScore &&
      rally.score[0] + rally.score[1] < previousScore[0] + previousScore[1]
    ) game += 1;
    if (!current || current.game !== game) groups.push({ game, rallies: [rally] });
    else current.rallies.push(rally);
    if (rally.score) previousScore = rally.score;
  }
  return groups;
}

export function scoreLeadModel(rallies: RallyModel[]): LeadModel {
  const ordered = [...rallies].sort((a, b) => a.start - b.start || a.id - b.id);
  const games: LeadGame[] = [];
  const winners: (LeadSide | null)[] = [];
  let maxAbsLead = 0;
  for (const group of effectiveGames(ordered)) {
    const steps: LeadStep[] = [];
    const gaps: LeadGap[] = [];
    // Every game opens at 0:0, so a leading gap holds a tie.
    let lastLead = 0;
    let lastLeader = 0;
    let peak: LeadStep | null = null;
    for (const [index, rally] of group.rallies.entries()) {
      const end = group.rallies[index + 1]?.start ?? rally.end;
      if (!rally.score) {
        gaps.push({ rally, start: rally.start, end, lead: lastLead });
        continue;
      }
      const lead = rally.score[0] - rally.score[1];
      const leader = sign(lead);
      const step: LeadStep = {
        rally,
        game: group.game,
        start: rally.start,
        end,
        score: rally.score,
        lead,
        uncertain: rally.gameConflict !== undefined,
        leadChange:
          leader !== 0 && lastLeader !== 0 && leader !== lastLeader
            ? leader > 0 ? "a" : "b"
            : null,
        points: gamePointSides(rally.score).map((side) => {
          const won = winners.filter((winner) => winner === side).length;
          const unknown = winners.some((winner) => winner === null);
          return {
            side,
            match: group.game >= GAMES_TO_WIN || (!unknown && won === GAMES_TO_WIN - 1),
          };
        }),
      };
      steps.push(step);
      if (!peak || Math.abs(lead) > Math.abs(peak.lead)) peak = step;
      maxAbsLead = Math.max(maxAbsLead, Math.abs(lead));
      lastLead = lead;
      if (leader !== 0) lastLeader = leader;
    }
    if (!steps.length) continue;
    const final = group.rallies.at(-1)!;
    const closing = final.score ? gamePointSides(final.score) : [];
    const winner = closing.length === 1 ? closing[0]! : null;
    winners.push(winner);
    games.push({
      game: group.game,
      start: group.rallies[0]!.start,
      end: final.end,
      steps,
      gaps,
      peak: peak && peak.lead !== 0 ? peak : null,
      winner,
    });
  }
  return { games, maxAbsLead };
}

/** Steps and gaps cover a game continuously; returns whichever holds `timeSec`. */
export function leadEntryAt(model: LeadModel, timeSec: number): LeadStep | LeadGap | null {
  for (const game of model.games) {
    if (timeSec < game.start || timeSec > game.end) continue;
    for (const entry of [...game.steps, ...game.gaps]) {
      if (timeSec >= entry.start && timeSec < entry.end) return entry;
    }
    const last = [...game.steps, ...game.gaps].find((entry) => entry.end === game.end);
    if (last) return last;
  }
  return null;
}

/** Leads within ±LINEAR_LIMIT stay linear; anything larger is compressed into the outer band. */
export const LEAD_LINEAR_LIMIT = 6;
export const LEAD_LINEAR_SHARE = 0.7;
export const LEAD_MIN_DOMAIN = 4;
/** Half-height used by the lead, in viewBox units around the centre line at 50. */
export const LEAD_Y_EXTENT = 40;

export function leadDomain(maxAbsLead: number) {
  return Math.max(LEAD_MIN_DOMAIN, maxAbsLead);
}

/** Signed position in [-1, 1] for a lead under a match-wide symmetric domain. */
export function leadFraction(lead: number, domain: number) {
  const magnitude = Math.min(Math.abs(lead), domain);
  let fraction: number;
  if (domain <= LEAD_LINEAR_LIMIT) fraction = magnitude / domain;
  else if (magnitude <= LEAD_LINEAR_LIMIT) fraction = (LEAD_LINEAR_SHARE * magnitude) / LEAD_LINEAR_LIMIT;
  else
    fraction =
      LEAD_LINEAR_SHARE +
      ((1 - LEAD_LINEAR_SHARE) * (magnitude - LEAD_LINEAR_LIMIT)) / (domain - LEAD_LINEAR_LIMIT);
  return sign(lead) * fraction;
}

export function leadY(lead: number, domain: number) {
  return 50 - leadFraction(lead, domain) * LEAD_Y_EXTENT;
}

/** Faint reference leads; ±5 sits in the linear band, ±10 marks the compressed band. */
export function leadGridlines(domain: number) {
  return [5, 10].filter((lead) => lead < domain);
}

export type LeadGamePaths = {
  game: number;
  line: string;
  dashed: string;
  areaA: string;
  areaB: string;
};

const clampX = (value: number) => Math.min(101, Math.max(-1, value));

/** SVG paths in a 0–100 viewBox; x follows the Timeline viewport, steps outside it are culled. */
export function leadGamePaths(game: LeadGame, view: TimelineViewport, domain: number): LeadGamePaths {
  const visible = (entry: { start: number; end: number }) =>
    entry.end >= view.startSec && entry.start <= view.endSec;
  const x = (timeSec: number) => clampX(timeToPercent(timeSec, view)).toFixed(3);
  const y = (lead: number, clip = (value: number) => value) => clip(leadY(lead, domain)).toFixed(3);
  const entries = [
    ...game.steps.map((step) => ({ kind: "step" as const, start: step.start, end: step.end, lead: step.lead })),
    ...game.gaps.map((gap) => ({ kind: "gap" as const, start: gap.start, end: gap.end, lead: gap.lead })),
  ].sort((a, b) => a.start - b.start);
  let line = "";
  let dashed = "";
  let areaA = "";
  let areaB = "";
  let run: typeof entries = [];
  const flushArea = () => {
    if (!run.length) return;
    const open = `M ${x(run[0]!.start)} 50`;
    const edge = (clip: (value: number) => number) =>
      run.map((entry) => `V ${y(entry.lead, clip)} H ${x(entry.end)}`).join(" ");
    areaA += `${open} ${edge((value) => Math.min(value, 50))} V 50 Z `;
    areaB += `${open} ${edge((value) => Math.max(value, 50))} V 50 Z `;
    run = [];
  };
  let previous: (typeof entries)[number] | null = null;
  for (const entry of entries) {
    if (!visible(entry)) {
      flushArea();
      previous = entry;
      continue;
    }
    if (entry.kind === "gap") {
      flushArea();
      dashed += `M ${x(entry.start)} ${y(entry.lead)} H ${x(entry.end)} `;
    } else {
      const joined = previous?.kind === "step" && run.length > 0;
      line += joined
        ? `V ${y(entry.lead)} H ${x(entry.end)} `
        : `M ${x(entry.start)} ${y(previous ? previous.lead : entry.lead)} V ${y(entry.lead)} H ${x(entry.end)} `;
      run.push(entry);
    }
    previous = entry;
  }
  flushArea();
  return { game: game.game, line: line.trim(), dashed: dashed.trim(), areaA: areaA.trim(), areaB: areaB.trim() };
}

export type LeadChartMarks = {
  separators: { game: number; x: number }[];
  labels: { game: number; x: number; text: string }[];
  changes: { rallyId: number; x: number; side: LeadSide }[];
  /** `direction` is where the label sits relative to the line: away from zero unless the line hugs the lane edge. */
  /** `align: "end"` puts the label left of a peak that sits at the end of its game. */
  peaks: { rallyId: number; x: number; y: number; text: string; side: LeadSide; direction: "up" | "down"; align: "start" | "end" }[];
  chips: { rallyId: number; x: number; score: ScoreModel; half: "upper" | "lower" }[];
  uncertain: { rallyId: number; x: number; y: number }[];
};

const GAME_LABEL_MIN_PX = 28;
/** Room a "G2" label takes from its left edge, for marks that must keep clear of it. */
export const GAME_LABEL_WIDTH_PX = 20;
const PEAK_LABEL_MIN_LEAD = 3;
const PEAK_LABEL_MIN_GAME_PX = 80;
/** Beyond this fraction the line is too close to the lane edge for a label outside it. */
const PEAK_OUTER_MAX_FRACTION = 0.6;
const SCORE_CHIP_MIN_PX = 56;
const PEAK_LABEL_ROOM_PX = 28;
/** Half a chip plus breathing room; a chip pinned this far in never touches the lane edge. */
const SCORE_CHIP_EDGE_PX = 30;

/** Discrete marks in Timeline percent space; only marks inside the viewport are returned. */
export function leadChartMarks(
  model: LeadModel,
  view: TimelineViewport,
  domain: number,
  trackWidth: number,
): LeadChartMarks {
  const marks: LeadChartMarks = { separators: [], labels: [], changes: [], peaks: [], chips: [], uncertain: [] };
  const percent = (timeSec: number) => timeToPercent(timeSec, view);
  const inView = (timeSec: number) => timeSec >= view.startSec && timeSec <= view.endSec;
  const chipEdgeSec = (SCORE_CHIP_EDGE_PX / Math.max(1, trackWidth)) * view.durationSec;
  const pixels = (startSec: number, endSec: number) =>
    ((Math.min(endSec, view.endSec) - Math.max(startSec, view.startSec)) / view.durationSec) * trackWidth;
  model.games.forEach((game, index) => {
    if (game.end < view.startSec || game.start > view.endSec) return;
    if (index > 0 && inView(game.start)) marks.separators.push({ game: game.game, x: percent(game.start) });
    if (pixels(game.start, game.end) >= GAME_LABEL_MIN_PX)
      marks.labels.push({ game: game.game, x: Math.max(0, percent(game.start)), text: `G${game.game + 1}` });
    for (const step of game.steps) {
      if (!inView(step.start)) continue;
      if (step.uncertain) marks.uncertain.push({ rallyId: step.rally.id, x: percent(step.start), y: leadY(step.lead, domain) });
      if (step.leadChange) marks.changes.push({ rallyId: step.rally.id, x: percent(step.start), side: step.leadChange });
    }
    for (const step of game.steps) {
      if (step.end < view.startSec || step.start > view.endSec) continue;
      if (pixels(step.start, step.end) < SCORE_CHIP_MIN_PX) continue;
      // Locked to the step centre so it scrolls with the line; only when that
      // centre leaves the view is it pinned to the edge. Centring on the visible
      // part instead would slide it at half speed while playback scrolls.
      const low = Math.max(step.start, view.startSec + chipEdgeSec);
      const high = Math.min(step.end, view.endSec - chipEdgeSec);
      const centre = (step.start + step.end) / 2;
      marks.chips.push({
        rallyId: step.rally.id,
        x: percent(low <= high ? Math.min(high, Math.max(low, centre)) : (low + high) / 2),
        score: step.score,
        half: step.lead < 0 ? "upper" : "lower",
      });
    }
    const peak = game.peak;
    if (
      peak &&
      Math.abs(peak.lead) >= PEAK_LABEL_MIN_LEAD &&
      inView(peak.start) &&
      pixels(game.start, game.end) >= PEAK_LABEL_MIN_GAME_PX
    ) {
      marks.peaks.push({
        rallyId: peak.rally.id,
        x: percent(peak.start),
        y: leadY(peak.lead, domain),
        text: `+${Math.abs(peak.lead)}`,
        side: peak.lead > 0 ? "a" : "b",
        direction:
          (peak.lead > 0) === (Math.abs(leadFraction(peak.lead, domain)) <= PEAK_OUTER_MAX_FRACTION) ? "up" : "down",
        align: pixels(peak.start, game.end) < PEAK_LABEL_ROOM_PX ? "end" : "start",
      });
    }
  });
  return marks;
}
