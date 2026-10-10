import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rename, rm, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import { z } from "zod";
import { browserRuntime, matchIdSchema, resolveInputVideo, type LocalRuntime } from "./local-matches";
import { mediaTool } from "./media-tools";

const run = promisify(execFile);
const firstSegment = z.object({
  segments: z.array(z.object({ start_sec: z.number(), end_sec: z.number() })).min(1),
});

/** The middle frame of the first segment, so the picture is play rather than a pre-match card. */
export async function thumbnailTime(matchRoot: string): Promise<number | null> {
  try {
    const body = JSON.parse(await readFile(join(matchRoot, "stages", "match_segmentation", "segments.json"), "utf8"));
    const parsed = firstSegment.safeParse(body);
    if (!parsed.success) return null;
    const { start_sec, end_sec } = parsed.data.segments[0];
    return (start_sec + end_sec) / 2;
  } catch { return null; }
}

/** Extract (or reuse) the thumbnail; the cache key covers the video file and the chosen time. */
export async function matchThumbnail(context: LocalRuntime, id: string): Promise<{ path: string; key: string } | null> {
  matchIdSchema.parse(id);
  const matchRoot = resolve(context.matchesDir, id);
  const [video, time] = await Promise.all([resolveInputVideo(matchRoot), thumbnailTime(matchRoot)]);
  if (!video || time === null) return null;
  const info = await stat(video);
  const key = createHash("sha256").update(JSON.stringify([video, info.size, info.mtimeMs, time])).digest("hex").slice(0, 16);
  const folder = join(context.dataDir, "thumbnails");
  const path = join(folder, `${id}-${key}.jpg`);
  if (await stat(path).then(file => file.isFile(), () => false)) return { path, key };
  await mkdir(folder, { recursive: true });
  const partial = `${path}.${process.pid}.tmp.jpg`;
  try {
    await run(mediaTool("ffmpeg"), ["-v", "error", "-ss", time.toFixed(3), "-i", video,
      "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "4", "-y", partial]);
    await rename(partial, path);
  } catch { await rm(partial, { force: true }); return null; }
  const stale = new RegExp(`^${id}-[a-f0-9]{16}\\.jpg$`);
  for (const name of await readdir(folder))
    if (stale.test(name) && join(folder, name) !== path) await rm(join(folder, name), { force: true });
  return { path, key };
}

/** Only directory names that pass the match ID schema are accepted; no request path is used as a disk path. */
export function createThumbnailHandler(context: string | LocalRuntime) {
  const config = typeof context === "string" ? browserRuntime(context) : context;
  const pending = new Map<string, ReturnType<typeof matchThumbnail>>();
  return async function serve(req: IncomingMessage, res: ServerResponse, next: () => void) {
    const pathname = (req.url ?? "").split("?")[0];
    if (!pathname.startsWith("/local-thumbnail/")) return next();
    if (req.method !== "GET" && req.method !== "HEAD") { res.writeHead(405).end(); return; }
    try {
      const id = /^\/local-thumbnail\/([A-Za-z0-9_-]+)$/.exec(pathname)?.[1];
      if (!id) { res.writeHead(404).end(); return; }
      let job = pending.get(id);
      if (!job) {
        job = matchThumbnail(config, id).finally(() => pending.delete(id));
        pending.set(id, job);
      }
      const thumbnail = await job;
      if (!thumbnail) { res.writeHead(404).end(); return; }
      const etag = `"${thumbnail.key}"`;
      if (req.headers["if-none-match"] === etag) { res.writeHead(304, { ETag: etag }).end(); return; }
      const body = await readFile(thumbnail.path);
      res.writeHead(200, { "Content-Type": "image/jpeg", "Content-Length": body.length,
        "Cache-Control": "no-cache", ETag: etag });
      res.end(req.method === "HEAD" ? undefined : body);
    } catch { if (!res.headersSent) res.writeHead(404).end(); else res.destroy(); }
  };
}

export function thumbnailPlugin(uiRoot: string): Plugin {
  const serve = createThumbnailHandler(browserRuntime(uiRoot));
  return { name: "local-match-thumbnail", configureServer(server) { server.middlewares.use(serve); },
    configurePreviewServer(server) { server.middlewares.use(serve); } };
}
