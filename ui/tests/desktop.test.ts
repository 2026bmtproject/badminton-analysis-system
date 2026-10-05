import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createServer, request as httpRequest } from "node:http";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { stat } from "node:fs/promises";
import { desktopRuntime, registerMatch } from "../scripts/local-matches";
import { startLocalHost } from "../scripts/local-host";
import { closeAction, canSwitchDirectory } from "../desktop/lifecycle";
import { defaultSettings, profileRoot, readSettings, saveSettings, validateSettings } from "../desktop/settings";
import { LocalTaskService, validateHandshake, type ServiceHealth,
  type ServiceDependencies } from "../desktop/service-manager";

async function temporary(name: string, fn: (root: string) => Promise<void>) {
  const root = await mkdtemp(join(tmpdir(), `badminton-desktop-${name}-`));
  try { await fn(root); } finally { await rm(root, { recursive: true, force: true }); }
}

test("desktop settings persist Chinese and spaced paths in userData", async () => {
  await temporary("settings", async root => {
    const userData = join(root, "使用者 資料");
    const defaults = defaultSettings();
    const settings = { matchesDir: join(root, "比賽 資料"),
      backendDir: join(root, "Python 專案"), uvBinary: join(root, "uv tools", "uv.exe") };
    await saveSettings(userData, settings);
    assert.deepEqual(await readSettings(userData, defaults), settings);
    assert.equal(profileRoot(userData, settings.matchesDir), profileRoot(userData, settings.matchesDir));
    assert.notEqual(profileRoot(userData, settings.matchesDir), profileRoot(userData, root));
  });
});

test("missing Python environment and uv give actionable setup errors", async () => {
  await temporary("missing", async root => {
    const matches = join(root, "matches"), backend = join(root, "Python 專案");
    await mkdir(matches);
    await mkdir(join(backend, "modules", "local_tasks"), { recursive: true });
    await writeFile(join(backend, "pyproject.toml"), "[project]\nname='test'");
    await writeFile(join(backend, "modules", "local_tasks", "service.py"), "");
    const settings = { matchesDir: matches, backendDir: backend, uvBinary: join(root, "missing-uv.exe") };
    assert.match((await validateSettings(settings))!, /uv 環境/);
    await mkdir(join(backend, ".venv", "Scripts"), { recursive: true });
    await writeFile(join(backend, ".venv", "Scripts", "python.exe"), "");
    assert.match((await validateSettings(settings))!, /找不到 uv/);
  });
});

test("production host serves dist and registered media without Vite or path fallback", async () => {
  await temporary("host", async root => {
    const ui = join(root, "ui"), dist = join(ui, "dist"), matches = join(root, "比賽 資料");
    const data = join(root, "user data"), backend = join(root, "Python checkout");
    await mkdir(join(dist, "assets"), { recursive: true });
    await mkdir(matches, { recursive: true });
    await writeFile(join(dist, "index.html"), "<html>Production desktop shell</html>");
    await writeFile(join(dist, "assets", "app.js"), "console.log('ready')");
    const video = join(root, "video sample.mp4");
    await writeFile(video, Buffer.from("0123456789"));
    const info = await stat(video);
    const context = desktopRuntime(ui, matches, backend, data, "uv");
    await registerMatch(context, "Sample", "Sample Match", { title: "Review" },
      { path: video, size: info.size, mtimeMs: info.mtimeMs });
    const host = await startLocalHost(context, dist, { pipelinePort: () => null });
    try {
      const request = (path: string, options?: RequestInit) => fetch(host.origin + path, options);
      assert.match(await (await request("/matches")).text(), /Production desktop shell/);
      assert.match(await (await request("/matches/match:Sample/review")).text(), /Production desktop shell/);
      for (const route of ["/tasks", "/settings", "/analysis/Sample"])
        assert.match(await (await request(route)).text(), /Production desktop shell/);
      assert.equal((await request("/assets/app.js")).status, 200);
      const catalog = await (await request("/matches/catalog.json")).json() as Array<{ url: string }>;
      assert.equal((await request(catalog[0].url)).status, 200);
      for (const range of [
        ["bytes=0-2", "012", "bytes 0-2/10"],
        ["bytes=4-6", "456", "bytes 4-6/10"],
        ["bytes=-3", "789", "bytes 7-9/10"],
      ]) {
        const response = await request("/local-video/Sample", { headers: { Range: range[0] } });
        assert.equal(response.status, 206);
        assert.equal(await response.text(), range[1]);
        assert.equal(response.headers.get("content-range"), range[2]);
      }
      assert.equal((await request("/local-video/../secret")).status, 404);
      assert.equal((await request("/api/pipeline/stages")).status, 503);
      assert.match(await (await request("/api/unknown")).text(), /not found/);
      assert.equal((await request("/assets/missing.js")).status, 404);
      assert.equal((await request("/matches/catalog.json", { headers: { Origin: "http://evil.example" } })).status, 403);
      const badHost = await new Promise<number>((ok, fail) => {
        const call = httpRequest(`${host.origin}/matches/catalog.json`,
          { headers: { Host: "evil.example" } }, response => {
            response.resume(); ok(response.statusCode || 0);
          });
        call.on("error", fail); call.end();
      });
      assert.equal(badHost, 403);
      const candidate = join(matches, "NewMatch", "input");
      await mkdir(candidate, { recursive: true });
      await writeFile(join(candidate, "match.mp4"), "invalid video");
      await mkdir(join(matches, "NewMatch", "stages", "match_segmentation"), { recursive: true });
      await writeFile(join(matches, "NewMatch", "stages", "match_segmentation", "segments.json"), "{}");
      await writeFile(join(matches, "NewMatch", "stages", "match_segmentation", "status.json"), '{"status":"completed"}');
      assert.equal((await request("/api/local-matches")).status, 200);
      const before = await readFile(join(data, "reviews", "catalog.json"), "utf8");
      const failed = await request("/api/local-matches", { method: "POST", headers: {
        "Content-Type": "application/json", Origin: host.origin }, body: JSON.stringify({ id: "NewMatch" }) });
      assert.equal(failed.status, 422);
      assert.equal(await readFile(join(data, "reviews", "catalog.json"), "utf8"), before);
    } finally { await host.close(); }
  });
});

