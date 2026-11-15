import { t as uiText, locale, UiError } from "../lang/lang";
// Текст и форматы — одна копия на окно, установщик, телефон и мини-апп.
// Ни строчки DOM: всё здесь можно прогнать в node (app/test/*).

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );

const titleRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const titleString = (value: unknown): string => typeof value === "string" ? value : "";
const titlePoints = (value: string) => Array.from(value.slice(0, 280));

/** Render only producer-owned parts. Authored text equal to a caption stays literal. */
function titleRecipe(value: unknown): string {
  const r = titleRecord(value);
  if (!r || r.schema !== "helene.run-title.v1") return "";
  let text = "";
  if (r.kind === "operator") {
    const name = titleString(r.name);
    text = name ? uiText("run.title.operator", {name}) : uiText("run.title.operator_short");
  } else if (r.kind === "event") {
    const kind = titleString(r.event);
    text = kind === "wake" ? uiText("run.title.wake")
      : kind === "task_window" ? uiText("run.title.task")
      : kind === "chat_turn" ? uiText("run.title.self_event")
      : kind || uiText("run.title.agent_event");
  } else if (r.kind === "origin_unavailable") {
    if (!Array.isArray(r.message_ids) || r.message_ids.some(v => typeof v !== "string")) return "";
    const ids = r.message_ids.map(v => "#" + v).join(", ");
    text = ids ? uiText("run.title.missing_messages", {ids}) : uiText("run.title.missing_trigger");
  } else if (r.kind === "conversation") {
    if (!["name", "participant", "author"].includes(String(r.author_kind))) return "";
    const value = titleString(r.author), body = titleString(r.text);
    const author = r.author_kind === "name" ? value
      : r.author_kind === "participant" ? uiText("run.title.participant", {id: value}) : uiText("run.title.author");
    text = body ? uiText("run.title.conversation", {author, text: body}) : author;
  } else return "";
  const points = titlePoints(text);
  return r.truncated || points.length > 140 ? points.slice(0,139).join("") + "…" : text;
}

/** A legacy backend without ownership metadata keeps its original string. */
export function runTitle(value: unknown): string {
  const r = titleRecord(value);
  return r ? titleRecipe(r.display_title) || titleString(r.goal_head) : "";
}

export function runOriginText(value: unknown): string {
  const r = titleRecord(value);
  return r ? titleRecipe(r.display_title) || titleString(r.text) : "";
}

/** Only resource-owner outcomes with an explicit recipe receive client captions. */
export function runControlNote(value: unknown): string {
  const r = titleRecord(titleRecord(value)?.display_note);
  if (!r || r.schema !== "helene.run-control.v1") return "";
  const key = r.kind === "confirmed" ? "run.control.confirmed"
    : r.kind === "pending" ? "run.control.pending"
    : r.kind === "invalid_id" ? "run.control.invalid_id"
    : r.kind === "ambiguous_id" ? "run.control.ambiguous_id" : "";
  return key ? uiText(key) : "";
}

const NOTICE_FIELDS: Record<string, readonly string[]> = {
  speech_failed:[], speech_unknown:[], turn_incomplete:[], files_pending:["count","run"],
  paused:["run"], turn_failed:["run"], silence:["agent"], silence_reason:["agent","reason"],
  closed:[], closed_file:[], tree_busy:["agent","pid"], birth_silent:["agent"], birth_failed:["tries"],
  resume_refused:["reason"], note_gave_up:["name","tries","path"], note_rejected:["name","reason"],
};

/** New producer-owned notices follow this client's UI; legacy history stays literal. */
export function systemNoticeText(value: unknown): string {
  const message=titleRecord(value),r=titleRecord(message?.display_notice),values=titleRecord(r?.values);
  if(message?.system!==true||!r||Object.keys(r).sort().join(",")!=="kind,schema,values"
    ||r.schema!=="helene.product-notice.v1"||typeof r.kind!=="string"||!Object.hasOwn(NOTICE_FIELDS,r.kind)||!values)return "";
  const fields=NOTICE_FIELDS[r.kind];
  if(Object.keys(values).length!==fields.length||fields.some(key=>!Object.hasOwn(values,key))
    ||Object.values(values).some(v=>typeof v!=="string"&&(typeof v!=="number"||!Number.isFinite(v))))return "";
  return uiText("notice."+r.kind,values as Record<string,string|number>);
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
}

export function fmtTimeSec(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Calendar classification stays independent of translated date captions. */
export function isToday(iso?: string | null): boolean {
  const date = new Date(iso ?? "");
  const now = new Date();
  return date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

export function fmtDay(iso?: string | null): string {
  const d = new Date(iso ?? "");
  if (isNaN(d.getTime())) return "…";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const that = new Date(d);
  that.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - that.getTime()) / 86400000);
  if (diff === 0) return uiText("ui.ui.kit.text.b688b4c0d7");
  if (diff === 1) return uiText("ui.ui.kit.text.6bc6210fc2");
  return d.toLocaleDateString(locale(), { day: "numeric", month: "long" });
}

// Данные приходят из дерева агента, которое продукт не контролирует: кривое
// число там — вопрос времени. Владельцу — честное «—», а не «NaN».
export const fmtN = (n: unknown): string => {
  if (n == null || n === "") return "";
  const v = Number(n);
  return isNaN(v) ? "—" : v.toLocaleString(locale());
};

