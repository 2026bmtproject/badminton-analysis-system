import { spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { DesktopSettings } from "./settings";

export type ServiceHealth = {
  service: "badminton-local-tasks"; apiVersion: 2; instanceId: string;
  matchesRoot: string; tasksRoot: string; uiOrigin: string;
  activeTask: null | { id: string | null; status: string };
};
export type ServiceStatus = { connected: boolean; owned: boolean; port: number | null;
  health: ServiceHealth | null; error: string | null; warning: string | null };
export type ServiceDependencies = {
  probe: typeof probe; freePort: typeof freePort; spawn: typeof spawn;
  delay: (ms: number) => Promise<unknown>;
};

export function validateHandshake(value: unknown, matchesDir: string, tasksDir: string): ServiceHealth {
  if (!value || typeof value !== "object") throw new Error("服務未提供健康資訊");
  const row = value as Record<string, unknown>;
  if (row.service !== "badminton-local-tasks" || row.apiVersion !== 2 ||
      typeof row.instanceId !== "string" || !/^[a-f0-9]{32}$/.test(row.instanceId) ||
      typeof row.uiOrigin !== "string") throw new Error("本機服務名稱或 API 版本不相容");
  const same = (a: unknown, b: string) => typeof a === "string" &&
    resolve(a).toLowerCase() === resolve(b).toLowerCase();
  if (!same(row.matchesRoot, matchesDir) || !same(row.tasksRoot, tasksDir))
    throw new Error("本機服務的 matches／tasks 目錄與桌面設定不同");
  if (row.activeTask !== null && (typeof row.activeTask !== "object" || !row.activeTask))
    throw new Error("服務健康資訊格式無效");
  return value as ServiceHealth;
}

async function probe(port: number): Promise<unknown | null> {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/pipeline/health`,
      { signal: AbortSignal.timeout(1500), cache: "no-store" });
    if (!response.ok) throw new Error(`port ${port} 回傳 HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    const code = (error as { cause?: { code?: string } }).cause?.code;
    if (code === "ECONNREFUSED") return null;
    throw new Error(`無法確認 port ${port} 的服務身分：${error instanceof Error ? error.message : error}`);
  }
}

async function freePort(): Promise<number> {
  const server = createServer();
  return new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => address && typeof address !== "string"
        ? ok(address.port) : fail(new Error("no free port")));
    });
  });
}

export class LocalTaskService {
  private child: ChildProcess | null = null;
  private ownedInstance: string | null = null;
  private port: number | null = null;
  private healthValue: ServiceHealth | null = null;
  private errorValue: string | null = null;
  private warningValue: string | null = null;
  constructor(private settings: DesktopSettings, private tasksDir: string,
    private userData: string, private hostOrigin: string,
    private deps: ServiceDependencies = { probe, freePort, spawn,
      delay: ms => new Promise(ok => setTimeout(ok, ms)) }) {}

  get serviceOrigin() { return this.healthValue?.uiOrigin || this.hostOrigin; }
  get servicePort() { return this.port ?? 8765; }
  get status(): ServiceStatus { return { connected: this.healthValue !== null,
    owned: this.ownedInstance !== null, port: this.port, health: this.healthValue,
    error: this.errorValue, warning: this.warningValue }; }

  private async endpointPort(): Promise<number | null> {
    try {
      const saved = JSON.parse(await readFile(join(this.userData, "service-endpoint.json"), "utf8"));
      return Number.isSafeInteger(saved.port) && saved.port > 0 && saved.port < 65536
        ? saved.port : null;
    } catch { return null; }
  }

  private async remember(port: number, health: ServiceHealth) {
    await mkdir(this.userData, { recursive: true });
    await writeFile(join(this.userData, "service-endpoint.json"),
      JSON.stringify({ port, instanceId: health.instanceId }) + "\n");
  }

