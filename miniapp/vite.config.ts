import { defineConfig } from "vite";
import { devOverride } from "../ui-kit/vite-dev";

// Мини-апп Telegram. Хостится своим сайтом (Caddy: статика + прокси /api,
// /pair, /events, /tunnel на канал) — относительные пути, чтобы сборка жила
// в любом корне. ui-kit лежит выше корня — fs.allow.
export default defineConfig({
  base: "./",
  clearScreen: false,
  plugins: [devOverride()],
  server: {
    port: 5176,
    strictPort: true,
    fs: { allow: [".."] },
    proxy: {
      "/api": "http://127.0.0.1:8094",
      "/pair": "http://127.0.0.1:8094",
      "/events": "http://127.0.0.1:8094",
      "/tunnel": { target: "ws://127.0.0.1:8094", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, target: "es2020", assetsInlineLimit: 0 },
});
