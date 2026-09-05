import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

const HERE = dirname(fileURLToPath(import.meta.url));
// Один источник правды на описания режимов: питоновский модуль харнесса.
// Установщик — нативный exe и питон при выборе режима не запускает, поэтому
// тексты приезжают сюда НА СБОРКЕ. Своей копии текстов в установщике нет
// намеренно: разошлись бы описания — владелец выбирал бы одно, а получал другое.
const MODES_PY = resolve(HERE, "../../localharness/modes.py");
const MODES_ID = "virtual:helene-modes";

/** Все строковые литералы куска питона подряд: неявная склейка ("a" "b") = "ab". */
function joinLiterals(chunk: string, what: string): string {
  const out: string[] = [];
  const re = /"((?:[^"\\]|\\.)*)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(chunk))) {
    try {
      out.push(JSON.parse(`"${m[1]}"`) as string);
    } catch {
      out.push(m[1]);
    }
  }
  if (!out.length) throw new Error(`modes.py: нет текста там, где он ожидался — ${what}`);
  return out.join("");
}

/** Тело словаря верхнего уровня: от `{` до строки `}` в первой колонке. */
function dictBody(src: string, name: string): string {
  const at = src.search(new RegExp(`^${name}\\s*(?::[^=\\n]*)?=\\s*\\{`, "m"));
  if (at < 0) throw new Error(`modes.py: не нашёл словарь ${name}`);
  const open = src.indexOf("{", at);
  const close = src.indexOf("\n}", open);
  if (close < 0) throw new Error(`modes.py: словарь ${name} не закрыт`);
  return src.slice(open + 1, close);
}

/** Значения словаря по известным ключам. Границей записи считаем следующий
 *  ключ из списка: значения бывают многострочные и в скобках, и разбирать
 *  питон целиком ради трёх строк незачем. */
function dictValues(src: string, name: string, keys: string[]): Record<string, string> {
  const body = dictBody(src, name);
  const out: Record<string, string> = {};
  for (const key of keys) {
    const head = `"${key}":`;
    const at = body.indexOf(head);
    if (at < 0) throw new Error(`modes.py: в ${name} нет ключа ${key}`);
    let end = body.length;
    for (const other of keys) {
      if (other === key) continue;
      const p = body.indexOf(`"${other}":`);
      if (p > at && p < end) end = p;
    }
    out[key] = joinLiterals(body.slice(at + head.length, end), `${name}["${key}"]`);
  }
  return out;
}

/** Значение простой константы верхнего уровня: `NAME = "…" "…"`.
 *  Значение кончается на первой строке, начинающейся не с пробела, — то есть
 *  на следующей константе, комментарии или пустой строке.
 *
 *  Якорь `^` обязателен: имена этих же констант поминаются в докстринге модуля,
 *  и поиск подстрокой находил там абзац прозы вместо значения. */
function constantChunk(src: string, name: string): string {
  const at = src.search(new RegExp(`^${name}\\s*(?::[^=\\n]*)?=`, "m"));
  if (at < 0) throw new Error(`modes.py: не нашёл ${name}`);
  const rest = src.slice(src.indexOf("=", at) + 1);
  const end = rest.search(/\n(?=\S)|\n[ \t]*\n/);
  return end < 0 ? rest : rest.slice(0, end);
}

function constantText(src: string, name: string): string {
  return joinLiterals(constantChunk(src, name), name);
}

function constantBool(src: string, name: string): boolean {
  const raw = constantChunk(src, name).trim();
  if (raw !== "True" && raw !== "False") {
    throw new Error(`modes.py: ${name} = ${raw} — здесь ожидался True или False`);
  }
  return raw === "True";
}

