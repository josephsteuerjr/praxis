// Шаги одного прогона агента — одна разметка для окна (панель хода, урок на
// экране «Система») и для телефона. Данные — `GET /api/run/{id}`.
import { esc, fmtK, md } from "./text";

export interface RunDetail {
  id?: string;
  origin?: { text: string; source: string };
  outcome?: { text: string; note: string };
  manifest?: {
    status?: string;
    created_at?: string;
    goal?: string;
    chat_id?: string | number | null;
    terminal?: { status?: string; reason?: string };
    context?: { chat_id?: string | number; delivery_chat_id?: string | number; origin_chat_id?: string | number; kind?: string };
  };
  iterations?: Array<{
    call_id?: string;
    seq?: number;
    status?: string;
    error?: string;
    at?: string;
    model?: string;
    ms?: number;
    /** stop_reason модели: max_tokens — ответ оборван потолком, а не получен. */
    stop?: string;
    text_chars?: number;
    text?: string;
    /** Длинное слово сохранено файлом результата: дочитать через /api/run/{id}/result/{ref}. */
    text_ref?: string;
    text_truncated?: boolean;
    usage?: { in?: number; cache_read?: number; out?: number };
    tools?: Array<{
      call_id?: string; seq?: number; at?: string; status?: string; error?: string;
      tool?: string; args?: unknown;
      result?: { head?: string; tail?: string; truncated?: boolean; result_id?: string; size?: number | null } | null;
    }>;
  }>;
}

export interface StepsOptions {
  /** Показать только последние N шагов (живой ход). */
  limit?: number;
  /** Обзор: повод и итог рядом, действия раскрываются отдельным блоком. */
  overview?: boolean;
  /** Пояснения под шагами (урок на экране «Система»): ключи think_first, think, hand, reply, end_turn, terminal. */
  lesson?: Record<string, string>;
}

const ACTIONS: Record<string, string> = {
  shell: "Команда", read_file: "Чтение файла", write_file: "Запись файла",
  edit_file: "Правка файла", search: "Поиск", web_search: "Поиск в интернете",
  fetch_url: "Чтение страницы", read_url: "Чтение страницы",
  reply: "Ответ", end_turn: "Завершение работы", remind_self: "Напоминание",
  recall: "Поиск в памяти", remember: "Запись в память", read_memory: "Чтение памяти",
  coding_task: "Задача разработки", coding_agent: "Помощник по коду",
  coding_process: "Процесс", coding_read: "Чтение кода", coding_search: "Поиск в коде",
  coding_patch: "Правка кода", coding_verify: "Проверка", coding_finish: "Завершение задачи",
  write_skill: "Запись навыка", consolidate_context: "Свёртка контекста",
  "telegram.deliver": "Доставка сообщения",
};
const TERMINAL: Record<string, string> = {
  done: "Работа завершена", completed: "Работа завершена", failed: "Работа остановилась с ошибкой",
  cancelled: "Работа отменена", canceled: "Работа отменена", paused: "Работа приостановлена",
  in_doubt: "Нужно проверить результат", blocked: "Есть препятствие",
};
/** Причина завершения человеческими словами. `done` бывает разным: оборвалась потолком —
 *  не то же, что «решила промолчать». Ход без руки reply НЕ помечается «без ответа»:
 *  в Hélène слово, написанное текстом, доставляет граница окна, а прогон ядра об этом
 *  не знает — такой ход остаётся просто «Работа завершена». */
function terminalLabel(status: string, reason: string): { label: string; failed: boolean } {
  const r = (reason || "").toLowerCase();
  if (status === "done" || status === "completed") {
    if (r.includes("max_tokens")) return { label: "Ответ оборван потолком, работа не доведена", failed: true };
    if (r === "silent decision") return { label: "Завершено: решила промолчать", failed: false };
  }
  return { label: TERMINAL[status] || status, failed: status === "failed" };
}
const clip = (s: string, n: number) => s.length > n ? s.slice(0, n) + "…" : s;
const pretty = (v: unknown) => typeof v === "string" ? v : JSON.stringify(v, null, 2) || "";

function resultText(text: string): string {
  try {
    const data = JSON.parse(text);
    return pretty(data);
  } catch {
    return text;
  }
}

function subject(args: unknown): string {
  if (!args || typeof args !== "object") return typeof args === "string" ? args : "";
  const a = args as Record<string, unknown>;
  for (const key of ["command", "cmd", "path", "file_path", "query", "url", "text", "goal", "note", "task", "action"]) {
    if (typeof a[key] === "string" && a[key]) return a[key] as string;
  }
  return "";
}

