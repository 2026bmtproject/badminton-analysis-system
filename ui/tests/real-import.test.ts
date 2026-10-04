import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile, mkdtemp, cp, rm, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { adapt, manifestSchema, type Input } from "../scripts/adapter";
import { registerMatch } from "../scripts/local-matches";

const manifest = manifestSchema.parse(
  JSON.parse(await readFile("fixtures/manifest.json", "utf8")),
);
async function raw(): Promise<Input> {
  return Object.fromEntries(
    await Promise.all(
      manifest.scenarios[0].stages
        .filter((s) => !s.startsWith("commentary"))
        .map(async (s) => [
          s,
          JSON.parse(await readFile(`fixtures/stages/${s}.json`, "utf8")),
        ]),
    ),
  );
}
test("legal null scores preserve valid segment scores and retry reason", async () => {
  const input = await raw();
  const data = input.scores as {
    rallies: Array<Record<string, unknown>>;
    attempts?: unknown[];
  };
  data.rallies[1].score_a = null;
  data.rallies[1].score_b = null;
  data.attempts = [{ segment_index: 1, note: "failed after 3 retries" }];
  const model = adapt(input, manifest, "test");
  assert.equal(model.states.scores.status, "available");
  assert.deepEqual(model.rallies[0].score, [18, 17]);
  assert.equal(model.rallies[1].score, null);
  assert.equal(model.rallies[1].scoreIssue, "failed after 3 retries");
  data.rallies[1].score_a = "invalid";
  assert.equal(adapt(input, manifest, "test").states.scores.status, "error");
});
test("identity epoch boundaries, zero-based games, missing mapping and explicit conflicts", async () => {
  const input = await raw();
  const scores = input.scores as {
    rallies: Array<{ game_index: number | null }>;
  };
  for (const s of scores.rallies) s.game_index = null;
  const epochs = [
    { first_segment: 0, last_segment: 1, game_index: 0, top: "a", bottom: "b" },
    { first_segment: 2, last_segment: 2, game_index: 1, top: "b", bottom: "a" },
  ];
  input.identity = { epochs };
  const model = adapt(input, manifest, "test");
  assert.deepEqual(
    model.rallies.map((r) => r.game),
    [0, 0, 1, null],
  );
  assert.equal(model.rallies[2].gameSource, "identity");
  assert.equal(model.rallies[2].hits![0].player, manifest.players.b);
  assert.equal(model.rallies[2].hits![1].player, manifest.players.a);
  assert.equal(model.rallies[3].hits![0].player, "畫面上方");
  scores.rallies[2].game_index = 2;
  const conflict = adapt(input, manifest, "test").rallies[2];
  assert.equal(conflict.game, 2);
  assert.match(conflict.gameConflict!, /衝突/);
  assert.deepEqual(conflict.score, [19, 19]);
  for (const bad of [
    { ...epochs[1], first_segment: 1 },
    { ...epochs[1], last_segment: 4 },
    { ...epochs[1], bottom: "b" },
  ]) {
    input.identity = { epochs: [epochs[0], bad] };
    const invalid = adapt(input, manifest, "test");
    assert.equal(invalid.states.identity.status, "error");
    assert.equal(invalid.rallies[0].hits![0].player, "畫面上方");
  }
});
test("missing commentary never supplies demo interpretation or citations", async () => {
  const model = adapt(await raw(), manifest, "real");
  assert.equal(model.states.commentary.status, "missing");
  assert.ok(model.rallies.every((r) => r.commentary.status === "unavailable"));
  assert.equal(model.rallies[1].hits![2].eventIndex, 7);
});
test("real catalog and registrations survive the actual demo-data generator", async () => {
  const folder = await mkdtemp(join(tmpdir(), "badminton-ui-import-"));
  try {
    await cp(resolve("fixtures"), join(folder, "fixtures"), {
      recursive: true,
    });
    await registerMatch(
      folder,
      "local_test",
      "Local",
      { rallies: [] },
      { path: "registered.mp4", size: 1, mtimeMs: 0 },
    );
    const catalog = JSON.parse(await readFile(join(folder, "public/matches/catalog.json"), "utf8"));
    const files = [
      "public/matches/catalog.json",
      `public${catalog[0].url}`,
      ".local/videos.json",
    ];
    const before = await Promise.all(
      files.map((f) => readFile(join(folder, f), "utf8")),
    );
    execFileSync(
      process.execPath,
      [
        "--import",
        pathToFileURL(resolve("node_modules/tsx/dist/loader.mjs")).href,
        resolve("scripts/generate-data.ts"),
      ],
      { cwd: folder },
    );
    assert.deepEqual(
      await Promise.all(files.map((f) => readFile(join(folder, f), "utf8"))),
      before,
    );
    assert.ok(
      (
        await readFile(join(folder, "public/generated/catalog.json"), "utf8")
      ).includes("dense-test"),
    );
  } finally {
    assert.ok(
      resolve(folder).startsWith(resolve(tmpdir()) + requireSeparator()),
    );
    await rm(folder, { recursive: true, force: true });
  }
});
test("failed catalog publication keeps the previous Review and video registration", async () => {
  const folder = await mkdtemp(join(tmpdir(), "badminton-ui-publish-"));
  try {
    await registerMatch(folder, "local_test", "Old", { version: 1 },
      { path: "old.mp4", size: 1, mtimeMs: 1 });
    const catalogPath = join(folder, "public/matches/catalog.json");
    const registryPath = join(folder, ".local/videos.json");
    const oldCatalog = await readFile(catalogPath, "utf8");
    const oldRegistry = await readFile(registryPath, "utf8");
    const oldUrl = JSON.parse(oldCatalog)[0].url;
    await mkdir(`${catalogPath}.tmp`);
    await assert.rejects(registerMatch(folder, "local_test", "New", { version: 2 },
      { path: "new.mp4", size: 2, mtimeMs: 2 }));
    assert.equal(await readFile(catalogPath, "utf8"), oldCatalog);
    assert.equal(await readFile(registryPath, "utf8"), oldRegistry);
    assert.deepEqual(JSON.parse(await readFile(join(folder, `public${oldUrl}`), "utf8")), { version: 1 });
  } finally {
    assert.ok(resolve(folder).startsWith(resolve(tmpdir()) + requireSeparator()));
    await rm(folder, { recursive: true, force: true });
  }
});
function requireSeparator() {
  return process.platform === "win32" ? "\\" : "/";
}
