import type { RallyModel, StrokeModel } from "../domain/models";
import { timeToPercent, type TimelineViewport } from "./timeline";

/** Shot families, coloured by their own tokens: never the A/B colours, which mean a player's side. */
export type StrokeFamily = "attack" | "net" | "transition" | "serve" | "unknown";

/** Bottom to top in a Rally's composition bar. */
export const STROKE_STACK_ORDER: StrokeFamily[] = ["serve", "transition", "net", "attack", "unknown"];

const SHOTS: Record<string, { family: StrokeFamily; glyph: string }> = {
  殺球: { family: "attack", glyph: "殺" },
  撲球: { family: "attack", glyph: "撲" },
  小球: { family: "net", glyph: "小" },
  勾球: { family: "net", glyph: "勾" },
  切球: { family: "net", glyph: "切" },
  高遠球: { family: "transition", glyph: "高" },
  平快球: { family: "transition", glyph: "平" },
  發球: { family: "serve", glyph: "發" },
};

export function strokeFamily(type: string | null): StrokeFamily {
  return (type !== null && SHOTS[type]?.family) || "unknown";
}

/** One character per shot, so a label fits beside every vertex once zoomed in. */
export function strokeGlyph(type: string | null) {
  return (type !== null && SHOTS[type]?.glyph) || "?";
}

/** A net shot still visibly crosses the net line. */
export const MIN_DRAWN_DEPTH = 0.15;
/** Without a measured position the hitter is drawn mid-court and flagged. */
const FALLBACK_DEPTH = 0.5;

/**
 * Distance from the net, 0 at the net and 1 at the back line.
 * `court_normalized_v1` runs y along the court with the net at 0.5 (checked
 * on ASG_vs_AA_2020: top hitters sit below 0.5, bottom hitters above).
 * Anything but a measured position falls back to mid-court, marked estimated.
 */
export function strokeDepth(stroke: StrokeModel): { depth: number; estimated: boolean } {
  if (!stroke.courtPosition || stroke.positionQuality !== "measured")
    return { depth: FALLBACK_DEPTH, estimated: true };
  return { depth: Math.min(1, Math.max(0, Math.abs(stroke.courtPosition.y - 0.5) * 2)), estimated: false };
}

/**
 * Signed height in [-1, 1]: the hitter at the top of the video above the net
 * line, the one at the bottom below, so the trace reads like the picture
 * beside it. That follows the court side (`hitterSide`), which swaps at every
 * change of ends; the hover text names the player. An unknown side sits on
 * the net line.
 */
export function strokeLevel(stroke: StrokeModel) {
  if (!stroke.hitterSide) return 0;
  const { depth } = strokeDepth(stroke);
  return (stroke.hitterSide === "top" ? 1 : -1) * Math.max(MIN_DRAWN_DEPTH, depth);
}

export const STROKE_FAMILY_LABELS: Record<StrokeFamily, string> = {
  attack: "進攻",
  net: "網前",
  transition: "過渡",
  serve: "發球",
  unknown: "未知",
};

/** Strokes per family; empty families are left out. */
export function strokeFamilyCounts(hits: StrokeModel[]): Partial<Record<StrokeFamily, number>> {
  const counts: Partial<Record<StrokeFamily, number>> = {};
  for (const hit of hits) {
    const family = strokeFamily(hit.type);
    counts[family] = (counts[family] ?? 0) + 1;
  }
  return counts;
}

/** Each family's share of a Rally's strokes; empty families are left out. */
export function strokeComposition(hits: StrokeModel[]): Partial<Record<StrokeFamily, number>> {
  const counts = strokeFamilyCounts(hits);
  for (const family of Object.keys(counts) as StrokeFamily[]) counts[family]! /= hits.length;
  return counts;
}

/** Median time between consecutive strokes of a Rally, match-wide so the zoom thresholds never depend on scroll. */
export function strokeSpacingSec(rallies: RallyModel[]) {
  const gaps = rallies
    .flatMap((rally) => (rally.hits ?? []).slice(1).map((hit, index) => hit.time - rally.hits![index]!.time))
    .filter((gap) => gap > 0)
    .sort((a, b) => a - b);
  return gaps.length ? gaps[Math.floor(gaps.length / 2)]! : 1;
}

/** Below this many pixels between strokes the trace is a scribble, so each Rally is summarised as a bar. */
export const TRACE_MIN_SPACING_PX = 6;
const MIN_BAR_PX = 1.5;
/** Past this a solid stack reads as a slab, so it is drawn as a tint, like the rally lane. */
const WIDE_BAR_PX = 48;
/** Room for one glyph; every zoom level, the single-Rally view included, labels with glyphs alone. */
const GLYPH_SLOT_PX = 16;
const LABEL_STRIDES = [1, 2, 5, 10];

export type StrokeBar = {
  rallyId: number;
  x: number;
  width: number;
  wide: boolean;
  /** Fractions of the bar height, bottom up in `STROKE_STACK_ORDER`. */
  segments: { family: StrokeFamily; from: number; to: number }[];
};

export type StrokeVertex = {
  eventIndex: number;
  x: number;
  level: number;
  family: StrokeFamily;
  estimated: boolean;
};

