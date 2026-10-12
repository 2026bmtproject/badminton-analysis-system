import type { RallyModel } from "../domain/models";
import FUN_ASSETS from "../assets/fun/fun-assets.json";

/**
 * Party modes: an easter egg behind Shift-clicking the overlay button, never stored, so a reload is
 * always serious again. Ported from tools/overlay_viewer.py's o / y keys, plus the steel pipe.
 */
export type FunMode = "off" | "oiia" | "nyan" | "pipe";

export const FUN_MODES: readonly { id: FunMode; label: string }[] = [
  { id: "off", label: "關" },
  { id: "oiia", label: "OIIA" },
  { id: "nyan", label: "Nyan" },
  { id: "pipe", label: "鋼管" },
];

/** The modes that draw something in place of the shuttle. */
export function replacesShuttle(mode: FunMode): mode is "oiia" | "nyan" {
  return mode === "oiia" || mode === "nyan";
}

// ── oiia ────────────────────────────────────────────────────────────────────
/** Cat height at a 1280-wide picture. */
export const OIIA_HEIGHT = 64;
/** The party beat; it follows video time, so pausing freezes it and 2x doubles it. */
export const OIIA_BPM = 128;
/** Seconds the lights keep going after the last hit, while the shuttle lands. */
export const OIIA_TAIL = 1;
/** One light colour per beat, in turn (RGB 0-255). */
export const OIIA_LIGHTS: readonly (readonly [number, number, number])[] = [
  [255, 60, 255], [40, 220, 255], [80, 255, 60], [255, 200, 40], [255, 80, 80], [60, 120, 255],
];
/** Video seconds the cat may vanish (tracking gaps) before its song stops and the next one starts over. */
export const OIIA_GRACE = 0.6;

export type OiiaSprite = typeof FUN_ASSETS.oiia;
export const OIIA = FUN_ASSETS.oiia;
export const NYAN_LOOP = FUN_ASSETS.nyan;

/**
 * The gif frame to show at a position in the oiia song: the cat spins through one of its two spins
 * while a voice burst plays, stretched to fit it, and stands still in the silences between them.
 */
export function oiiaFrame(position: number, sprite: OiiaSprite = OIIA) {
  const { bursts } = sprite.audio;
  const index = bursts.findIndex(([start, end]) => position >= start && position < end);
  if (index >= 0) {
    const [start, end] = bursts[index]!;
    const [first, last] = sprite.spins[index % sprite.spins.length]!;
    const progress = (position - start) / (end - start);
    return first + Math.min(last - first, Math.floor(progress * (last - first + 1)));
  }
  const [first, last] = sprite.stand;
  return first + Math.floor(Math.max(position, 0) * 30) % (last - first + 1);
}

/** Where the oiia song is after `elapsed` seconds of a cat's appearance; each appearance starts at the first voice. */
export function oiiaSongPosition(elapsed: number, sprite: OiiaSprite = OIIA) {
  const { loopStart, loopEnd } = sprite.audio;
  const length = loopEnd - loopStart;
  return loopStart + ((Math.max(elapsed, 0) % length) + length) % length;
}

/**
 * Tracks one appearance of the cat in video time. It starts when the cat first shows, survives tracking
 * gaps shorter than OIIA_GRACE, and ends on a seek, so the song always starts with the cat.
 */
export class CatAppearance {
  private start: number | null = null;
  private lastSeen = -Infinity;

  /** Feed every drawn frame; returns seconds since the appearance began, or null while there is none. */
  update(time: number, visible: boolean): number | null {
    if (this.start !== null && (time < this.lastSeen || time - this.lastSeen > OIIA_GRACE)) this.start = null;
    if (visible) {
      this.start ??= time;
      this.lastSeen = time;
    }
    return this.start === null ? null : time - this.start;
  }

  reset() {
    this.start = null;
    this.lastSeen = -Infinity;
  }
}

/** True from a Rally's first hit to OIIA_TAIL seconds after its last, but never past the Rally's end. */
export function partying(rally: Pick<RallyModel, "end" | "hits"> | null, time: number) {
  const hits = rally?.hits;
  if (!hits?.length) return false;
  return time >= hits[0]!.time && time <= Math.min(hits[hits.length - 1]!.time + OIIA_TAIL, rally!.end);
}

/**
 * The party light at a video time, as the tool's per-channel gain split in two: a multiply colour
 * (0-1, never brightens) and a brightness that lifts the brightest channel back above one.
 */
