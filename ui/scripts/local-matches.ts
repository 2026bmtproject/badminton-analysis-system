import { readFile, readdir, mkdir, writeFile, rename, stat, rm } from "node:fs/promises";
import { resolve, join, extname } from "node:path";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { z } from "zod";
import { parseMatchModel } from "../src/data/matchParser";
import type { MatchModel } from "../src/domain/models";
import { mediaTool } from "./media-tools";

export const matchIdSchema = z.string().regex(/^[A-Za-z0-9_-]+$/);
export function matchesRoot(uiRoot: string) {
  return resolve(process.env.BADMINTON_MATCHES_DIR?.trim() || resolve(uiRoot, "../matches"));
}
export type LocalRuntime = {
  uiRoot: string; matchesDir: string; backendDir: string; dataDir: string;
  reviewDir: string; uvBinary: string;
};
export function browserRuntime(uiRoot: string): LocalRuntime {
  return { uiRoot, matchesDir: matchesRoot(uiRoot), backendDir: resolve(uiRoot, ".."),
    dataDir: join(uiRoot, ".local"), reviewDir: join(uiRoot, "public", "matches"),
    uvBinary: process.env.UV_BINARY?.trim() || "uv" };
}
export function desktopRuntime(uiRoot: string, matchesDir: string, backendDir: string,
  dataDir: string, uvBinary: string): LocalRuntime {
  return { uiRoot: resolve(uiRoot), matchesDir: resolve(matchesDir), backendDir: resolve(backendDir),
    dataDir: resolve(dataDir), reviewDir: resolve(dataDir, "reviews"), uvBinary };
}
function runtime(value: string | LocalRuntime) { return typeof value === "string" ? browserRuntime(value) : value; }
export type CatalogEntry = { id: string; name: string; url: string };
export type VideoRegistration = { path: string; size: number; mtimeMs: number };

async function json(path: string) { return JSON.parse(await readFile(path, "utf8")); }
async function optionalJson(path: string) {
  try { return await json(path); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}
const VIDEO_EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".m4v"];
/** Mirror of modules.contracts.resolve_input_video: the first video file under input/ by name.
 * Windows paths sort case-insensitively in Python, so match that to pick the same file. */
export async function resolveInputVideo(matchRoot: string): Promise<string | null> {
  const folder = join(matchRoot, "input");
  let names: string[];
  try { names = await readdir(folder); }
  catch (error) {
    if (["ENOENT", "ENOTDIR"].includes((error as NodeJS.ErrnoException).code ?? "")) return null;
    throw error;
  }
  const key = (name: string) => process.platform === "win32" ? name.toLowerCase() : name;
  for (const name of names.sort((a, b) => key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0)) {
    if (!VIDEO_EXTENSIONS.includes(extname(name).toLowerCase())) continue;
    const path = join(folder, name);
    if (await stat(path).then(info => info.isFile(), () => false)) return path;
  }
  return null;
}
async function save(path: string, value: unknown) {
  await writeFile(path + ".tmp", JSON.stringify(value, null, 2) + "\n");
  await rename(path + ".tmp", path);
}

export async function registerMatch(
  context: string | LocalRuntime, id: string, name: string, model: unknown, video: VideoRegistration,
) {
  matchIdSchema.parse(id);
  const { reviewDir: publicDir, dataDir: localDir } = runtime(context);
  await mkdir(publicDir, { recursive: true });
  await mkdir(localDir, { recursive: true });
  const catalog: CatalogEntry[] = (await optionalJson(join(publicDir, "catalog.json"))) ?? [];
  const registry: Record<string, VideoRegistration> =
    (await optionalJson(join(localDir, "videos.json"))) ?? {};
  const hash = createHash("sha256").update(JSON.stringify(model)).digest("hex").slice(0, 16);
  const entry = { id: `match:${id}`, name, url: `/matches/${id}-${hash}.json` };
  // Publish immutable data first; the catalog update is the commit point.
  await save(join(publicDir, `${id}-${hash}.json`), model);
  await save(join(localDir, "videos.json"), { ...registry, [id]: video });
  try {
    await save(join(publicDir, "catalog.json"), [
      ...catalog.filter((candidate) => candidate.id !== entry.id), entry,
    ]);
  } catch (error) {
    await save(join(localDir, "videos.json"), registry);
    throw error;
  }
}

/** Invoke the Python data boundary with an argv array; no shell command is composed. */
export async function exportReview(context: string | LocalRuntime, id: string, videoPath: string): Promise<MatchModel> {
  matchIdSchema.parse(id);
  const { backendDir: repositoryRoot, matchesDir, dataDir: localDir, uvBinary } = runtime(context);
  const matchRoot = resolve(matchesDir, id);
  const metadata = z.object({
    title: z.string().optional(),
    players: z.object({ a: z.string(), b: z.string() }).optional(),
  }).parse((await optionalJson(join(matchRoot, "review-metadata.json"))) ?? {});
  const probe = JSON.parse(execFileSync(mediaTool("ffprobe"), [
    "-v", "error", "-show_entries", "format=duration:stream=codec_type,duration", "-of", "json", videoPath,
  ], { encoding: "utf8" }));
  const duration = Number(probe.format.duration ??
    probe.streams.find((stream: { codec_type: string }) => stream.codec_type === "video")?.duration);
  if (!Number.isFinite(duration) || duration <= 0) throw new Error("影片片長無效");
  await mkdir(localDir, { recursive: true });
  const output = join(localDir, `${id}-review-export-${process.pid}.json`);
  const args = ["run", "--no-sync", "python", "-m", "modules.review_export", matchRoot,
    "--output", output, "--duration", String(duration),
    "--video-url", `/local-video/${id}`, "--scenario", `match:${id}`];
  if (metadata.title) args.push("--title", metadata.title);
  if (metadata.players) args.push("--player-a", metadata.players.a, "--player-b", metadata.players.b);
  try {
    execFileSync(uvBinary, args, {
      cwd: repositoryRoot, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
    });
    return parseMatchModel(await json(output));
  } catch (error) {
    // A schema rejection prints as a JSON issue dump; name the first broken field instead.
    const detail = error instanceof z.ZodError
      ? `分析結果資料不一致（${error.issues[0]?.path.join(".")}：${error.issues[0]?.message}）`
      : error instanceof Error && "stderr" in error
        ? String((error as Error & { stderr?: string }).stderr ?? error.message).trim()
        : error instanceof Error ? error.message : "Python exporter failed";
    throw new Error(`匯入結果失敗：${detail}`);
  } finally {
    await rm(output, { force: true });
  }
}

export async function importMatch(context: string | LocalRuntime, id: string) {
  const config = runtime(context);
  const matchRoot = resolve(config.matchesDir, id);
  const videoPath = await resolveInputVideo(matchRoot);
  if (!videoPath) throw new Error("input/ 中找不到影片檔");
  const before = await stat(videoPath);
  const model = await exportReview(config, id, videoPath);
  const after = await stat(videoPath);
  if (before.size !== after.size || before.mtimeMs !== after.mtimeMs)
    throw new Error("匯入期間影片變動");
  await registerMatch(config, id, model.title, model, {
    path: videoPath, size: after.size, mtimeMs: after.mtimeMs,
  });
  await save(join(config.dataDir, `${id}-sources.json`), {
    matchRoot, video: { path: videoPath, size: after.size, mtimeMs: after.mtimeMs },
    fingerprints: model.source?.fingerprints ?? {}, states: model.states,
  });
  return model;
}
