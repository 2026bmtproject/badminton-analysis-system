import { readFile, mkdir, writeFile, rename, stat, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";
import { z } from "zod";
import { parseMatchModel } from "../src/data/matchParser";
import type { MatchModel } from "../src/domain/models";
import { mediaTool } from "./media-tools";

export const matchIdSchema = z.string().regex(/^[A-Za-z0-9_-]+$/);
export function matchesRoot(uiRoot: string) {
  return resolve(process.env.BADMINTON_MATCHES_DIR?.trim() || resolve(uiRoot, "../matches"));
}
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
async function save(path: string, value: unknown) {
  await writeFile(path + ".tmp", JSON.stringify(value, null, 2) + "\n");
  await rename(path + ".tmp", path);
}

export async function registerMatch(
  uiRoot: string, id: string, name: string, model: unknown, video: VideoRegistration,
) {
  matchIdSchema.parse(id);
  const publicDir = join(uiRoot, "public/matches"), localDir = join(uiRoot, ".local");
  await mkdir(publicDir, { recursive: true });
  await mkdir(localDir, { recursive: true });
  const catalog: CatalogEntry[] = (await optionalJson(join(publicDir, "catalog.json"))) ?? [];
  const registry: Record<string, VideoRegistration> =
    (await optionalJson(join(localDir, "videos.json"))) ?? {};
  const entry = { id: `match:${id}`, name, url: `/matches/${id}.json` };
  await save(join(publicDir, `${id}.json`), model);
  await save(join(localDir, "videos.json"), { ...registry, [id]: video });
  await save(join(publicDir, "catalog.json"), [
    ...catalog.filter((candidate) => candidate.id !== entry.id), entry,
  ]);
}

/** Invoke the Python data boundary with an argv array; no shell command is composed. */
export async function exportReview(uiRoot: string, id: string): Promise<MatchModel> {
  matchIdSchema.parse(id);
  const repositoryRoot = resolve(uiRoot, "..");
  const matchRoot = resolve(matchesRoot(uiRoot), id);
  const videoPath = join(matchRoot, "input/match.mp4");
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
  const localDir = join(uiRoot, ".local");
  await mkdir(localDir, { recursive: true });
  const output = join(localDir, `${id}-review-export-${process.pid}.json`);
  const args = ["run", "python", "-m", "modules.review_export", matchRoot,
    "--output", output, "--duration", String(duration),
    "--video-url", `/local-video/${id}`, "--scenario", `match:${id}`];
  if (metadata.title) args.push("--title", metadata.title);
  if (metadata.players) args.push("--player-a", metadata.players.a, "--player-b", metadata.players.b);
  try {
    execFileSync(process.env.UV_BINARY?.trim() || "uv", args, {
      cwd: repositoryRoot, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
    });
    return parseMatchModel(await json(output));
  } catch (error) {
    const detail = error instanceof Error && "stderr" in error
      ? String((error as Error & { stderr?: string }).stderr ?? error.message).trim()
      : error instanceof Error ? error.message : "Python exporter failed";
    throw new Error(`回看資料匯出失敗：${detail}`);
  } finally {
    await rm(output, { force: true });
  }
}

export async function importMatch(uiRoot: string, id: string) {
  const matchRoot = resolve(matchesRoot(uiRoot), id);
  const videoPath = join(matchRoot, "input/match.mp4");
  const before = await stat(videoPath);
  const model = await exportReview(uiRoot, id);
  const after = await stat(videoPath);
  if (before.size !== after.size || before.mtimeMs !== after.mtimeMs)
    throw new Error("匯入期間影片變動");
  await registerMatch(uiRoot, id, model.title, model, {
    path: videoPath, size: after.size, mtimeMs: after.mtimeMs,
  });
  await save(join(uiRoot, ".local", `${id}-sources.json`), {
    matchRoot, video: { path: videoPath, size: after.size, mtimeMs: after.mtimeMs },
    fingerprints: model.source?.fingerprints ?? {}, states: model.states,
  });
  return model;
}