test("production host forwards court preview and save through the existing pipeline proxy", async () => {
  await temporary("court-proxy", async root => {
    const ui = join(root, "ui"), dist = join(ui, "dist"), matches = join(root, "matches");
    await mkdir(dist, { recursive: true }); await mkdir(matches);
    await writeFile(join(dist, "index.html"), "<html>desktop</html>");
    const paths: string[] = [];
    const service = createServer((req, res) => {
      paths.push(`${req.method} ${req.url}`);
      assert.equal(req.headers.origin, "http://127.0.0.1:5173");
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ ok: true }));
    });
    await new Promise<void>(resolve => service.listen(0, "127.0.0.1", resolve));
    const address = service.address();
    if (!address || typeof address === "string") throw new Error("service port unavailable");
    const context = desktopRuntime(ui, matches, root, join(root, "data"), "uv");
    const host = await startLocalHost(context, dist, {
      pipelinePort: () => address.port, serviceOrigin: () => "http://127.0.0.1:5173",
    });
    try {
      assert.equal((await fetch(`${host.origin}/api/pipeline/court?matchId=Sample`)).status, 200);
      assert.equal((await fetch(`${host.origin}/api/pipeline/court/save`, { method: "POST",
        headers: { "Content-Type": "application/json", Origin: host.origin }, body: JSON.stringify({ matchId: "Sample" }) })).status, 200);
      assert.deepEqual(paths, ["GET /api/pipeline/court?matchId=Sample", "POST /api/pipeline/court/save"]);
    } finally { await host.close(); await new Promise<void>(resolve => service.close(() => resolve())); }
  });
});

function health(matches: string, tasks: string, activeTask: ServiceHealth["activeTask"] = null): ServiceHealth {
  return { service: "badminton-local-tasks", apiVersion: 2, instanceId: "a".repeat(32),
    matchesRoot: resolve(matches), tasksRoot: resolve(tasks), uiOrigin: "http://127.0.0.1:5173",
    activeTask };
}

test("service handshake checks identity, version and configured directories", () => {
  const expected = health("C:/比賽 資料", "C:/tasks");
  assert.equal(validateHandshake(expected, expected.matchesRoot, expected.tasksRoot).instanceId, expected.instanceId);
  assert.throws(() => validateHandshake({ ...expected, apiVersion: 1 }, expected.matchesRoot, expected.tasksRoot));
  assert.throws(() => validateHandshake({ ...expected, service: "other" }, expected.matchesRoot, expected.tasksRoot));
  assert.throws(() => validateHandshake(expected, "C:/other", expected.tasksRoot));
});

