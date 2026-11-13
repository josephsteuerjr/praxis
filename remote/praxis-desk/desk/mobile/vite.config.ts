import { defineConfig } from "vite";
import { devOverride, stampServiceWorker } from "../ui-kit/vite-dev";

// PWA для телефона. Отдаётся deskapp по пути /m/ с того же origin, что и API:
// один канал, один ключ устройства. ui-kit лежит выше корня — fs.allow.
export default defineConfig({
  base: "/m/",
  clearScreen: false,
  plugins: [devOverride(), stampServiceWorker()],
  server: {
    port: 5175,
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
