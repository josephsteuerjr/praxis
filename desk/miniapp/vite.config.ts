import { defineConfig } from "vite";
import { readFileSync } from "node:fs";
import { devOverride } from "../ui-kit/vite-dev";

// Мини-апп Telegram. Хостится своим сайтом (Caddy: статика + прокси /api,
// /pair, /events, /tunnel на канал) — относительные пути, чтобы сборка жила
// в любом корне. ui-kit лежит выше корня — fs.allow.
export default defineConfig({
  base: "./",
  clearScreen: false,
  plugins: [devOverride(), {name:'shared-program-shell', transformIndexHtml: {order:'pre', handler(html) {
    const desktop=readFileSync(new URL('../app/index.html',import.meta.url),'utf8');
    const body=desktop.match(/<body>([\s\S]*?)<\/body>/)?.[1];
    if(!body)throw Error('Desktop shell markup missing');
    return html.replace(/<body>[\s\S]*?<\/body>/,'<body>'+body+'</body>');
  }}}],
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
