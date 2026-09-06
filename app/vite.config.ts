import { defineConfig, type Plugin } from "vite";

// UI основной программы. base './' — те же файлы живут и в exe (Tauri), и
// за deskapp по HTTP; ui-kit лежит выше корня, поэтому fs.allow.
//
// Превью против чужого канала (Пульт Праксис на VPS, задача B 07.09): в dev
// адрес и ключ приходят из среды — HELENE_DEV_BASE и HELENE_DEV_KEY — и
// подставляются в страницу как window.PULT_CONFIG_OVERRIDE. В сборку это не
// попадает: плагин работает только у dev-сервера, ключ в файлы не пишется.
function devOverride(): Plugin {
  return {
    name: "helene-dev-override",
    apply: "serve",
    transformIndexHtml(html) {
      const base = (process.env.HELENE_DEV_BASE || "").trim().replace(/\/+$/, "");
      const key = (process.env.HELENE_DEV_KEY || "").trim();
      const agent = (process.env.HELENE_DEV_AGENT || "").trim();
      if (!base) return html;
      const cfg = JSON.stringify({ base, key, agent });
      return html.replace("<head>", `<head><script>window.PULT_CONFIG_OVERRIDE = ${cfg};</script>`);
    },
  };
}

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
  build: { outDir: "dist", emptyOutDir: true, target: "chrome120", assetsInlineLimit: 0 },
});
