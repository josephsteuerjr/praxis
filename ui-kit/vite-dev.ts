import type { Plugin } from "vite";

// Превью против чужого канала (Пульт Праксис на VPS): адрес, ключ и имя
// агента приходят из среды dev-сервера — HELENE_DEV_BASE, HELENE_DEV_KEY,
// HELENE_DEV_AGENT — и подставляются в страницу как window.PULT_CONFIG_OVERRIDE.
// В сборку не попадает: плагин работает только у dev-сервера, ключ в файлы
// не пишется. Один плагин на окно, телефон и мини-апп.
export function devOverride(): Plugin {
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