test("existing compatible service is connected without taking ownership", async () => {
  await temporary("connect", async root => {
    const matches = join(root, "matches"), tasks = join(root, "tasks");
    let spawned = false;
    const deps = { probe: async () => health(matches, tasks), freePort: async () => 9001,
      spawn: (() => { spawned = true; throw new Error("unexpected spawn"); }) as ServiceDependencies["spawn"],
      delay: async () => undefined };
    const manager = new LocalTaskService({ matchesDir: matches, backendDir: root, uvBinary: "uv" },
      tasks, root, "http://127.0.0.1:4567", deps);
    assert.equal((await manager.connect()).owned, false);
    assert.equal(spawned, false);
    assert.equal((await manager.stopOwnedIfIdle()), true);
  });
});

test("old service on the same tasks directory reports a version conflict without spawning", async () => {
  await temporary("old-service", async root => {
    const matches = join(root, "matches"), tasks = join(root, "tasks");
    let spawned = false;
    const deps: ServiceDependencies = {
      probe: async () => ({ ...health(matches, tasks), apiVersion: 1 }),
      freePort: async () => 9001,
      spawn: (() => { spawned = true; throw new Error("unexpected spawn"); }) as ServiceDependencies["spawn"],
      delay: async () => undefined,
    };
    const manager = new LocalTaskService({ matchesDir: matches, backendDir: root, uvBinary: "uv" },
      tasks, root, "http://127.0.0.1:4567", deps);
    await assert.rejects(manager.connect(), /API v1.*tasks/);
    assert.equal(spawned, false);
  });
});

test("incompatible port uses a separate owned service; active worker blocks exit and switching", async () => {
  await temporary("lifecycle", async root => {
    const matches = join(root, "比賽 資料"), tasks = join(root, "tasks");
    let running = false, stopped = false;
    let active: ServiceHealth["activeTask"] = null;
    let argv: string[] = [], cwd = "";
    const child = Object.assign(new EventEmitter(), { exitCode: null, kill: () => { stopped = true; } });
    const deps: ServiceDependencies = {
      probe: async port => port === 8765 ? { service: "other" }
        : running ? health(matches, tasks, active) : null,
      freePort: async () => 9010,
      spawn: ((command: string, args: readonly string[], options: { cwd?: string }) => {
        assert.equal(command, join(root, "uv tools", "uv.exe"));
        argv = [...args]; cwd = options.cwd || ""; running = true;
        return child;
      }) as ServiceDependencies["spawn"],
      delay: async () => undefined,
    };
    const manager = new LocalTaskService({ matchesDir: matches, backendDir: join(root, "Python 專案"),
      uvBinary: join(root, "uv tools", "uv.exe") }, tasks, root, "http://127.0.0.1:4567", deps);
    const connected = await manager.connect();
    assert.equal(connected.port, 9010);
    assert.equal(connected.owned, true);
    assert.equal(cwd, join(root, "Python 專案"));
    assert.deepEqual(argv.slice(0, 5), ["run", "--no-sync", "python", "-m", "modules.local_tasks.service"]);
    assert.ok(argv.includes(matches));
    active = { id: "b".repeat(32), status: "running" };
    const during = await manager.refresh();
    assert.equal(canSwitchDirectory(during), false);
    assert.equal(closeAction(during), "background");
    assert.equal(await manager.stopOwnedIfIdle(), false);
    assert.equal(stopped, false);
    active = null;
    const after = await manager.refresh();
    assert.equal(closeAction(after), "quit");
    assert.equal(await manager.stopOwnedIfIdle(), true);
    assert.equal(stopped, true);
  });
});

test("failed Python launch reports the error without claiming ownership", async () => {
  await temporary("spawn-fail", async root => {
    const matches = join(root, "matches"), tasks = join(root, "tasks");
    const child = Object.assign(new EventEmitter(), { exitCode: null, kill: () => {} });
    const launch = (() => {
      queueMicrotask(() => child.emit("error", new Error("uv missing")));
      return child;
    }) as unknown as ServiceDependencies["spawn"];
    const deps: ServiceDependencies = {
      probe: async () => null, freePort: async () => 9001,
      spawn: launch,
      delay: async () => undefined,
    };
    const manager = new LocalTaskService({ matchesDir: matches, backendDir: root,
      uvBinary: "missing-uv.exe" }, tasks, root, "http://127.0.0.1:4567", deps);
    await assert.rejects(manager.connect(), /uv missing/);
    assert.equal(manager.status.owned, false);
  });
});
