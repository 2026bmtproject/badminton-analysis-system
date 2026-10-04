import { existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(typeof __filename !== "undefined" ? __filename : import.meta.url);

export type MediaTool = "ffmpeg" | "ffprobe";

const bundled: Record<MediaTool, string | undefined> = {
  ffmpeg: require("@ffmpeg-installer/ffmpeg")?.path,
  ffprobe: require("ffprobe-static")?.path,
};

const environmentName: Record<MediaTool, string> = {
  ffmpeg: "FFMPEG_BINARY",
  ffprobe: "FFPROBE_BINARY",
};

/** Resolve an explicit override first, then the project bundle, then PATH. */
export function mediaTool(tool: MediaTool): string {
  const configured = process.env[environmentName[tool]]?.trim();
  if (configured) return configured;
  const projectTool = bundled[tool];
  if (projectTool && existsSync(projectTool)) return projectTool;
  return tool;
}
