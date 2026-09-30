import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import { localVideoPlugin } from "./scripts/video-plugin";

export default defineConfig({
  plugins: [
    vue(),
    localVideoPlugin(fileURLToPath(new URL(".", import.meta.url))),
  ],
});
