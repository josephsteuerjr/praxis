import { defineConfig } from "vite";
import { devOverride } from "../ui-kit/vite-dev";

// UI основной программы. base './' — те же файлы живут и в exe (Tauri), и
// за deskapp по HTTP; ui-kit лежит выше корня, поэтому fs.allow.
// Превью против чужого канала — ui-kit/vite-dev.ts (только dev-сервер).
export default defineConfig({
  base: "./",
  clearScreen: false,
  plugins: [devOverride()],
  // В превью API и канал идут на локальный deskapp: те же пути, что в exe.
  server: {
    port: 5174,
    strictPort: true,
    fs: { allow: [".."] },
    proxy: {
      "/api": "http://127.0.0.1:8094",
      "/pair": "http://127.0.0.1:8094",
      "/events": "http://127.0.0.1:8094",
      "/tunnel": { target: "ws://127.0.0.1:8094", ws: true },
    },
  },
  // ui-kit лежит ВЫШЕ корня пакета, и `@tauri-apps/api` из его файлов Node
  // ищет вверх по дереву, мимо node_modules этого пакета: на машине сборки
  // его спасал случайный node_modules в домашней папке, на раннере — ничем.
  // dedupe заставляет брать пакет из корня проекта; tsconfig — paths для tsc.
  resolve: { dedupe: ["@tauri-apps/api"] },
  build: { outDir: "dist", emptyOutDir: true, target: "chrome120", assetsInlineLimit: 0 },
});
