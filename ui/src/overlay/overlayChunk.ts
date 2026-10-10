import { z } from "zod";
import type { OverlayManifestModel } from "../domain/models";

export type OverlayPoint = readonly [number, number];
export type OverlayPose = {
  bbox: readonly [number, number, number, number] | null;
  /** COCO-17; null where the keypoint fell below the pose score threshold. */
  keypoints: readonly (OverlayPoint | null)[] | null;
};
export type OverlaySide = "top" | "bottom";

/** One Rally's drawable geometry in video pixels; tracks are indexed by `frame - startFrame`. */
export type OverlayChunk = {
  segmentIndex: number;
  startFrame: number;
  frameCount: number;
  court: readonly OverlayPoint[] | null;
  shuttle: Readonly<Record<string, readonly (OverlayPoint | null)[]>>;
  pose: Readonly<Partial<Record<OverlaySide, readonly (OverlayPose | null)[]>>>;
};

const point = z.tuple([z.number(), z.number()]);
const pose = z.object({
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  keypoints: z.array(point.nullable()).length(17).nullable(),
}).nullable();
const chunk = z.object({
  schemaVersion: z.literal("review-overlay-v1"),
  segmentIndex: z.number().int().nonnegative(),
  startFrame: z.number().int().nonnegative(),
  frameCount: z.number().int().positive(),
  court: z.array(point).length(16).nullable(),
  shuttle: z.record(z.string(), z.array(point.nullable())),
  pose: z.object({ top: z.array(pose).optional(), bottom: z.array(pose).optional() }),
}).superRefine((value, context) => {
  const tracks = [...Object.values(value.shuttle), value.pose.top, value.pose.bottom];
  if (tracks.some((track) => track && track.length !== value.frameCount)) {
    context.addIssue({ code: "custom", message: "Overlay tracks must cover every frame of their Rally" });
  }
});

export function parseOverlayChunk(input: unknown, segmentIndex: number): OverlayChunk {
  const { schemaVersion: _version, ...parsed } = chunk.parse(input);
  if (parsed.segmentIndex !== segmentIndex) throw new Error("Overlay file belongs to another Rally");
  return parsed;
}

function slot(chunk: OverlayChunk, frame: number) {
  const offset = frame - chunk.startFrame;
  return offset >= 0 && offset < chunk.frameCount ? offset : null;
}

export function shuttleAt(chunk: OverlayChunk | null, method: string, frame: number): OverlayPoint | null {
  const offset = chunk ? slot(chunk, frame) : null;
  return offset === null ? null : chunk!.shuttle[method]?.[offset] ?? null;
}

export function poseAt(chunk: OverlayChunk | null, side: OverlaySide, frame: number): OverlayPose | null {
  const offset = chunk ? slot(chunk, frame) : null;
  return offset === null ? null : chunk!.pose[side]?.[offset] ?? null;
}

export type OverlayLoader = {
  /** The loaded file, null when there is none or it failed, undefined while it is still unknown. */
  peek(segmentIndex: number): OverlayChunk | null | undefined;
  load(segmentIndex: number): Promise<OverlayChunk | null>;
};

/**
 * Fetches Rally files on demand and keeps the most recent few. A failed file stays failed for this
 * loader, so a missing file is not refetched on every video frame.
 */
export function createOverlayLoader(
  manifest: Pick<OverlayManifestModel, "url" | "segments">,
  fetchJson: (url: string) => Promise<unknown> = defaultFetchJson,
  capacity = 6,
): OverlayLoader {
  const available = new Set(manifest.segments);
  const settled = new Map<number, OverlayChunk | null>();
  const pending = new Map<number, Promise<OverlayChunk | null>>();
  function remember(segmentIndex: number, value: OverlayChunk | null) {
    settled.delete(segmentIndex);
    settled.set(segmentIndex, value);
    while (settled.size > capacity) settled.delete(settled.keys().next().value!);
  }
  return {
    peek(segmentIndex) {
      if (!available.has(segmentIndex)) return null;
      const value = settled.get(segmentIndex);
      if (value !== undefined) remember(segmentIndex, value);
      return value;
    },
    load(segmentIndex) {
      if (!available.has(segmentIndex)) return Promise.resolve(null);
      if (settled.has(segmentIndex)) return Promise.resolve(this.peek(segmentIndex) ?? null);
      let job = pending.get(segmentIndex);
      if (!job) {
        const url = `${manifest.url}/rally-${String(segmentIndex).padStart(3, "0")}.json`;
        job = fetchJson(url)
          .then((body) => parseOverlayChunk(body, segmentIndex))
          .catch(() => null)
          .then((value) => { pending.delete(segmentIndex); remember(segmentIndex, value); return value; });
        pending.set(segmentIndex, job);
      }
      return job;
    },
  };
}

async function defaultFetchJson(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Overlay file ${url} returned ${response.status}`);
  return response.json() as Promise<unknown>;
}