export function partyLight(time: number) {
  const beats = time * OIIA_BPM / 60;
  const beat = Math.floor(beats);
  const pulse = Math.exp(-4 * (beats - beat));                       // 1 on the beat, falling to 0.02
  const light = OIIA_LIGHTS[beat % OIIA_LIGHTS.length]!;
  const strength = 0.45 + 0.35 * pulse;
  const boost = 1.2 + 0.6 * pulse;
  const gain = light.map((channel) => 1 - strength + strength * (channel / 255) * boost);
  const brightness = Math.max(1, ...gain);
  return { multiply: gain.map((value) => value / brightness) as [number, number, number], brightness };
}

// ── nyan ────────────────────────────────────────────────────────────────────
/** Cat height at a 1280-wide picture; the pixel size follows from it. */
export const NYAN_HEIGHT = 60;
/** Frames of rainbow behind the cat. */
export const NYAN_TRAIL = 20;
/** Cells per colour band. */
export const NYAN_STRIPE = 2;
/** Cells between each up/down step of the rainbow's wave. */
export const NYAN_WAVE = 4;
/** Wave flips per second of video. */
export const NYAN_WIGGLE = 6;
/** Red, orange, yellow, green, blue, violet. */
export const NYAN_RAINBOW = ["#ff0000", "#ff9900", "#ffff00", "#33ff00", "#0099ff", "#6633ff"] as const;

/**
 * The rainbow along a run of screen points as grid cells of `unit` px, one list per colour band. Cells
 * align to the canvas origin, so the rainbow never shimmers as it moves; `tick` flips the wave.
 */
export function rainbowCells(points: readonly (readonly [number, number])[], unit: number, tick: number) {
  const bands = NYAN_RAINBOW.map(() => new Map<string, readonly [number, number]>());
  if (points.length < 2) return bands.map(() => []);
  const grid = points.map(([x, y]) => [x / unit, y / unit] as const);
  const along = [0];
  for (let i = 1; i < grid.length; i += 1) {
    along.push(along[i - 1]! + Math.hypot(grid[i]![0] - grid[i - 1]![0], grid[i]![1] - grid[i - 1]![1]));
  }
  const length = along[along.length - 1]!;
  if (length < 1) return bands.map(() => []);
  const total = NYAN_STRIPE * NYAN_RAINBOW.length;
  let segment = 1;
  // Half a cell apart, so diagonals never leave gaps.
  for (let s = 0; s < length; s += 0.5) {
    while (segment < grid.length - 1 && along[segment]! < s) segment += 1;
    const [ax, ay] = grid[segment - 1]!, [bx, by] = grid[segment]!;
    const span = along[segment]! - along[segment - 1]!;
    const t = span > 0 ? (s - along[segment - 1]!) / span : 0;
    const x = ax + (bx - ax) * t, y = ay + (by - ay) * t;
    const norm = Math.hypot(bx - ax, by - ay) || 1;
    const nx = -(by - ay) / norm, ny = (bx - ax) / norm;
    // Counted back from the cat's tail, so the wave steps stay put while the cat flies on.
    const wave = (Math.floor((length - s) / NYAN_WAVE) + tick) % 2 === 0 ? 0.5 : -0.5;
    NYAN_RAINBOW.forEach((_, band) => {
      for (let sub = 0; sub < NYAN_STRIPE; sub += 0.5) {
        const offset = band * NYAN_STRIPE + sub - total / 2 + wave;
        const cell = [Math.floor(x + nx * offset), Math.floor(y + ny * offset)] as const;
        bands[band]!.set(`${cell[0]},${cell[1]}`, cell);
      }
    });
  }
  return bands.map((cells) => [...cells.values()]);
}

// ── pipe ────────────────────────────────────────────────────────────────────
/**
 * Hits played through when the picture moved from `from` to `to`. Only forward steps no longer than
 * `maxStep` count, so seeks and Segment skips never set the pipe off.
 */
export function crossedHits(rallies: readonly Pick<RallyModel, "hits">[], from: number, to: number, maxStep: number) {
  if (!(to > from) || to - from > maxStep) return [];
  return rallies.flatMap((rally) => rally.hits ?? []).filter((hit) => hit.frame > from && hit.frame <= to);
}

/** Short, hard and small: rotate (deg) and shift (px) a little, decaying fast, with a zoom that hides the corners. */
export const PIPE_SHAKE: Keyframe[] = [
  { transform: "none" },
  { transform: "translate(-9px, 6px) rotate(-2.2deg) scale(1.06)", offset: 0.08 },
  { transform: "translate(8px, -5px) rotate(1.8deg) scale(1.05)", offset: 0.22 },
  { transform: "translate(-5px, 4px) rotate(-1.1deg) scale(1.035)", offset: 0.38 },
  { transform: "translate(3px, -2px) rotate(0.6deg) scale(1.02)", offset: 0.56 },
  { transform: "translate(-1px, 1px) rotate(-0.2deg) scale(1.008)", offset: 0.76 },
  { transform: "none" },
];
export const PIPE_SHAKE_MS = 320;
