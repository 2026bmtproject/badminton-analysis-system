import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import { browserRuntime, importMatch, matchIdSchema, resolveInputVideo, type LocalRuntime } from "./local-matches";

type Candidate = { id: string; available: boolean; reason?: string };

function json(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function exists(path: string) {
  try { await stat(path); return true; }
  catch { return false; }
}

export async function listImportableMatches(context: string | LocalRuntime): Promise<Candidate[]> {
  const config = typeof context === "string" ? browserRuntime(context) : context;
  let entries;
  try { entries = await readdir(config.matchesDir, { withFileTypes: true }); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  return Promise.all(entries.filter((entry) => entry.isDirectory() && matchIdSchema.safeParse(entry.name).success)
    .sort((a, b) => a.name.localeCompare(b.name))
    .map(async (entry) => {
      const root = join(config.matchesDir, entry.name);
      if (!await resolveInputVideo(root))
        return { id: entry.name, available: false, reason: "input/ 中找不到影片檔" };
      if (!await exists(join(root, "stages", "match_segmentation", "segments.json")))
        return { id: entry.name, available: false, reason: "缺少分段結果" };
      const statusPath = join(root, "stages", "match_segmentation", "status.json");
      if (!await exists(statusPath))
        return { id: entry.name, available: false, reason: "缺少分段狀態" };
      try {
        const status = JSON.parse(await readFile(statusPath, "utf8"));
        if (status.status !== "completed")
          return { id: entry.name, available: false, reason: "分段分析尚未完成" };
      } catch {
        return { id: entry.name, available: false, reason: "分段狀態無法讀取" };
      }
      return { id: entry.name, available: true };
    }));
}

export function createMatchImportHandler(context: string | LocalRuntime) {
  let importing = false;
  return async function serve(req: IncomingMessage, res: ServerResponse, next: () => void) {
    if ((req.url ?? "").split("?")[0] !== "/api/local-matches") return next();
    const origin = req.headers.origin;
    if (origin && origin !== `http://${req.headers.host}`) {
      json(res, 403, { error: "只允許同源匯入" }); return;
    }
    if (req.method === "GET") {
      try { json(res, 200, { matches: await listImportableMatches(context) }); }
      catch { json(res, 500, { error: "無法讀取 matches 目錄" }); }
      return;
    }
    if (req.method !== "POST") { json(res, 405, { error: "不支援此操作" }); return; }
    if (!req.headers["content-type"]?.startsWith("application/json")) {
      json(res, 415, { error: "請使用 JSON 請求" }); return;
    }
    if (importing) { json(res, 409, { error: "已有比賽正在匯入" }); return; }
    importing = true;
    try {
      let body = "";
      for await (const chunk of req) {
        body += chunk.toString();
        if (body.length > 1024) { json(res, 413, { error: "請求過大" }); return; }
      }
      const id: unknown = JSON.parse(body)?.id;
      if (typeof id !== "string" || !matchIdSchema.safeParse(id).success) {
        json(res, 400, { error: "比賽 ID 無效" }); return;
      }
      const candidate = (await listImportableMatches(context)).find((item) => item.id === id);
      if (!candidate) { json(res, 404, { error: "matches 目錄中找不到此比賽" }); return; }
      if (!candidate.available) { json(res, 422, { error: candidate.reason }); return; }
      await importMatch(context, id);
      json(res, 200, { id: `match:${id}` });
    } catch (error) {
      json(res, 422, { error: error instanceof Error ? error.message : "匯入失敗" });
    } finally {
      importing = false;
    }
  };
}

export function matchImportPlugin(uiRoot: string): Plugin {
  const serve = createMatchImportHandler(browserRuntime(uiRoot));
  return { name: "local-match-import", configureServer(server) { server.middlewares.use(serve); },
    configurePreviewServer(server) { server.middlewares.use(serve); } };
}
