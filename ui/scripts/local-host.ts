import { createReadStream } from "node:fs";
import { createServer, request as httpRequest, type IncomingMessage, type ServerResponse } from "node:http";
import { stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createMatchImportHandler } from "./match-import-plugin";
import { createLocalVideoHandler } from "./video-plugin";
import type { LocalRuntime } from "./local-matches";

export type LocalHost = { origin: string; port: number; close: () => Promise<void> };
export type HostOptions = { port?: number; pipelinePort?: () => number | null; serviceOrigin?: () => string };

function json(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

function mime(path: string) {
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  if (path.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (path.endsWith(".css")) return "text/css; charset=utf-8";
  if (path.endsWith(".json")) return "application/json; charset=utf-8";
  if (path.endsWith(".svg")) return "image/svg+xml";
  if (path.endsWith(".woff2")) return "font/woff2";
  if (path.endsWith(".png")) return "image/png";
  return "application/octet-stream";
}

async function file(res: ServerResponse, path: string, head = false) {
  try {
    const info = await stat(path);
    if (!info.isFile()) throw new Error("not a file");
    res.writeHead(200, { "Content-Type": mime(path), "Content-Length": info.size,
      "Cache-Control": path.endsWith("index.html") || path.endsWith("catalog.json")
        ? "no-store" : "public, max-age=31536000, immutable",
      "Cross-Origin-Resource-Policy": "same-origin" });
    if (head) return res.end();
    createReadStream(path).on("error", () => res.destroy()).pipe(res);
  } catch { json(res, 404, { error: "not found" }); }
}

function proxy(req: IncomingMessage, res: ServerResponse, port: number, serviceOrigin: string) {
  if (req.method !== "GET" && req.method !== "POST") return json(res, 405, { error: "method not allowed" });
  const upstream = httpRequest({ hostname: "127.0.0.1", port, path: req.url,
    method: req.method, headers: {
      Host: `127.0.0.1:${port}`, Origin: serviceOrigin,
      ...(req.method === "POST" ? { "Content-Type": req.headers["content-type"] ?? "",
        "Content-Length": req.headers["content-length"] ?? "0" } : {}),
    } }, upstreamResponse => {
    res.writeHead(upstreamResponse.statusCode ?? 502,
      { "Content-Type": upstreamResponse.headers["content-type"] ?? "application/json",
        "Cache-Control": "no-store" });
    upstreamResponse.pipe(res);
  });
  upstream.on("error", () => { if (!res.headersSent) json(res, 503, { error: "分析服務未連線" }); });
  if (req.method === "POST" && Number(req.headers["content-length"] ?? 0) > 8192) {
    upstream.destroy(); return json(res, 413, { error: "request too large" });
  }
  req.pipe(upstream);
}

export async function startLocalHost(context: LocalRuntime, distDir: string,
  options: HostOptions = {}): Promise<LocalHost> {
  const dist = resolve(distDir);
  if (!(await stat(join(dist, "index.html")).catch(() => null))?.isFile())
    throw new Error(`Vue dist is missing: ${dist}`);
  const importHandler = createMatchImportHandler(context);
  const videoHandler = createLocalVideoHandler(context);
  let origin = "";
  const server = createServer(async (req, res) => {
    try {
      if (req.headers.host !== origin.slice(7) ||
          (req.headers.origin && req.headers.origin !== origin) ||
          req.headers["sec-fetch-site"] === "cross-site")
        return json(res, 403, { error: "only the local app origin is allowed" });
      const path = new URL(req.url ?? "/", origin).pathname;
      if (path === "/api/local-matches")
        return void importHandler(req, res, () => json(res, 404, { error: "not found" }));
      if (path.startsWith("/local-video/"))
        return void videoHandler(req, res, () => json(res, 404, { error: "not found" }));
      if (path.startsWith("/api/pipeline/")) {
        const pipelinePort = options.pipelinePort?.();
        if (!pipelinePort) return json(res, 503, { error: "分析服務未連線" });
        return proxy(req, res, pipelinePort, options.serviceOrigin?.() || origin);
      }
      if (path.startsWith("/api/") || path.startsWith("/local-video/"))
        return json(res, 404, { error: "not found" });
      if (req.method !== "GET" && req.method !== "HEAD")
        return json(res, 405, { error: "method not allowed" });
      const head = req.method === "HEAD";
      if (path === "/matches/catalog.json")
        return await file(res, join(context.reviewDir, "catalog.json"), head);
      const review = /^\/matches\/([A-Za-z0-9_-]+-[a-f0-9]{16}\.json)$/.exec(path);
      if (review) return await file(res, join(context.reviewDir, review[1]), head);
      if (path.startsWith("/matches/") && path.endsWith(".json"))
        return json(res, 404, { error: "not found" });
      const asset = /^\/assets\/([A-Za-z0-9._-]+)$/.exec(path);
      if (asset) return await file(res, join(dist, "assets", asset[1]), head);
      if (path.startsWith("/assets/") || path.startsWith("/generated/"))
        return json(res, 404, { error: "not found" });
      if (path === "/" || path === "/matches" || path === "/tasks" || path === "/settings" || /^\/analysis\/[^/]+$/.test(path) || /^\/matches\/[^/]+(?:\/review|\/rallies(?:\/[^/]+)?)?$/.test(path)) {
        res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'self'");
        return await file(res, join(dist, "index.html"), head);
      }
      return json(res, 404, { error: "not found" });
    } catch { if (!res.headersSent) json(res, 500, { error: "local host failed" }); else res.destroy(); }
  });
  await new Promise<void>((ok, fail) => {
    server.once("error", fail);
    server.listen(options.port ?? 0, "127.0.0.1", () => { server.off("error", fail); ok(); });
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("local host address unavailable");
  origin = `http://127.0.0.1:${address.port}`;
  return { origin, port: address.port, close: () => new Promise<void>((ok, fail) =>
    server.close(error => error ? fail(error) : ok())) };
}
