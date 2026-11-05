import { defineConfig } from "vite";
import { devOverride } from "../ui-kit/vite-dev";

// Compatibility shell for /m/ bookmarks. The bot provides the current entry.
export default defineConfig({
  base: "/m/",
  clearScreen: false,
  plugins: [devOverride()],
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