/** `dashed`: an end has an estimated depth, so the height of the swing is a guess. */
export type StrokeLink = { from: number; to: number; x1: number; y1: number; x2: number; y2: number; dashed: boolean };

/** `row`: the video side of the hitter, above or below the net line, so the text alone says which end hit. */
export type StrokeLabel = { eventIndex: number; x: number; row: "top" | "bottom"; text: string; selected: boolean };

export type StrokeLaneMarks =
  | { mode: "bars"; bars: StrokeBar[]; markers: { eventIndex: number; x: number }[] }
  | { mode: "trace"; links: StrokeLink[]; vertices: StrokeVertex[]; labels: StrokeLabel[] };

export type StrokeLaneOptions = {
  spacingSec: number;
  /** Always labelled, and the only markers left in the bar view. */
  focusEventIndices: number[];
};

/**
 * Stroke lane marks in Timeline percent space. Zoomed out, each Rally is one
 * 100% stacked bar of its shot families; once strokes are far enough apart it
 * becomes a rally trace: one vertex per stroke, on the hitter's side of the
 * net line as the video shows it, as far out as the hitter stood from the net.
 */
export function strokeLaneMarks(
  rallies: RallyModel[],
  view: TimelineViewport,
  trackWidth: number,
  options: StrokeLaneOptions,
): StrokeLaneMarks {
  const percent = (timeSec: number) => timeToPercent(timeSec, view);
  const pxPerSec = Math.max(1, trackWidth) / view.durationSec;
  const visible = rallies.filter((rally) => rally.hits?.length && rally.end >= view.startSec && rally.start <= view.endSec);
  const spacingPx = options.spacingSec * pxPerSec;
  if (spacingPx < TRACE_MIN_SPACING_PX) {
    const minBarPercent = (MIN_BAR_PX / Math.max(1, trackWidth)) * 100;
    const bars = visible.map((rally): StrokeBar => {
      const x = percent(rally.start);
      const width = percent(rally.end) - x;
      const shares = strokeComposition(rally.hits!);
      let from = 0;
      const segments = STROKE_STACK_ORDER.flatMap((family) => {
        const share = shares[family];
        if (!share) return [];
        const segment = { family, from, to: from + share };
        from += share;
        return [segment];
      });
      return { rallyId: rally.id, x, width: Math.max(minBarPercent, width), wide: (width / 100) * trackWidth > WIDE_BAR_PX, segments };
    });
    const markers = visible
      .flatMap((rally) => rally.hits!)
      .filter((hit) => options.focusEventIndices.includes(hit.eventIndex))
      .map((hit) => ({ eventIndex: hit.eventIndex, x: percent(hit.time) }));
    return { mode: "bars", bars, markers };
  }

  const links: StrokeLink[] = [];
  const vertices: StrokeVertex[] = [];
  const inView = (timeSec: number) => timeSec >= view.startSec && timeSec <= view.endSec;
  for (const rally of visible) {
    const hits = rally.hits!;
    hits.forEach((hit, index) => {
      const { estimated } = strokeDepth(hit);
      const vertex: StrokeVertex = {
        eventIndex: hit.eventIndex,
        x: percent(hit.time),
        level: strokeLevel(hit),
        family: strokeFamily(hit.type),
        estimated: estimated && hit.hitterSide != null,
      };
      vertices.push(vertex);
      const previous = vertices.at(-2);
      if (index > 0 && previous)
        links.push({
          from: previous.eventIndex,
          to: vertex.eventIndex,
          x1: previous.x,
          y1: previous.level,
          x2: vertex.x,
          y2: vertex.level,
          dashed: previous.estimated || vertex.estimated,
        });
    });
  }

  // Each side has its own label row, so neighbours on a row are usually two strokes apart.
  const slot = GLYPH_SLOT_PX;
  const stride = LABEL_STRIDES.find((step) => step * spacingPx * 2 >= slot) ?? null;
  const labels: StrokeLabel[] = [];
  const taken: Record<StrokeLabel["row"], number[]> = { top: [], bottom: [] };
  const edge = (percentValue: number) => {
    const px = (percentValue / 100) * trackWidth;
    return Math.min(trackWidth - slot / 2, Math.max(slot / 2, px));
  };
  const place = (hit: StrokeModel, selected: boolean) => {
    // A row would claim a side; an unknown one keeps only its vertex on the net line.
    if (!inView(hit.time) || !hit.hitterSide) return;
    const row = hit.hitterSide;
    const px = edge(percent(hit.time));
    if (!selected && taken[row].some((other) => Math.abs(other - px) < slot)) return;
    taken[row].push(px);
    labels.push({
      eventIndex: hit.eventIndex,
      x: (px / Math.max(1, trackWidth)) * 100,
      row,
      text: strokeGlyph(hit.type),
      selected,
    });
  };
  const hits = visible.flatMap((rally) => rally.hits!);
  for (const hit of hits) if (options.focusEventIndices.includes(hit.eventIndex)) place(hit, true);
  if (stride !== null)
    for (const hit of hits)
      if (!options.focusEventIndices.includes(hit.eventIndex) && (hit.ordinal - 1) % stride === 0) place(hit, false);
  return { mode: "trace", links, vertices, labels };
}