function details(key: string, parts: Array<[string, string]>, label = "Подробности"): string {
  const body = parts.filter(([, value]) => value).map(([name, value]) =>
    `<div class="action-detail-label">${esc(name)}</div><pre>${esc(value)}</pre>`).join("");
  return body ? `<details class="action-details" data-detail="${esc(key)}"><summary>${esc(label)}</summary>${body}</details>` : "";
}

/** Повод человеческими словами: служебные префиксы продукта («Hélène: Сработал твой будильник.
 *  Намечено было вот что:», «[обещание] напоминание себе:») уходят в подпись, содержание остаётся. */
function humanOrigin(text: string, label: string): { text: string; label: string; kind: string } {
  let t = text.replace(/^(?:Hélène|Praxis|Праксис):\s*/u, "");
  let kind = "";
  const alarm = t.match(/^Сработал твой будильник[.:]\s*(?:Намечено было вот что:\s*|намечено\s+)?/iu);
  if (alarm) { t = t.slice(alarm[0].length); label = "Будильник"; kind = "alarm"; }
  const promise = t.match(/^\[обещание\]\s*напоминание себе:\s*/iu);
  if (promise) { t = t.slice(promise[0].length); label = "Напоминание себе"; kind = "promise"; }
  return { text: t.trim() || text, label, kind };
}

/** Ссылки под поводом: место в чате и, для напоминаний, просьба агенту словами (в композер, не отправка). */
function originLinks(d: RunDetail | undefined, kind: string, goal: string): string {
  if (!d) return "";
  const room = String(d.manifest?.chat_id ?? "").replace(/^pult$/, "window");
  const at = d.manifest?.created_at || "";
  const links: string[] = [];
  if (room) links.push(`<a href="#" data-open-room="${esc(room)}" data-at="${esc(at)}">Открыть в чате</a>`);
  if (room && (kind === "alarm" || kind === "promise")) {
    const g = clip(goal.replace(/\s+/g, " ").trim(), 200);
    links.push(`<a href="#" data-compose="${esc(room)}" data-text="${esc(`Повтори это напоминание через 10 минут: «${g}»`)}">Повторить через 10 минут</a>`);
    links.push(`<a href="#" data-compose="${esc(room)}" data-text="${esc(`Сними напоминание: «${g}»`)}">Снять</a>`);
  }
  return links.length ? `<div class="run-links">${links.join(" · ")}</div>` : "";
}

function readable(text: string, label: string, key: string, d?: RunDetail): string {
  if (!text) return "";
  let kind = "";
  if (key === "origin") ({ text, label, kind } = humanOrigin(text, label));
  const body = `<div class="run-reading md">${md(text)}</div>`;
  const links = key === "origin" ? originLinks(d, kind, text) : "";
  return `<section class="run-message"><div class="action-detail-label">${label}</div>${text.length > 320 ? `<details data-detail="${key}" class="run-reading-more"><summary><div class="run-reading-preview">${md(clip(text, 240))}</div><span>Читать полностью</span></summary>${body}</details>` : body}${links}</section>`;
}

// ---------------------------------------------------------------- длинные тексты
// Результат руки или её слово показываются целиком, а не обрубком в 280 знаков: длинный
// текст складывается до 12 строк с кнопкой «Показать целиком» (без прокрутки внутри
// коробки), а если сервер сохранил только голову и хвост (inline 2000 знаков), кнопка
// дочитывает файл результата через /api/run/{run}/result/{id}. Слово владельца 08.09:
// «не разворачивается текстовое окошко с ответом или выводом, если они длинные очень».
export type ResultFetcher = (runId: string, resultId: string) => Promise<{ text?: string; model_text?: string; complete?: boolean } | null>;
let fetchResult: ResultFetcher | null = null;
const fetched = new Map<string, string>();
const CLIP_LINES = 12;
const CLIP_CHARS = 900;

export function setResultFetcher(fn: ResultFetcher): void {
  fetchResult = fn;
}

const lineCount = (s: string) => s.split("\n").length;
const needsClip = (text: string) => text.length > CLIP_CHARS || lineCount(text) > CLIP_LINES;

