import type { OverlayManifestModel, RallyModel, StrokeModel } from "../domain/models";
import { rallyAtOrBefore } from "../temporal/activeContext";
import { poseAt, shuttleAt, type OverlayChunk, type OverlayPoint, type OverlaySide } from "./overlayChunk";
import type { OverlayLayerId } from "./overlaySettings";

/** Shuttle trail length, in frames. */
export const TRAIL_FRAMES = 12;
/** Frames on either side of a hit that flash. */
export const HIT_FLASH_FRAMES = 3;
/** Frames a stroke label stays up after its hit. */
export const STROKE_HOLD_FRAMES = 14;

/** A colour role; the renderer maps it to a theme colour. `method:<name>` is one shuttle tracking method. */
export type OverlayTone = "court" | "neutral" | "a" | "b" | "hit" | `method:${string}`;

/** Positions are video pixels; widths and radii are screen pixels, so marks keep their size at any zoom. */
export type OverlayMark =
  | { kind: "line"; points: OverlayPoint[]; tone: OverlayTone; width: number; closed?: boolean; alpha?: number }
  | { kind: "circle"; center: OverlayPoint; radius: number; tone: OverlayTone; width: number | "fill" }
  | { kind: "label"; at: OverlayPoint | "video-bottom"; text: string; tone: OverlayTone; align: "start" | "center"; lift: number }
  | { kind: "border"; tone: OverlayTone; width: number };

export type OverlaySceneInput = {
  frame: number;
  chunk: OverlayChunk | null;
  manifest: Pick<OverlayManifestModel, "courtLines" | "skeleton" | "baseMethod"> | null;
  rally: RallyModel | null;
  layers: Record<OverlayLayerId, boolean>;
  /** Shuttle methods to draw. */
  methods: readonly string[];
  players: { a: string; b: string };
};

/** The frame shown at a presented frame's media time; frame n is presented at n / fps. */
export function frameAtMediaTime(mediaTime: number, fps: number) {
  return Math.max(0, Math.round(mediaTime * fps));
}

/** The frame on screen at a playback position, which shows the last frame that started at or before it. */
export function frameAtTime(time: number, fps: number) {
  return Math.max(0, Math.floor(time * fps + 0.01));
}

/** The Rally that owns a frame; the frame's midpoint keeps millisecond-rounded Rally bounds from losing edge frames. */
export function rallyAtFrame<T extends Pick<RallyModel, "start" | "end">>(rallies: readonly T[], frame: number, fps: number): T | null {
  const time = (frame + 0.5) / fps;
  const rally = rallyAtOrBefore(rallies, time);
  return rally && frame / fps <= rally.end ? rally : null;
}

/** Where `object-fit: contain` puts the picture inside its element. */
export function containedRect(box: { width: number; height: number }, media: { width: number; height: number }) {
  if (box.width <= 0 || box.height <= 0 || media.width <= 0 || media.height <= 0) return null;
  const scale = Math.min(box.width / media.width, box.height / media.height);
  const width = media.width * scale;
  const height = media.height * scale;
  return { x: (box.width - width) / 2, y: (box.height - height) / 2, width, height, scale };
}

/** The visible stretches of the shuttle's last frames; a gap in tracking breaks the trail. */
export function shuttleTrail(chunk: OverlayChunk | null, method: string, frame: number, length = TRAIL_FRAMES): OverlayPoint[][] {
  if (!chunk) return [];
  const runs: OverlayPoint[][] = [];
  let run: OverlayPoint[] = [];
  for (let at = Math.max(chunk.startFrame, frame - length); at <= frame; at += 1) {
    const point = shuttleAt(chunk, method, at);
    if (point) run.push(point);
    else { if (run.length > 1) runs.push(run); run = []; }
  }
  if (run.length > 1) runs.push(run);
  return runs;
}

/** The hit flashing at this frame, nearest first, and how far its ring has grown. */
export function hitFlash(hits: readonly StrokeModel[] | null | undefined, frame: number) {
  let nearest: StrokeModel | null = null;
  for (const hit of hits ?? []) {
    const distance = Math.abs(hit.frame - frame);
    if (distance <= HIT_FLASH_FRAMES && (!nearest || distance < Math.abs(nearest.frame - frame))) nearest = hit;
  }
  return nearest ? { hit: nearest, grow: 6 * (HIT_FLASH_FRAMES - Math.abs(nearest.frame - frame) + 1) } : null;
}

