import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { Plugin } from "vite";

// Превью против чужого канала (Praxis на VPS): адрес, ключ и имя
// агента приходят из среды dev-сервера — HELENE_DEV_BASE, HELENE_DEV_KEY,
// HELENE_DEV_AGENT — и подставляются в страницу как window.DESK_CONFIG_OVERRIDE.
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
      return html.replace("<head>", `<head><script>window.DESK_CONFIG_OVERRIDE = ${cfg};</script>`);
    },
  };
}

/**
 * Штамп сборки в service worker: `__BUILD__` в `dist/sw.js` заменяется на
 * время сборки. Новая сборка = новые байты sw.js = браузер ставит новый SW,
 * а тот на activate выбрасывает старый кэш оболочки (ui-kit/version.ts —
 * вторая половина принудительного обновления).
 */
export function stampServiceWorker(outDir = "dist"): Plugin {
  return {
    name: "helene-stamp-sw",
    apply: "build",
    closeBundle() {
      const p = join(process.cwd(), outDir, "sw.js");
      if (!existsSync(p)) return;
      const stamp = Date.now().toString(36);
      writeFileSync(p, readFileSync(p, "utf8").replace(/__BUILD__/g, stamp));
    },
  };
}
