// Текст и форматы — одна копия на окно, установщик, телефон и мини-апп.
// Ни строчки DOM: всё здесь можно прогнать в node (app/test/*).

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );

export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

export function fmtTimeSec(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDay(iso?: string | null): string {
  const d = new Date(iso ?? "");
  if (isNaN(d.getTime())) return "…";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const that = new Date(d);
  that.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - that.getTime()) / 86400000);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

// Данные приходят из дерева агента, которое продукт не контролирует: кривое
// число там — вопрос времени. Владельцу — честное «—», а не «NaN».
export const fmtN = (n: unknown): string => {
  if (n == null || n === "") return "";
  const v = Number(n);
  return isNaN(v) ? "—" : v.toLocaleString("ru-RU");
};

/** Короткая запись токенов: 12 345 → «12,3 тыс.», 1 234 567 → «1,2 млн». */
export const fmtK = (n: unknown): string => {
  const v = Number(n);
  if (n == null || n === "" || isNaN(v)) return "—";
  if (v >= 1_000_000) return (v / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " млн";
  if (v >= 10_000) return Math.round(v / 1000).toLocaleString("ru-RU") + " тыс.";
  if (v >= 1000) return (v / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " тыс.";
  return v.toLocaleString("ru-RU");
};

export const fmtTs = (ts?: number | string | null): string => {
  if (ts == null || ts === "") return "";
  const d = new Date(Number(ts) * 1000);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ru-RU");
};

export function fmtAge(mtime: unknown): string {
  const s = Date.now() / 1000 - Number(mtime);
  if (!isFinite(s)) return "—";
  if (s < 60) return "только что";
  if (s < 3600) return Math.round(s / 60) + " мин";
  if (s < 86400) return Math.round(s / 3600) + " ч";
  return Math.round(s / 86400) + " дн";
}

/** Длительность в секундах словами: 4 → «4 с», 95 → «1 мин 35 с», 3700 → «1 ч 2 мин». */
export function fmtDur(sec: number): string {
  if (!isFinite(sec) || sec < 0) return "—";
  if (sec < 60) return Math.round(sec) + " с";
  if (sec < 3600) return `${Math.floor(sec / 60)} мин ${Math.round(sec % 60)} с`;
  return `${Math.floor(sec / 3600)} ч ${Math.round((sec % 3600) / 60)} мин`;
}

export function plural(n: number, a: string, b: string, c: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return a;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
  return c;
}

/** Мини-маркдаун файлов и реплик агента: заголовки, списки, код, таблицы, цитаты. */
export function md(src: unknown): string {
  const lines = String(src || "").split("\n");
  const out: string[] = [];
  let inCode = false;
  let codeBuf: string[] = [];
  let listStack: "ul" | "ol" | null = null;
  let inQuote = false;
  let tableBuf: string[] = [];
  // Потолок стоит и на ОТДЕЛЬНОЙ строке, и на СУММЕ: текст сюда кладёт агент
  // или модель, и заметка-бомба замораживала окно (замер: 100 строк по 4000
  // знаков — 1,7 с главного потока). Дальше бюджета разметку не ищем.
  let budget = 32_000;
  const inline = (s: string) => {
    const safe = esc(s);
    // Квадратичный откат регэкспов на длинной строке с россыпью «[» — 6 с
    // заморозки на 78 КБ; на слишком длинной строке разметку не ищем вовсе.
    if (safe.length > 4000) return safe;
    budget -= safe.length;
    if (budget < 0) return safe;
    return safe
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  };
  const flushList = () => {
    if (listStack) {
      out.push(listStack === "ul" ? "</ul>" : "</ol>");
      listStack = null;
    }
  };
  const flushQuote = () => {
    if (inQuote) {
      out.push("</blockquote>");
      inQuote = false;
    }
  };
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = tableBuf.filter((r) => !/^\s*\|[\s\-:|]+\|\s*$/.test(r));
    out.push("<table>");
    rows.forEach((row, i) => {
      const cells = row.replace(/^\s*\||\|\s*$/g, "").split("|");
      out.push(
        "<tr>" + cells.map((c) => `<${i ? "td" : "th"}>${inline(c.trim())}</${i ? "td" : "th"}>`).join("") + "</tr>",
      );
    });
    out.push("</table>");
    tableBuf = [];
  };
  for (const raw of lines) {
    if (raw.startsWith("```")) {
      flushList();
      flushQuote();
      flushTable();
      if (inCode) {
        out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>");
        codeBuf = [];
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      codeBuf.push(raw);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(raw)) {
      flushList();
      flushQuote();
      tableBuf.push(raw);
      continue;
    }
    flushTable();
    const h = raw.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      flushList();
      flushQuote();
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      continue;
    }
    if (/^\s*([-*_]){3,}\s*$/.test(raw)) {
      flushList();
      flushQuote();
      out.push("<hr>");
      continue;
    }
    const qm = raw.match(/^>\s?(.*)/);
    if (qm) {
      flushList();
      if (!inQuote) {
        out.push("<blockquote>");
        inQuote = true;
      }
      out.push(inline(qm[1]) + "<br>");
      continue;
    }
    flushQuote();
    const li = raw.match(/^\s*([-*•]|\d+[.)])\s+(.*)/);
    if (li) {
      const kind: "ul" | "ol" = /^[-*•]/.test(li[1]) ? "ul" : "ol";
      if (listStack !== kind) {
        flushList();
        out.push(kind === "ul" ? "<ul>" : "<ol>");
        listStack = kind;
      }
      out.push("<li>" + inline(li[2]) + "</li>");
      continue;
    }
    flushList();
    if (!raw.trim()) {
      out.push("");
      continue;
    }
    out.push("<p>" + inline(raw) + "</p>");
  }
  if (inCode) out.push("<pre><code>" + esc(codeBuf.join("\n")) + "</code></pre>");
  flushList();
  flushQuote();
  flushTable();
  return out.join("\n");
}