/** Коробка текста: ключ сохраняет раскрытие при перерисовке; data-full — что дочитать. */
function textBox(key: string, text: string, opts: { truncated?: boolean; ref?: string; run?: string; size?: number | null } = {}): string {
  const full = opts.ref && opts.run ? fetched.get(`${opts.run}/${opts.ref}`) : undefined;
  const body = full ?? text;
  const truncated = !!opts.truncated && full === undefined;
  const folded = needsClip(body) || truncated;
  const more = truncated
    ? `Показать целиком${opts.size ? ` · ${fmtK(opts.size)} байт` : ""}`
    : `Показать целиком · ${lineCount(body)} строк`;
  const fullAttr = truncated && opts.ref && opts.run ? ` data-full="${esc(opts.run)}/${esc(opts.ref)}"` : "";
  return `<div class="run-text" data-text="${esc(key)}">` +
    `<pre class="run-text-body ${folded ? "clipped" : ""}">${esc(body)}</pre>` +
    (folded ? `<button type="button" class="run-text-more" data-more="${esc(key)}"${fullAttr}>${more}</button>` : "") +
    `</div>`;
}

function textOf(payload: { text?: string; model_text?: string } | null): string {
  if (!payload) return "";
  if (payload.model_text) return payload.model_text;
  const raw = payload.text || "";
  try {
    return pretty(JSON.parse(raw));
  } catch {
    return raw;
  }
}

let bound = false;
function bindMore(): void {
  if (bound || typeof document === "undefined") return;
  bound = true;
  document.addEventListener("click", (e) => {
    const target = e.target as HTMLElement | null;
    // Ссылки с карточки: хозяин ленты (окно, телефон) решает, как открыть чат и композер.
    const open = target?.closest<HTMLAnchorElement>("a[data-open-room]");
    if (open) {
      e.preventDefault();
      window.dispatchEvent(new CustomEvent("steps-open", { detail: { room: open.dataset.openRoom || "", at: open.dataset.at || "" } }));
      return;
    }
    const compose = target?.closest<HTMLAnchorElement>("a[data-compose]");
    if (compose) {
      e.preventDefault();
      window.dispatchEvent(new CustomEvent("steps-compose", { detail: { room: compose.dataset.compose || "", text: compose.dataset.text || "" } }));
      return;
    }
    const btn = target?.closest<HTMLButtonElement>("button[data-more]");
    if (!btn) return;
    e.preventDefault();
    const box = btn.closest<HTMLElement>(".run-text");
    const pre = box?.querySelector<HTMLElement>(".run-text-body");
    if (!box || !pre) return;
    const full = btn.dataset.full;
    if (full && !fetched.has(full) && fetchResult) {
      const [run, rid] = full.split("/");
      btn.disabled = true;
      btn.textContent = "читаю…";
      fetchResult(run, rid).then((payload) => {
        const text = textOf(payload);
        if (text) {
          fetched.set(full, text);
          pre.textContent = text;
        }
        pre.classList.remove("clipped");
        btn.disabled = false;
        btn.textContent = text ? "Свернуть" : "Файл результата не прочитался";
      }, () => {
        btn.disabled = false;
        btn.textContent = "Не прочиталось, попробовать ещё";
      });
      return;
    }
    const closed = pre.classList.toggle("clipped");
    btn.textContent = closed ? `Показать целиком · ${lineCount(pre.textContent || "")} строк` : "Свернуть";
  });
}
bindMore();

// ---------------------------------------------------------------- лента шагов