/** Модуль `virtual:helene-modes` — ДВА ИЗМЕРЕНИЯ, а не один список из трёх.
 *
 *  `MODE_CARDS` — ограда рук (песочница | интерактивный), `SERVICE_OPTION` —
 *  опция службы поверх любой из них. Склеивать их обратно нельзя: из склейки
 *  вырос P0 (у владельца со службой И песочницей миграция выводила «service»,
 *  а «service» означал «ограды нет» — ограда снималась молча).
 *
 *  Тексты — из localharness/modes.py, разбор не удался — сборка ПАДАЕТ: молча
 *  подставить свои слова здесь хуже, чем не собраться. */
function modesFromPython(): Plugin {
  return {
    name: "helene-modes-from-python",
    resolveId(id) {
      return id === MODES_ID ? `\0${MODES_ID}` : null;
    },
    configureServer(server) {
      server.watcher.add(MODES_PY);
    },
    load(id) {
      if (id !== `\0${MODES_ID}`) return null;
      this.addWatchFile(MODES_PY);
      const py = readFileSync(MODES_PY, "utf8");

      // Порядок ограды — от самой запертой к самой свободной, он же порядок
      // карточек на экране. Сверяемся с MODES: появится третья ограда (или
      // вернётся склейка со службой) — установщик не соберётся, а не покажет
      // владельцу одно, записав в конфиг другое.
      const tuple = /^MODES[^=]*=\s*\(([^)]*)\)/m.exec(py);
      if (!tuple) throw new Error("modes.py: не нашёл MODES");
      const order = [...tuple[1].matchAll(/"(\w+)"/g)].map((m) => m[1]);
      const want = ["sandbox", "interactive"];
      if (order.join(",") !== want.join(",")) {
        throw new Error(
          `modes.py: MODES = [${order.join(", ")}], а экран установщика знает [${want.join(", ")}] — ` +
            "поправь setup/ui/src/scenes/mode.ts и этот список. Если в списке снова появилась " +
            "«служба» — она не ограда, а опция поверх любой из них (SERVICE_OPTION).",
        );
      }

      const titles = dictValues(py, "TITLES", order);
      const texts = dictValues(py, "TEXTS", order);
      const cards = order.map((name) => ({
        name,
        title: titles[name],
        text: texts[name],
        // Ни одна ограда прав администратора не требует: и per-user установка,
        // и профиль AppContainer, и icacls на свои папки обходятся без него.
        // Админ нужен опции службы — она ниже и отдельно.
        sandbox: name === "sandbox",
      }));

      // Опция службы: зеркало modes.service_option(). Ключи галочек — те же,
      // что читают служба (`service.session0`) и харнесс (`service.firewall`).
      const session0Warning = constantText(py, "SESSION0_WARNING");
      const option = {
        title: constantText(py, "SERVICE_TITLE"),
        text: constantText(py, "SERVICE_TEXT"),
        toggles: [
          {
            key: "service.session0",
            title: constantText(py, "SESSION0_TITLE"),
            text: constantText(py, "SESSION0_TEXT"),
            warning: session0Warning,
            default: false,
          },
          {
            key: "service.firewall",
            title: constantText(py, "FIREWALL_TITLE"),
            text: constantText(py, "FIREWALL_TEXT"),
            warning: "",
            default: constantBool(py, "FIREWALL_DEFAULT"),
          },
        ],
      };
      return (
        "// собрано из localharness/modes.py плагином helene-modes-from-python\n" +
        `export const MODE_CARDS = ${JSON.stringify(cards, null, 2)};\n` +
        `export const SERVICE_OPTION = ${JSON.stringify(option, null, 2)};\n` +
        `export const SESSION0_WARNING = ${JSON.stringify(session0Warning)};\n`
      );
    },
  };
}

// base './' — ассеты грузятся относительными путями и в dev-сервере, и из exe
// (Tauri отдаёт dist со своего origin). Ничего внешнего: шрифты вшиты.
export default defineConfig({
  base: "./",
  clearScreen: false,
  plugins: [modesFromPython()],
  // resources/SOUL.md лежит выше корня UI: одна конституция на установщик и boot.py.
  server: { port: 5173, strictPort: true, fs: { allow: ["../.."] } },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    target: "chrome120",
    assetsInlineLimit: 0,
  },
});
