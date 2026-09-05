// Мелкие общие вещи: экранирование, мини-маркдаун, форматы, DOM.

export const esc = (s: unknown): string =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );

export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
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
// число там — вопрос времени. Правило то же, что у fmtTime/fmtDay выше: не
// показывать владельцу «Invalid Date», «NaN мин» и «не число», а честное «—».
export const fmtN = (n: unknown): string => {
  if (n == null || n === "") return "";
  const v = Number(n);
  return isNaN(v) ? "—" : v.toLocaleString("ru-RU");
};

export const fmtTs = (ts?: number | string | null): string => {
  if (ts == null || ts === "") return "";
  const d = new Date(Number(ts) * 1000);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ru-RU");
};

export function fmtAge(mtime: unknown): string {
  const s = Date.now() / 1000 - Number(mtime);
  if (!isFinite(s)) return "—";
  if (s < 3600) return Math.round(s / 60) + " мин";
  if (s < 86400) return Math.round(s / 3600) + " ч";
  return Math.round(s / 86400) + " дн";
}

export function plural(n: number, a: string, b: string, c: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return a;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
  return c;
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function q<E extends Element = HTMLElement>(sel: string, root: ParentNode = document): E {
  const node = root.querySelector<E>(sel);
  if (!node) throw new Error(`нет элемента ${sel}`);
  return node;
}

/** Мини-маркдаун её файлов и реплик: заголовки, списки, код, таблицы, цитаты. */
export function md(src: unknown): string {
  const lines = String(src || "").split("\n");
  const out: string[] = [];
  let inCode = false;
  let codeBuf: string[] = [];
  let listStack: "ul" | "ol" | null = null;
  let inQuote = false;
  let tableBuf: string[] = [];
  // Потолок стоит и на ОТДЕЛЬНОЙ строке, и на СУММЕ. Кэпа на строку мало:
  // md() бьёт вход по «\n» и зовёт inline() на каждой строке, а число строк
  // ничем не ограничено. Замер на настоящем lib.ts: 25 строк по 4000 знаков —
  // 368 мс, 50 — 873 мс, 100 (400 КБ, ровно потолок чтения файла на трубе) —
  // 1679 мс заморозки главного потока. Текст сюда кладёт агент или модель, и
  // заметка-бомба замораживала окно при обычном открытии в «Файлах».
  // Дальше бюджета разметку не ищем: текст показывается как есть.
  let budget = 32_000;
  const inline = (s: string) => {
    const safe = esc(s);
    // Одна длинная строка с россыпью «[» роняла главный поток на секунды:
    // у /\[([^\]]+)\]\(…\)/ квадратичный откат (78 КБ = 6.1 с заморозки окна),
    // и то же по «*» и «`». Текст сюда приходит из дерева агента и от модели —
    // продукт его не контролирует, поэтому на слишком длинной строке разметку
    // просто не ищем: лучше показать её как есть, чем заморозить окно.
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
        "<tr>" +
          cells.map((c) => `<${i ? "td" : "th"}>${inline(c.trim())}</${i ? "td" : "th"}>`).join("") +
          "</tr>",
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

let toastTimer = 0;
let toastHideTimer = 0;
let toastBound = false;
export function toast(text: string) {
  const box = document.getElementById("toast");
  if (!box) return;
  if (!toastBound) {
    toastBound = true;
    // Текст тоста теперь можно выделить и скопировать (было pointer-events:none),
    // а значит он ловит клики — и должен уметь уйти с дороги композера сам.
    // Клик прячет его, но не когда владелец что-то выделяет.
    box.addEventListener("click", () => {
      if ((window.getSelection()?.toString() || "").trim()) return;
      clearTimeout(toastTimer);
      clearTimeout(toastHideTimer);
      box.classList.remove("show");
      toastHideTimer = window.setTimeout(() => (box.hidden = true), 300);
    });
  }
  box.textContent = text;
  box.hidden = false;
  box.classList.add("show");
  clearTimeout(toastTimer);
  // Раньше id вложенного таймера не сохранялся, и чужой таймер гасил новый тост
  // через ~200 мс: при пачке ошибок владелец не видел ни буквы.
  clearTimeout(toastHideTimer);
  // Время показа от длины: 2.8 с хватает на «Сохранено», но не на 160 знаков —
  // это ~58 знаков в секунду, втрое быстрее чтения.
  const ms = Math.min(12000, Math.max(2800, Math.round(text.length * 55)));
  toastTimer = window.setTimeout(() => {
    box.classList.remove("show");
    toastHideTimer = window.setTimeout(() => (box.hidden = true), 300);
  }, ms);
}

// ------------------------------------------------------------------ отказы
//
// Сырьё («Failed to fetch», Python-эксепшн из трубы, вывод netsh) владельцу не
// объяснение. Один словарь причин на всю программу: человеческая фраза наверху,
// сырой текст — в складке «Подробности», откуда его можно скопировать.

export interface HumanError {
  text: string;
  detail: string;
}

const OFFLINE_MARKS = [
  "failed to fetch",
  "networkerror",
  "load failed",
  "связь оборвалась",
  "нет ответа",
  "err_connection",
  "err_network",
];

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
  if (/\b40[13]\b/.test(raw)) return { text: "Труба не пустила: ключ доступа не подошёл.", detail: raw };
  if (/\b404\b/.test(raw)) return { text: "Программа попросила у агента то, чего он не знает.", detail: raw };
  if (/\b5\d\d\b/.test(raw)) return { text: "Не удалось прочитать память агента.", detail: raw };
  // Сырой Python-эксепшн из трубы: `TypeError: …`, `OSError: …`.
  if (/^[A-Za-z_][A-Za-z_0-9]*(Error|Exception):/.test(raw)) {
    return { text: "Агент не смог это прочитать.", detail: raw };
  }
  return { text: "Не получилось.", detail: raw };
}

/** Экран/блок отказа: фраза, «Повторить» и складка с сырым текстом. */
export function failHTML(e: unknown, opts: { retry?: boolean } = {}): string {
  const h = humanError(e);
  const retry = opts.retry === false ? "" : '<button class="btn btn-quiet" data-fail-retry type="button">Повторить</button>';
  const detail = h.detail
    ? `<details class="fail-detail"><summary>Подробности</summary><pre class="mono">${esc(h.detail)}</pre>` +
      '<button class="btn btn-quiet" data-fail-copy type="button">Скопировать</button></details>'
    : "";
  return `<div class="fail"><b>${esc(h.text)}</b><div class="fail-actions">${retry}</div>${detail}</div>`;
}

/**
 * «Выстрелил и забыл» рядом с надписью «читаю…» — всегда баг: промис отвергся,
 * ловца нет, спиннер стоит вечно. Эта обёртка рисует отказ на месте спиннера.
 * Отказ пишется только если узел ещё на экране (иначе владелец давно ушёл).
 */
export function safeRender(box: HTMLElement, fn: () => Promise<void>): void {
  void fn().catch((e) => {
    if (!box.isConnected) return;
    box.innerHTML = failHTML(e);
    bindFail(box, () => safeRender(box, fn));
  });
}

/** Оживляет кнопки блока отказа: «Повторить» и «Скопировать». */
export function bindFail(root: ParentNode, retry?: () => void) {
  for (const b of root.querySelectorAll<HTMLButtonElement>("[data-fail-retry]")) {
    if (retry) b.addEventListener("click", retry);
    else b.hidden = true;
  }
  for (const b of root.querySelectorAll<HTMLButtonElement>("[data-fail-copy]")) {
    b.addEventListener("click", () => {
      const pre = b.parentElement?.querySelector("pre");
      const text = pre?.textContent || "";
      navigator.clipboard?.writeText(text).then(
        () => toast("Скопировано"),
        () => toast("Скопировать не вышло — выдели текст мышью"),
      );
    });
  }
}