/** Шаги хода: один блок на итерацию модели — номер, время, токены, затем руки и слово. */
export function stepsHTML(d: RunDetail, opts: StepsOptions = {}): string {
  const steps: string[] = [];
  const L = opts.lesson;
  const lesson = (key: string) => (L && L[key] ? `<div class="lesson">${esc(L[key])}</div>` : "");
  const live = d.manifest?.status === "running";
  const runId = d.id || "";
  let hands = 0;
  (d.iterations || []).forEach((it, i) => {
    const n = i + 1;
    const u = it.usage || {};
    const cached = u.cache_read || 0;
    const total = (u.in || 0) + cached;
    const share = total ? Math.round((100 * cached) / total) : 0;
    const key = it.call_id || String(it.seq ?? n);
    const complete = it.status === "completed" || (it.status !== "failed" && it.ms != null);
    const thinking = !complete && it.status !== "failed" && !it.tools?.length && live;
    const cut = complete && it.stop === "max_tokens";
    const status = it.status === "failed" ? "ошибка модели" : cut ? (it.text_chars ? "ответ оборван потолком, фраза не закончена" : "ответ оборван потолком, ни слова не дошло") : thinking ? "думает" : "";
    const seconds = it.ms != null ? `${(it.ms / 1000).toFixed(1)} с` : "";
    const tokens = u.in != null ? `${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ${fmtK(u.out || 0)}` : "";
    const meta = [seconds, tokens].filter(Boolean).join(" · ");
    const parts: string[] = [];
    parts.push(`<div class="step-head"><span class="step-n">Шаг ${n}</span>${meta ? `<span class="step-meta">${esc(meta)}</span>` : ""}${status ? `<span class="action-status">${esc(status)}</span>` : ""}</div>`);
    parts.push(details("model:" + key, [["Модель", it.model || ""], ["Время", seconds],
      ["Токены", u.in != null ? `вход ${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ответ ${fmtK(u.out || 0)}` : ""],
      ["Остановка", cut ? "max_tokens: потолок ответа исчерпан размышлением или длинным ответом" : ""], ["Ошибка", it.error || ""]], "Подробности шага"));
    parts.push(lesson(n === 1 ? "think_first" : "think"));
    for (const t of it.tools || []) {
      hands += 1;
      const args = t.args != null ? pretty(t.args) : "";
      const head = String(t.result?.head || t.result?.tail || "");
      const received = t.result != null || t.status === "received";
      const failed = t.status === "failed";
      const active = !received && !failed && live;
      // Шаг доставки ядра с нулём знаков — не её действие и не «получила ноль»: на границе
      // прогона доставлять было нечего (слово ушло рукой reply или границей окна).
      const emptyDelivery = t.tool === "telegram.deliver" && (t.args as { text_chars?: number } | null)?.text_chars === 0 && !(t.args as { media_count?: number } | null)?.media_count;
      const tstatus = failed ? "ошибка" : emptyDelivery ? "нечего доставлять" : received ? "результат получен" : active ? "выполняется" : "результат неизвестен";
      const title = emptyDelivery ? "Доставка ядра" : ACTIONS[t.tool || ""] || t.tool || "Действие";
      const what = subject(t.args);
      const result = t.result?.truncated ? [t.result.head, "… пропущена часть результата …", t.result.tail].filter(Boolean).join("\n") : head;
      // Квитанции содержат инструкции раннеру; сохраняем их в подробностях.
      const receipt = ["reply", "end_turn", "telegram.deliver"].includes(t.tool || "");
      const lessonKey = t.tool === "reply" ? "reply" : t.tool === "end_turn" ? "end_turn" : "hand";
      const tkey = "tool:" + (t.call_id || String(t.seq ?? key + ":" + hands));
      parts.push(
        `<div class="hand ${active ? "action-active" : ""} ${failed ? "action-failed" : ""}">` +
          `<div class="action-head"><b class="action-title">${esc(title)}</b><span class="action-status">${tstatus}</span></div>` +
          `${what ? `<div class="action-subject">${esc(clip(what, 400))}</div>` : ""}` +
          `${head && !receipt ? textBox(tkey + ":result", resultText(result), { truncated: t.result?.truncated, ref: t.result?.result_id, run: runId, size: t.result?.size }) : ""}` +
          details(tkey, [["Инструмент", t.tool || ""], ["Параметры", args], [receipt ? (t.result?.truncated ? "Сохранённый фрагмент квитанции" : "Квитанция") : "", receipt ? result : ""], ["Ошибка", t.error || ""]]) +
          lesson(lessonKey) +
          `</div>`,
      );
    }
    if (it.text) {
      parts.push(`<div class="hand word"><div class="action-head"><b class="action-title">Слово</b></div>${textBox("text:" + key, it.text, { truncated: !!it.text_truncated, ref: it.text_ref, run: runId })}</div>`);
    } else if (it.text_ref) {
      parts.push(`<div class="hand word"><div class="action-head"><b class="action-title">Слово</b><span class="action-status">длинный ответ сохранён файлом</span></div>${textBox("text:" + key, "", { truncated: true, ref: it.text_ref, run: runId })}</div>`);
    }
    steps.push(`<div class="ev-step step ${thinking ? "action-active" : ""} ${cut || it.status === "failed" ? "action-failed" : ""}">${parts.join("")}</div>`);
  });
  const term = d.manifest?.terminal || {};
  const tail: string[] = [];
  if (term.status) {
    const t = terminalLabel(term.status, term.reason || "");
    tail.push(
      `<div class="ev-step ${t.failed ? "action-failed" : ""}"><b class="action-title">${esc(t.label)}</b>${details("terminal", [["Причина", term.reason || ""]])}${lesson("terminal")}</div>`,
    );
  }
  const origin = readable(d.origin?.text || "", "Повод запуска", "origin", d) || (d.origin?.source === "unknown" ? '<div class="muted">Повод запуска не записан.</div>' : "");
  const outcome = readable(d.outcome?.text || d.outcome?.note || "", "Итог", "outcome");
  let actions = steps.join("") || `<div class="muted">${live ? "Работа началась. Первые шаги ещё не записаны." : "Шаги не записаны."}</div>`;
  if (opts.limit && steps.length > opts.limit) {
    // Счёт один и тот же везде: шаги = итерации модели, не сумма рук и не строки ленты.
    const hidden = steps.length - opts.limit;
    actions = `<details class="action-earlier" data-detail="earlier"><summary>Показать ранние шаги · ${hidden} из ${steps.length}</summary>${steps.slice(0, -opts.limit).join("")}</details>` + steps.slice(-opts.limit).join("");
  }
  actions += tail.join("");
  if (opts.overview) {
    const count = steps.length ? ` · ${steps.length}${hands ? `, рук ${hands}` : ""}` : "";
    return origin + outcome + `<details class="run-actions" data-detail="actions" ${live ? "open" : ""}><summary>Шаги${count}</summary>${actions}</details>`;
  }
  return origin + actions + outcome;
}