// ------------------------------------------------------------------ отказы
//
// Сырьё («Failed to fetch», Python-эксепшн из канала, вывод netsh) владельцу не
// объяснение. Один словарь причин: человеческая фраза наверху, сырой текст —
// в складку «Подробности».

export interface HumanError {
  text: string;
  detail: string;
}

const OFFLINE_MARKS = ["failed to fetch", "networkerror", "load failed", "связь оборвалась", "нет ответа", "err_connection", "err_network"];

export function humanError(e: unknown): HumanError {
  const raw = (e instanceof Error ? e.message : String(e ?? "")).trim();
  const low = raw.toLowerCase();
  if (!raw) return { text: "Не получилось, а причину программа не назвала.", detail: "" };
  // «доступно только в приложении Hélène» и подобное уже написано по-русски.
  if (raw.startsWith("доступно только")) return { text: raw, detail: "" };
  if (OFFLINE_MARKS.some((m) => low.includes(m))) {
    return { text: "Агент не отвечает. Программа продолжает попытки сама.", detail: raw };
  }
  if (low.includes("filenotfounderror") || low.includes("winerror 2") || low.includes("winerror 3")) {
    return { text: "Этого файла больше нет — возможно, агент его убрал.", detail: raw };
  }
  if (low.includes("permissionerror") || low.includes("winerror 5")) {
    return { text: "Windows не дал сюда заглянуть.", detail: raw };
  }
  if (/\b40[13]\b/.test(raw)) return { text: "Канал не пустил: ключ доступа не подошёл.", detail: raw };
  if (/\b404\b/.test(raw)) return { text: "Программа попросила у агента то, чего он не знает.", detail: raw };
  if (/\b5\d\d\b/.test(raw)) return { text: "Не удалось прочитать память агента.", detail: raw };
  // Сырой Python-эксепшн из канала: `TypeError: …`, `OSError: …`.
  if (/^[A-Za-z_][A-Za-z_0-9]*(Error|Exception):/.test(raw)) {
    return { text: "Агент не смог это прочитать.", detail: raw };
  }
  return { text: "Не получилось.", detail: raw };
}
