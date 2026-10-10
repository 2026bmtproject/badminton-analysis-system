import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFile, mkdir, rename, stat, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

export type DesktopSettings = {
  matchesDir: string | null;
  backendDir: string | null;
  uvBinary: string;
};

export function defaultSettings(developmentRoot?: string): DesktopSettings {
  return {
    matchesDir: process.env.BADMINTON_MATCHES_DIR || null,
    backendDir: process.env.BADMINTON_BACKEND_DIR || developmentRoot || null,
    uvBinary: process.env.UV_BINARY || "uv",
  };
}

export async function readSettings(userData: string, defaults: DesktopSettings): Promise<DesktopSettings> {
  try {
    const stored = JSON.parse(await readFile(join(userData, "settings.json"), "utf8"));
    return {
      matchesDir: typeof stored.matchesDir === "string" ? stored.matchesDir : defaults.matchesDir,
      backendDir: typeof stored.backendDir === "string" ? stored.backendDir : defaults.backendDir,
      uvBinary: typeof stored.uvBinary === "string" && stored.uvBinary.trim()
        ? stored.uvBinary : defaults.uvBinary,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return defaults;
    throw new Error("桌面設定檔無法讀取，請檢查 userData/settings.json", { cause: error });
  }
}

export async function saveSettings(userData: string, value: DesktopSettings): Promise<void> {
  await mkdir(userData, { recursive: true });
  const destination = join(userData, "settings.json");
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, JSON.stringify(value, null, 2) + "\n", "utf8");
  await rename(temporary, destination);
}

export function profileRoot(userData: string, matchesDir: string): string {
  const key = createHash("sha256").update(resolve(matchesDir).toLowerCase()).digest("hex").slice(0, 16);
  return join(userData, "profiles", key);
}

async function isFile(path: string) { return (await stat(path).catch(() => null))?.isFile() ?? false; }
async function isDirectory(path: string) { return (await stat(path).catch(() => null))?.isDirectory() ?? false; }

export async function validateSettings(value: DesktopSettings): Promise<string | null> {
  if (!value.matchesDir) return "請先選擇 matches 資料夾。";
  if (!await isDirectory(value.matchesDir)) return "matches 資料夾不存在或無法讀取。";
  if (!value.backendDir) return "請選擇 Python 專案 checkout。";
  if (!await isFile(join(value.backendDir, "pyproject.toml")) ||
      !await isFile(join(value.backendDir, "modules", "local_tasks", "service.py")))
    return "所選 Python checkout 缺少 local_tasks 服務。";
  if (!await isFile(join(value.backendDir, ".venv", "Scripts", "python.exe")))
    return "Python checkout 缺少 .venv\\Scripts\\python.exe；請先在該 checkout 準備 uv 環境。";
  const uv = spawnSync(value.uvBinary, ["--version"], { encoding: "utf8", timeout: 5000,
    windowsHide: true });
  if (uv.error || uv.status !== 0) return "找不到 uv 執行檔；請指定 uv.exe 或確認 uv 在 PATH。";
  return null;
}