  async connect(): Promise<ServiceStatus> {
    if (!this.settings.matchesDir || !this.settings.backendDir)
      throw new Error("請先設定 matches 資料夾與 Python checkout");
    let incompatibleVersion: number | null = null;
    const candidates = [...new Set([this.port, await this.endpointPort(), 8765].filter(
      (port): port is number => typeof port === "number"))];
    for (const candidate of candidates) {
      let raw: unknown | null;
      try { raw = await this.deps.probe(candidate); }
      catch (error) { this.warningValue = error instanceof Error ? error.message : String(error); continue; }
      if (!raw) continue;
      try {
        const health = validateHandshake(raw, this.settings.matchesDir, this.tasksDir);
        this.port = candidate;
        this.healthValue = health;
        this.errorValue = null;
        this.warningValue = null;
        if (this.ownedInstance !== health.instanceId) this.ownedInstance = null;
        await this.remember(candidate, health);
        return this.status;
      } catch (error) {
        this.warningValue = error instanceof Error ? error.message : String(error);
        if (typeof raw === "object" && raw !== null) {
          const row = raw as Record<string, unknown>;
          const same = (value: unknown, target: string) => typeof value === "string" &&
            resolve(value).toLowerCase() === resolve(target).toLowerCase();
          if (row.service === "badminton-local-tasks" && typeof row.apiVersion === "number" &&
              row.apiVersion !== 2 && same(row.matchesRoot, this.settings.matchesDir) &&
              same(row.tasksRoot, this.tasksDir)) incompatibleVersion = row.apiVersion;
        }
      }
    }
    if (incompatibleVersion !== null)
      throw new Error(`舊版 Python 分析服務（API v${incompatibleVersion}）仍占用此 tasks 目錄。請先完成分析並退出舊版程式；若已退出，請停止殘留服務後重開桌面程式。`);
    const port = (await this.deps.probe(8765).catch(() => ({}))) === null
      ? 8765 : await this.deps.freePort();
    const args = ["run", "--no-sync", "python", "-m", "modules.local_tasks.service",
      "--host", "127.0.0.1", "--port", String(port), "--ui-origin", this.hostOrigin,
      "--matches-dir", this.settings.matchesDir, "--tasks-dir", this.tasksDir];
    try {
      this.child = this.deps.spawn(this.settings.uvBinary, args, { cwd: this.settings.backendDir,
        windowsHide: true, stdio: "ignore", env: { ...process.env,
          BADMINTON_MATCHES_DIR: this.settings.matchesDir, BADMINTON_TASKS_DIR: this.tasksDir } });
    } catch (error) {
      throw new Error(`無法啟動 Python 服務：${error instanceof Error ? error.message : error}`);
    }
    let spawnFailure = "";
    this.child.once("error", error => { spawnFailure = error.message; });
    for (let attempt = 0; attempt < 100; attempt++) {
      await this.deps.delay(100);
      if (spawnFailure) break;
      const raw = await this.deps.probe(port).catch(() => null);
      if (raw) {
        const health = validateHandshake(raw, this.settings.matchesDir, this.tasksDir);
        this.port = port; this.healthValue = health; this.ownedInstance = health.instanceId;
        this.errorValue = null; this.warningValue = null;
        await this.remember(port, health);
        return this.status;
      }
      if (this.child.exitCode !== null) break;
    }
    this.child = null;
    throw new Error(spawnFailure ? `無法啟動 Python 服務：${spawnFailure}`
      : "Python 服務未能啟動；請檢查 checkout 的 uv 環境與 tasks 目錄。");
  }

  async refresh(): Promise<ServiceStatus> {
    if (this.port === null) return this.status;
    try {
      const raw = await this.deps.probe(this.port);
      if (!raw) throw new Error("Python 服務未回應");
      const health = validateHandshake(raw, this.settings.matchesDir!, this.tasksDir);
      if (this.ownedInstance && health.instanceId !== this.ownedInstance) this.ownedInstance = null;
      this.healthValue = health; this.errorValue = null;
    } catch (error) {
      this.healthValue = null;
      this.errorValue = error instanceof Error ? error.message : String(error);
    }
    return this.status;
  }

  async stopOwnedIfIdle(): Promise<boolean> {
    await this.refresh();
    if (this.port !== null && !this.healthValue) return false;
    if (this.healthValue?.activeTask) return false;
    if (this.ownedInstance && this.child && this.healthValue?.instanceId === this.ownedInstance)
      this.child.kill();
    this.child = null; this.ownedInstance = null; this.healthValue = null; this.port = null;
    return true;
  }
}
