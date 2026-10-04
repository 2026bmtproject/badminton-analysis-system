import { packager } from "@electron/packager";
import { resolve } from "node:path";

const uiRoot = resolve(import.meta.dirname, "..");
const out = process.env.BADMINTON_DESKTOP_OUT?.trim()
  ? resolve(process.env.BADMINTON_DESKTOP_OUT) : resolve(uiRoot, "desktop-out");
const output = await packager({
  dir: uiRoot,
  name: "BadmintonReview",
  executableName: "BadmintonReview",
  platform: "win32",
  arch: "x64",
  out,
  overwrite: true,
  asar: false,
  prune: true,
  ignore: [
    /^[/\\](?:desktop-out|src|scripts|tests|fixtures|public|generated|\.local)(?:[/\\]|$)/,
    /^[/\\]desktop[/\\].*\.ts$/,
    /^[/\\](?:tsconfig\.json|vite\.config\.ts|package-lock\.json)$/,
  ],
});
console.log(output.join("\n"));
