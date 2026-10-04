import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import { browserRuntime, type LocalRuntime, type VideoRegistration } from "./local-matches";

/** Only registered IDs are accepted; no request path is used as a disk path. */
export function createLocalVideoHandler(context: string | LocalRuntime) {
  const config = typeof context === "string" ? browserRuntime(context) : context;
  return async function serve(req: IncomingMessage, res: ServerResponse, next: () => void) {
    const pathname = (req.url ?? "").split("?")[0];
    if (!pathname.startsWith("/local-video/")) return next();
    if (req.method !== "GET" && req.method !== "HEAD") { res.writeHead(405).end(); return; }
    try {
      const id = /^\/local-video\/([A-Za-z0-9_-]+)$/.exec(pathname)?.[1];
      const registry: Record<string, VideoRegistration> = JSON.parse(await readFile(join(config.dataDir, "videos.json"), "utf8"));
      const entry = id && Object.hasOwn(registry, id) ? registry[id] : undefined;
      if (!entry) { res.writeHead(404).end(); return; }
      const info = await stat(entry.path);
      if (info.size !== entry.size || info.mtimeMs !== entry.mtimeMs) { res.writeHead(409).end("Video changed; reimport match."); return; }
      let start = 0, end = info.size - 1;
      if (req.headers.range) {
        const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
        if (!range || (!range[1] && !range[2])) { res.writeHead(416, { "Content-Range": `bytes */${info.size}` }).end(); return; }
        if (!range[1]) start = Math.max(0, info.size - Number(range[2]));
        else { start = Number(range[1]); if (range[2]) end = Math.min(end, Number(range[2])); }
        if (!Number.isSafeInteger(start) || start < 0 || start > end) { res.writeHead(416, { "Content-Range": `bytes */${info.size}` }).end(); return; }
      }
      res.writeHead(req.headers.range ? 206 : 200, { "Content-Type": "video/mp4", "Accept-Ranges": "bytes",
        "Content-Length": end - start + 1, "Cache-Control": "no-cache",
        ...(req.headers.range ? { "Content-Range": `bytes ${start}-${end}/${info.size}` } : {}) });
      if (req.method === "HEAD") { res.end(); return; }
      const stream = createReadStream(entry.path, { start, end });
      res.on("close", () => stream.destroy()); stream.on("error", () => res.destroy()); stream.pipe(res);
    } catch { if (!res.headersSent) res.writeHead(404).end(); else res.destroy(); }
  };
}

export function localVideoPlugin(uiRoot: string): Plugin {
  const serve = createLocalVideoHandler(browserRuntime(uiRoot));
  return { name: "registered-local-video", configureServer(server) { server.middlewares.use(serve); },
    configurePreviewServer(server) { server.middlewares.use(serve); } };
}