/** The latest hit at or before this frame whose label is still up. */
export function recentStroke(hits: readonly StrokeModel[] | null | undefined, frame: number) {
  let recent: StrokeModel | null = null;
  for (const hit of hits ?? []) {
    if (hit.frame > frame) break;
    recent = hit;
  }
  return recent && frame - recent.frame <= STROKE_HOLD_FRAMES ? recent : null;
}

const SIDES: readonly OverlaySide[] = ["top", "bottom"];
const SIDE_LABEL: Record<OverlaySide, string> = { top: "畫面上方", bottom: "畫面下方" };

/** Everything to draw at one frame. Without identity a player keeps a neutral colour and a court-side name. */
export function buildOverlayScene(input: OverlaySceneInput): OverlayMark[] {
  const { frame, chunk, manifest, rally, layers, players } = input;
  const marks: OverlayMark[] = [];
  const row = (side: OverlaySide | null | undefined) => (side && rally?.identity?.[side]) || null;

  if (layers.court && chunk?.court && manifest) {
    const court = chunk.court;
    for (const [from, to] of manifest.courtLines) {
      if (court[from] && court[to]) marks.push({ kind: "line", points: [court[from], court[to]], tone: "court", width: 1.5 });
    }
    for (const point of court) marks.push({ kind: "circle", center: point, radius: 2.5, tone: "court", width: "fill" });
  }

  for (const side of SIDES) {
    const pose = poseAt(chunk, side, frame);
    if (!pose) continue;
    const tone: OverlayTone = row(side) ?? "neutral";
    if (layers.pose) {
      if (pose.bbox) {
        const [x1, y1, x2, y2] = pose.bbox;
        marks.push({ kind: "line", points: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], tone, width: 1, closed: true, alpha: 0.6 });
      }
      const keypoints = pose.keypoints;
      if (keypoints && manifest) {
        for (const [from, to] of manifest.skeleton) {
          const a = keypoints[from], b = keypoints[to];
          if (a && b) marks.push({ kind: "line", points: [a, b], tone, width: 2 });
        }
        for (const point of keypoints) if (point) marks.push({ kind: "circle", center: point, radius: 2.5, tone, width: "fill" });
      }
    }
    if (layers.players && pose.bbox) {
      const owner = row(side);
      marks.push({ kind: "label", at: [pose.bbox[0], pose.bbox[1]], text: owner ? players[owner] : SIDE_LABEL[side],
        tone, align: "start", lift: 4 });
    }
  }

  if (layers.shuttle) {
    for (const method of input.methods) {
      const tone: OverlayTone = `method:${method}`;
      if (layers.trail) {
        for (const run of shuttleTrail(chunk, method, frame)) marks.push({ kind: "line", points: run, tone, width: 1.5 });
      }
      const point = shuttleAt(chunk, method, frame);
      if (point) {
        marks.push({ kind: "circle", center: point, radius: 7, tone, width: 2 });
        marks.push({ kind: "circle", center: point, radius: 1.5, tone, width: "fill" });
      }
    }
  }

  if (layers.hit) {
    const flash = hitFlash(rally?.hits, frame);
    if (flash) {
      const method = manifest?.baseMethod ?? input.methods[0];
      const at = method ? shuttleAt(chunk, method, flash.hit.frame) : null;
      // Without a tracked shuttle the whole picture flashes, so a hit never passes unseen.
      marks.push(at ? { kind: "circle", center: at, radius: 12 + flash.grow, tone: "hit", width: 2.5 }
        : { kind: "border", tone: "hit", width: 3 });
    }
  }

  if (layers.stroke) {
    const hit = recentStroke(rally?.hits, frame);
    if (hit?.type) {
      const side = hit.hitterSide ?? null;
      // The label follows where the hitter is now, so it never drifts onto the other player.
      const pose = side ? poseAt(chunk, side, frame) ?? poseAt(chunk, side, hit.frame) : null;
      const text = hit.confidence === null ? hit.type : `${hit.type} ${hit.confidence.toFixed(2)}`;
      const tone: OverlayTone = hit.hitter ?? row(side) ?? "neutral";
      marks.push(pose?.bbox
        ? { kind: "label", at: [(pose.bbox[0] + pose.bbox[2]) / 2, pose.bbox[1]], text, tone, align: "center",
          lift: layers.players ? 28 : 6 }
        : { kind: "label", at: "video-bottom", text, tone, align: "center", lift: 48 });
    }
  }
  return marks;
}
