import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath } from "node:url";
import { localVideoPlugin } from "./scripts/video-plugin";
import { matchImportPlugin } from "./scripts/match-import-plugin";

export default defineConfig({
  server: { proxy: { "/api/pipeline": { target: "http://127.0.0.1:8765", changeOrigin: true } } },
  preview: { proxy: { "/api/pipeline": { target: "http://127.0.0.1:8765", changeOrigin: true } } },
  plugins: [
    vue(),
    localVideoPlugin(fileURLToPath(new URL(".", import.meta.url))),
    matchImportPlugin(fileURLToPath(new URL(".", import.meta.url))),
  ],
});
