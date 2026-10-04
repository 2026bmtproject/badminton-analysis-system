import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, renameSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { mediaTool } from "./media-tools";

const uiRoot = fileURLToPath(new URL("..", import.meta.url));
const output = resolve(uiRoot, "public/generated/demo.mp4");
if (existsSync(output)) {
  console.log(`Reusing ${output}; remove it explicitly to rebuild.`);
  process.exit(0);
}

const manifest = JSON.parse(
  readFileSync(resolve(uiRoot, "fixtures/manifest.json"), "utf8"),
) as { duration: number };
const segments = JSON.parse(
  readFileSync(resolve(uiRoot, "fixtures/stages/segments.json"), "utf8"),
) as { fps: number; segments: Array<{ start_sec: number; end_sec: number }> };
const events = JSON.parse(
  readFileSync(resolve(uiRoot, "fixtures/stages/events.json"), "utf8"),
) as { events: Array<{ frame: number }> };

const filters = [
  "drawbox=x=300:y=70:w=680:h=580:color=0x234a3b:t=fill",
  "drawbox=x=330:y=95:w=620:h=530:color=0x8ab5a0:t=2",
  "drawbox=x=350:y=95:w=580:h=530:color=0x8ab5a0:t=2",
  "drawbox=x=330:y=270:w=620:h=180:color=0x8ab5a0:t=2",
  "drawbox=x=638:y=95:w=2:h=530:color=0x8ab5a0:t=fill",
  "drawbox=x=315:y=358:w=650:h=4:color=white:t=fill",
  ...segments.segments.map(
    (segment, index) =>
      `drawbox=x=22:y=${24 + index * 18}:w=180:h=8:color=0x9bd7b6:t=fill:enable='between(t,${segment.start_sec},${segment.end_sec})'`,
  ),
  ...events.events.map((event, index) => {
    const time = event.frame / segments.fps;
    return `drawbox=x=${index % 2 ? 615 : 480}:y=${index % 2 ? 470 : 220}:w=20:h=20:color=0xdafa91:t=fill:enable='between(t,${time},${time + 0.7})'`;
  }),
];
const partial = resolve(uiRoot, "public/generated/demo.partial.mp4");
rmSync(partial, { force: true });
try {
  execFileSync(
    mediaTool("ffmpeg"),
    [
      "-hide_banner", "-loglevel", "error", "-y",
      "-f", "lavfi",
      "-i", `color=c=0x111e19:s=1280x720:r=${segments.fps}:d=${manifest.duration}`,
      "-vf", filters.join(","),
      "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "24",
      "-pix_fmt", "yuv420p", "-movflags", "+faststart", partial,
    ],
    { stdio: "inherit", cwd: uiRoot },
  );
  renameSync(partial, output);
  console.log(`Generated ${output}`);
} finally {
  rmSync(partial, { force: true });
}