/** Короткая запись токенов: 12 345 → «12,3 тыс.», 1 234 567 → «1,2 млн». */
export const fmtK = (n: unknown): string => {
  const v = Number(n);
  if (n == null || n === "" || !Number.isFinite(v)) return "—";
  return new Intl.NumberFormat(locale(), { notation: 'compact', maximumFractionDigits: 1 }).format(v);
};

export const fmtTs = (ts?: number | string | null): string => {
  if (ts == null || ts === "") return "";
  const d = new Date(Number(ts) * 1000);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString(locale());
};

export function fmtAge(mtime: unknown): string {
  const s = Date.now() / 1000 - Number(mtime);
  if (!isFinite(s)) return "—";
  if (s < 60) return uiText("ui.ui.kit.text.556f1e91d5");
  if (s < 3600) return Math.round(s / 60) + uiText("ui.ui.kit.text.84d5d93d12");
  if (s < 86400) return Math.round(s / 3600) + uiText("ui.ui.kit.text.e34d885124");
  return Math.round(s / 86400) + uiText("ui.ui.kit.text.37e04cffd9");
}

/** Длительность в секундах словами: 4 → «4 с», 95 → «1 мин 35 с», 3700 → «1 ч 2 мин». */
export function fmtDur(sec: number): string {
  if (!isFinite(sec) || sec < 0) return "—";
  if (sec < 60) return Math.round(sec) + uiText("ui.ui.kit.text.ddae8eb8bf");
  if (sec < 3600) return uiText("ui.ui.kit.text.aef6c8dfde", {p0: String(Math.floor(sec / 60)), p1: String(Math.round(sec % 60))});
  return uiText("ui.ui.kit.text.c463a3b13d", {p0: String(Math.floor(sec / 3600)), p1: String(Math.round((sec % 3600) / 60))});
}

export function plural(n: number, a: string, b: string, c: string): string {
  const form = new Intl.PluralRules(locale()).select(n);
  return form === 'one' ? a : form === 'few' ? b : c;
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
    // Ссылки — до остальной разметки и через заглушки: `_` и `*` внутри адреса
    // иначе читались как курсив, а голый https://… (так чаще всего цитирует
    // модель и так пишут люди) вообще не становился ссылкой. Заглушка —
    // управляющий символ U+0001: в тексте реплик его не бывает, а пробел и
    // цифра бывают, и они бы подменялись ссылками.
    const links: string[] = [];
    const hold = (href: string, label: string) => {
      links.push(`<a href="${href}" target="_blank" rel="noopener">${label}</a>`);
      return `\u0001${links.length - 1}\u0001`;
    };
    const marked = safe
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, (_m, label: string, href: string) => hold(href, label))
      .replace(/(^|[\s(>])(https?:\/\/[^\s<>"']+?)([.,;:!?)]*)(?=$|[\s<])/g, (_m, pre: string, href: string, tail: string) => pre + hold(href, href) + tail);
    return marked
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
      .replace(/\u0001(\d+)\u0001/g, (_m, i: string) => links[Number(i)] ?? "");
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
  if (e instanceof UiError && raw) return { text: raw, detail: "" };
  const low = raw.toLowerCase();
  if (!raw) return { text: uiText("ui.ui.kit.text.b705d031e4"), detail: "" };
  // «доступно только в приложении Hélène» и подобное уже написано по-русски.
  if (raw.startsWith("доступно только")) return { text: raw, detail: "" };
  if (OFFLINE_MARKS.some((m) => low.includes(m))) {
    return { text: uiText("ui.ui.kit.text.67f604aa87"), detail: raw };
  }
  if (low.includes("filenotfounderror") || low.includes("winerror 2") || low.includes("winerror 3")) {
    return { text: uiText("ui.ui.kit.text.0123487c98"), detail: raw };
  }
  // «Система», а не «Windows»: та же строка едет и на macOS (порт 0.7.1).
  if (low.includes("permissionerror") || low.includes("winerror 5")) {
    return { text: uiText("ui.ui.kit.text.cf15b1a364"), detail: raw };
  }
  if (/\b40[13]\b/.test(raw)) return { text: uiText("ui.ui.kit.text.600debbfff"), detail: raw };
  if (/\b404\b/.test(raw)) return { text: uiText("ui.ui.kit.text.32407f31e1"), detail: raw };
  if (/\b5\d\d\b/.test(raw)) return { text: uiText("ui.ui.kit.text.619eb5a1fd"), detail: raw };
  // Сырой Python-эксепшн из канала: `TypeError: …`, `OSError: …`.
  if (/^[A-Za-z_][A-Za-z_0-9]*(Error|Exception):/.test(raw)) {
    return { text: uiText("ui.ui.kit.text.3de4d3c410"), detail: raw };
  }
  // 12.11: our own replies are already written for a person («Сначала включи Stop…»,
  // «На компьютере: …»). Flattening them to «Не получилось.» hid the actual reason.
  // OS errors and tracebacks still go to the fold.
  if (/[А-Яа-яЁё]/.test(raw) && !/^\[(Errno|WinError) -?\d+\]|Traceback|File "|^\{/.test(raw)) {
    return { text: raw, detail: "" };
  }
  return { text: uiText("ui.ui.kit.text.00d268f7d8"), detail: raw };
}
