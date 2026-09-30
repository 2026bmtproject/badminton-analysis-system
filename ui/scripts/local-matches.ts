import { createReadStream } from "node:fs";
import {
  readFile,
  mkdir,
  writeFile,
  rename,
  stat,
  readdir,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";
import { z } from "zod";
import { adapt, manifestSchema, type Input } from "./adapter";
import { mediaTool } from "./media-tools";
import { deriveCourtPositions, invertHomography } from "./court-positions";

export const stageFiles: Record<string, string> = {
  match_segmentation: "segments.json",
  score_recognition: "scores.json",
  event_detection: "events.json",
  stroke_classification: "strokes.json",
  audio_highlight: "audio_signals.json",
  highlight_ranking: "highlights.json",
  player_identity: "identity.json",
  commentary: "commentary.json",
  court_detection: "court.json",
  pose: "pose.json",
  shuttle_tracking: "shuttle.json",
};
const aliases: Record<string, string> = {
  match_segmentation: "segments",
  score_recognition: "scores",
  event_detection: "events",
  stroke_classification: "strokes",
  audio_highlight: "audio_signals",
  highlight_ranking: "highlights",
  player_identity: "identity",
  commentary: "commentary",
  court_detection: "court",
  pose: "pose",
};
export const matchIdSchema = z.string().regex(/^[A-Za-z0-9_-]+$/);
export type CatalogEntry = { id: string; name: string; url: string };
export type VideoRegistration = { path: string; size: number; mtimeMs: number };

async function json(path: string) {
  return JSON.parse(await readFile(path, "utf8"));
}
async function optionalJson(path: string) {
  try {
    return await json(path);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw e;
  }
}
async function save(path: string, value: unknown) {
  await writeFile(path + ".tmp", JSON.stringify(value, null, 2) + "\n");
  await rename(path + ".tmp", path);
}

/** Same streaming SHA256[:16] as modules.base.artifact_fingerprint. */
async function fingerprint(path: string) {
  const hash = createHash("sha256");
  for await (const bytes of createReadStream(path)) hash.update(bytes);
  return hash.digest("hex").slice(0, 16);
}

export async function readCommentarySegmentArtifacts(matchRoot: string) {
  const directory = join(matchRoot, "stages", "commentary", "segments");
  let names: string[];
  try {
    names = await readdir(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT")
      return { artifacts: [], fingerprints: {}, paths: {} };
    throw error;
  }
  const artifacts: unknown[] = [];
  const fingerprints: Record<string, string> = {};
  const paths: Record<string, string> = {};
  for (const name of names
    .filter((candidate) => /^segment_\d{3,}\.json$/.test(candidate))
    .sort()) {
    const path = join(directory, name);
    const key = `commentary/segments/${name.replace(/\.json$/, "")}`;
    const bytes = await readFile(path);
    fingerprints[key] = createHash("sha256")
      .update(bytes)
      .digest("hex")
      .slice(0, 16);
    paths[key] = path;
    try {
      artifacts.push(JSON.parse(bytes.toString("utf8")));
    } catch {
      artifacts.push({ readError: true, artifact: name });
    }
  }
  return { artifacts, fingerprints, paths };
}

export async function registerMatch(
  uiRoot: string,
  id: string,
  name: string,
  model: unknown,
  video: VideoRegistration,
) {
  matchIdSchema.parse(id);
  const publicDir = join(uiRoot, "public/matches"),
    localDir = join(uiRoot, ".local");
  await mkdir(publicDir, { recursive: true });
  await mkdir(localDir, { recursive: true });
  const catalog: CatalogEntry[] =
    (await optionalJson(join(publicDir, "catalog.json"))) ?? [];
  const registry: Record<string, VideoRegistration> =
    (await optionalJson(join(localDir, "videos.json"))) ?? {};
  const entry = { id: `match:${id}`, name, url: `/matches/${id}.json` };
  await save(join(publicDir, `${id}.json`), model);
  await save(join(localDir, "videos.json"), { ...registry, [id]: video });
  await save(join(publicDir, "catalog.json"), [
    ...catalog.filter((e) => e.id !== entry.id),
    entry,
  ]);
}

export async function importMatch(uiRoot: string, id: string) {
  matchIdSchema.parse(id);
  const matchRoot = resolve(uiRoot, "../matches", id);
  const file = (stage: string) => {
    if (!stageFiles[stage]) throw new Error(`未知上游階段：${stage}`);
    return join(matchRoot, "stages", stage, stageFiles[stage]);
  };
  const fingerprints: Record<string, string> = {},
    statuses: Record<string, unknown> = {};
  const verifiedPaths: Record<string, string> = {};
  const pending = new Set<string>();
  async function verify(stage: string): Promise<void> {
    if (fingerprints[stage]) return;
    if (pending.has(stage)) throw new Error(`上游循環：${stage}`);
    pending.add(stage);
    const status = z
      .object({
        status: z.literal("completed"),
        inputs: z.record(z.string(), z.string()),
        finished_at: z.string().nullish(),
      })
      .parse(await json(join(matchRoot, "stages", stage, "status.json")));
    for (const [dependency, expected] of Object.entries(status.inputs)) {
      await verify(dependency);
      if (fingerprints[dependency] !== expected)
        throw new Error(`${stage} 的 ${dependency} 指紋已變動，拒絕混用資料`);
    }
    verifiedPaths[stage] = file(stage);
    fingerprints[stage] = await fingerprint(verifiedPaths[stage]);
    statuses[stage] = status;
    pending.delete(stage);
  }
  const raw: Input = {};
  for (const [stage, alias] of Object.entries(aliases)) {
    let bytes: Buffer;
    try {
      bytes = await readFile(file(stage));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      if (stage === "match_segmentation")
        throw new Error("缺少必要的 segments.json");
      continue;
    }
    await verify(stage);
    // Ensure the JSON parsed for this import is the same artifact we verified.
    if (
      createHash("sha256").update(bytes).digest("hex").slice(0, 16) !==
      fingerprints[stage]
    )
      throw new Error(`匯入期間 ${stage} 變動，請重試`);
    try {
      raw[alias] = JSON.parse(bytes.toString("utf8"));
    } catch (error) {
      if (stage !== "commentary") throw error;
      raw[alias] = { readError: true };
    }
  }
  const selectedCommentary = await readCommentarySegmentArtifacts(matchRoot);
  if (selectedCommentary.artifacts.length)
    raw.commentary_segments = selectedCommentary.artifacts;
  Object.assign(fingerprints, selectedCommentary.fingerprints);
  Object.assign(verifiedPaths, selectedCommentary.paths);
  const metadata = z
    .object({
      title: z.string().optional(),
      players: z.object({ a: z.string(), b: z.string() }).optional(),
    })
    .parse((await optionalJson(join(matchRoot, "review-metadata.json"))) ?? {});
  const videoPath = join(matchRoot, "input/match.mp4");
  const videoStat = await stat(videoPath);
  const probe = JSON.parse(
    execFileSync(
      mediaTool("ffprobe"),
      [
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,duration",
        "-of",
        "json",
        videoPath,
      ],
      { encoding: "utf8" },
    ),
  );
  const duration = Number(
    probe.format.duration ??
      probe.streams.find(
        (s: { codec_type: string }) => s.codec_type === "video",
      )?.duration,
  );
  if (!Number.isFinite(duration) || duration <= 0)
    throw new Error("影片片長無效");
  const manifest = manifestSchema.parse({
    title: metadata.title ?? id,
    video: `/local-video/${id}`,
    duration,
    players: metadata.players ?? {
      a: "選手 A（記分板列）",
      b: "選手 B（記分板列）",
    },
    identities: {},
    scenarios: [],
  });
  const model = adapt(raw, manifest, `match:${id}`);
  deriveCourtPositions(model, raw.court, raw.pose);
  model.states.court = raw.court === undefined ? { status: "missing" } :
    invertHomography((raw.court as { courts?: { homography?: unknown }[] })?.courts?.[0]?.homography)
      ? { status: "available" } : { status: "error", message: "球場校正不可用" };
  model.states.pose = raw.pose === undefined ? { status: "missing" } :
    Array.isArray((raw.pose as { frames?: unknown[] })?.frames)
      ? { status: "available" } : { status: "error", message: "姿態資料不可用" };
  model.capabilities.court = model.states.court.status === "available";
  model.capabilities.pose = model.states.pose.status === "available";
  const backendBaseCommit = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: resolve(uiRoot, ".."),
    encoding: "utf8",
  }).trim();
  const court = raw.court as { confirmed?: boolean } | undefined;
  model.source = {
    matchId: id,
    importedAt: new Date().toISOString(),
    fingerprints,
    backendBaseCommit,
    limitations: [
      "分析片段不等於完整得分回合；資料一致性不代表辨識準確率。",
      "局數與場上身分依 identity 推導；缺值保留，未補讀比分。",
      ...(court?.confirmed === false ? ["球場為自動校正，尚未人工確認。"] : []),
      "歡呼與精彩分數為片段彙總指標，不代表戰術品質。",
    ],
  };
  // Reject a moving source set rather than combining two analysis runs.
  for (const [stage, expected] of Object.entries(fingerprints))
    if ((await fingerprint(verifiedPaths[stage])) !== expected)
      throw new Error(`匯入期间 ${stage} 變動，請重試`);
  const finalStat = await stat(videoPath);
  if (
    finalStat.size !== videoStat.size ||
    finalStat.mtimeMs !== videoStat.mtimeMs
  )
    throw new Error("匯入期間影片變動");
  await registerMatch(uiRoot, id, manifest.title, model, {
    path: videoPath,
    size: videoStat.size,
    mtimeMs: videoStat.mtimeMs,
  });
  await save(join(uiRoot, ".local", `${id}-sources.json`), {
    matchRoot,
    video: {
      path: videoPath,
      size: videoStat.size,
      mtimeMs: videoStat.mtimeMs,
      duration,
    },
    fingerprints,
    statuses,
  });
  return model;
}