/** Обновлять содержимое только при изменении, сохраняя раскрытие, развёрнутые тексты и фокус. */
const rendered = new WeakMap<HTMLElement, string>();
export function renderSteps(box: HTMLElement, d: RunDetail, opts: StepsOptions = {}): void {
  const html = stepsHTML(d, opts);
  if (rendered.get(box) === html) return;
  const expanded = new Map([...box.querySelectorAll<HTMLDetailsElement>("details[data-detail]")].map((el) => [el.dataset.detail, el.open]));
  const unclipped = new Set([...box.querySelectorAll<HTMLElement>(".run-text")].filter((el) => !el.querySelector(".run-text-body.clipped")).map((el) => el.dataset.text));
  const focused = box.contains(document.activeElement) ? document.activeElement?.closest<HTMLElement>("[data-detail]")?.dataset.detail : undefined;
  box.innerHTML = html;
  rendered.set(box, html);
  for (const el of box.querySelectorAll<HTMLDetailsElement>("details[data-detail]")) {
    if (expanded.has(el.dataset.detail)) el.open = expanded.get(el.dataset.detail)!;
    if (focused === el.dataset.detail) el.querySelector("summary")?.focus({ preventScroll: true });
  }
  for (const el of box.querySelectorAll<HTMLElement>(".run-text")) {
    if (!unclipped.has(el.dataset.text)) continue;
    el.querySelector(".run-text-body")?.classList.remove("clipped");
    const btn = el.querySelector<HTMLButtonElement>(".run-text-more");
    if (btn && !btn.dataset.full) btn.textContent = "Свернуть";
  }
}

/** Последнее использование модели в прогоне: цена кадра. */
export function lastUsage(d: RunDetail | undefined): { in: number; cached: number; out: number; model: string } | null {
  const its = d?.iterations || [];
  for (let i = its.length - 1; i >= 0; i--) {
    const u = its[i].usage;
    if (u && (u.in != null || u.cache_read != null)) {
      return { in: u.in || 0, cached: u.cache_read || 0, out: u.out || 0, model: its[i].model || "" };
    }
  }
  return null;
}

/** Полоса кадра: из кэша / заново / ответ. */
export function frameStripHTML(d: RunDetail | undefined, title: string, link = ""): string {
  const u = lastUsage(d);
  if (!u) return "";
  const total = u.in + u.cached;
  const share = total ? Math.round((100 * u.cached) / total) : 0;
  const all = total + u.out || 1;
  const w = (n: number) => `${Math.max(0, (100 * n) / all).toFixed(2)}%`;
  return `<div class="frame-strip">
    <div class="frame-strip-head"><span>${esc(title)}</span>${link}</div>
    <div class="frame-bar"><i class="cached" style="width:${w(u.cached)}"></i><i class="fresh" style="width:${w(u.in)}"></i><i class="out" style="width:${w(u.out)}"></i></div>
    <div class="frame-legend">
      <span class="cached"><i></i>из кэша ${fmtK(u.cached)} (${share}%)</span>
      <span class="fresh"><i></i>заново ${fmtK(u.in)}</span>
      <span class="out"><i></i>ответ ${fmtK(u.out)}</span>
    </div>
  </div>`;
}
