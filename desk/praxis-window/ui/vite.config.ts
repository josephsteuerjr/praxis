import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Адрес сервера для локальной разработки — тот же, что у оболочки: electron/server.json
// рядом с программой (образец — server.example.json). Сервер разработки ходит
// на /api и /pair прямо туда, страница в браузере — к нему же.
function serverOrigin(): string {
  const file = fileURLToPath(new URL("../electron/server.json", import.meta.url));
  try {
    return new URL(JSON.parse(readFileSync(file, "utf8").replace(/^\uFEFF/, "")).server).origin;
  } catch {
    return "https://praxis.203.0.113.10.nip.io";
  }
}
const target = serverOrigin();

// Окно Пракс. Страница живёт на её сервере по адресу /assets/praxis-window/, поэтому
// все пути относительные; её телефонный набор (ui-kit) — материал, из которого оно собрано.
export default defineConfig({
  base: "./",
  clearScreen: false,
  build: { outDir: "dist", emptyOutDir: true, target: "es2022", assetsInlineLimit: 0 },
  server: {
    port: 5177,
    strictPort: true,
    fs: { allow: [".."] },
    proxy: {
      "/api": { target, changeOrigin: true, secure: true },
      "/pair": { target, changeOrigin: true, secure: true },
    },
  },
});
