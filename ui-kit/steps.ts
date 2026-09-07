// Шаги одного прогона агента — одна разметка для окна (панель хода, урок на
// экране «Система») и для телефона. Данные — `GET /api/run/{id}`.
import { esc, fmtK } from "./text";

export interface RunDetail {
  id?: string;
  origin?: { text: string; source: string };
  outcome?: { text: string; note: string };
  manifest?: {
    status?: string;
    created_at?: string;
    goal?: string;
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
    text?: string;
    usage?: { in?: number; cache_read?: number; out?: number };
    tools?: Array<{
      call_id?: string; seq?: number; at?: string; status?: string; error?: string;
      tool?: string; args?: unknown;
      result?: { head?: string; tail?: string; truncated?: boolean } | null;
    }>;
  }>;
}

export interface StepsOptions {
  /** Показать только последние N шагов (живой ход). */
  limit?: number;
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
};
const TERMINAL: Record<string, string> = {
  done: "Работа завершена", completed: "Работа завершена", failed: "Работа остановилась с ошибкой",
  cancelled: "Работа отменена", canceled: "Работа отменена", paused: "Работа приостановлена",
  in_doubt: "Нужно проверить результат", blocked: "Есть препятствие",
};
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

function details(key: string, parts: Array<[string, string]>): string {
  const body = parts.filter(([, value]) => value).map(([label, value]) =>
    `<div class="action-detail-label">${esc(label)}</div><pre>${esc(value)}</pre>`).join("");
  return body ? `<details class="action-details" data-detail="${esc(key)}"><summary>Подробности</summary>${body}</details>` : "";
}

function readable(text: string, label: string, key: string): string {
  if (!text) return "";
  const body = `<div class="run-reading">${esc(text)}</div>`;
  return `<section class="run-message"><div class="action-detail-label">${label}</div>${text.length > 320 ? `<details data-detail="${key}" class="run-reading-more"><summary>${esc(clip(text, 240))}<span>Читать полностью</span></summary>${body}</details>` : body}</section>`;
}

/** Видимые действия и записанные результаты, без догадок об успехе инструмента. */
export function stepsHTML(d: RunDetail, opts: StepsOptions = {}): string {
  const steps: string[] = [];
  const L = opts.lesson;
  const lesson = (key: string) => (L && L[key] ? `<div class="lesson">${esc(L[key])}</div>` : "");
  let thought = 0;
  const live = d.manifest?.status === "running";
  for (const it of d.iterations || []) {
    const u = it.usage || {};
    thought += 1;
    const cached = u.cache_read || 0;
    const total = (u.in || 0) + cached;
    const share = total ? Math.round((100 * cached) / total) : 0;
    const key = it.call_id || String(it.seq ?? thought);
    const complete = it.status === "completed" || (it.status !== "failed" && it.ms != null);
    const thinking = !complete && it.status !== "failed" && !it.tools?.length && live;
    const label = it.status === "failed" ? "Ошибка ответа модели" : complete ? "Ответ модели получен" : thinking ? "Ожидает ответа модели" : "Ответ модели не записан";
    if (it.call_id || it.status || it.model || it.ms != null) steps.push(`<div class="ev-step action-model ${thinking ? "action-active" : ""}">
      <div class="action-head"><span class="action-title">${label}</span>${thinking ? '<span class="action-status">сейчас</span>' : ""}</div>` +
      details("model:" + key, [["Модель", it.model || ""], ["Время", it.ms != null ? `${(it.ms / 1000).toFixed(1)} с` : ""],
        ["Токены", u.in != null ? `вход ${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ответ ${fmtK(u.out || 0)}` : ""], ["Ошибка", it.error || ""]]) +
      lesson(thought === 1 ? "think_first" : "think") + `</div>`);
    for (const t of it.tools || []) {
      const args = t.args != null ? pretty(t.args) : "";
      const head = String(t.result?.head || t.result?.tail || "");
      const received = t.result != null || t.status === "received";
      const failed = t.status === "failed";
      const active = !received && !failed && live;
      const status = failed ? "ошибка" : received ? "результат получен" : active ? "выполняется" : "результат неизвестен";
      const title = ACTIONS[t.tool || ""] || t.tool || "Действие";
      const what = subject(t.args);
      const result = t.result?.truncated ? [t.result.head, "… пропущена часть результата …", t.result.tail].filter(Boolean).join("\n") : head;
      const lessonKey = t.tool === "reply" ? "reply" : t.tool === "end_turn" ? "end_turn" : "hand";
      steps.push(
        `<div class="ev-step action-tool ${active ? "action-active" : ""} ${failed ? "action-failed" : ""}">` +
          `<div class="action-head"><b class="action-title">${esc(title)}</b><span class="action-status">${status}</span></div>` +
          `${what ? `<div class="action-subject">${esc(clip(what, 240))}</div>` : ""}` +
          `${head ? `<div class="action-result">${esc(clip(resultText(head), 280))}</div>` : ""}` +
          details("tool:" + (t.call_id || String(t.seq ?? key + ":" + steps.length)), [["Инструмент", t.tool || ""], ["Параметры", args], [t.result?.truncated ? "Сохранённый фрагмент результата" : "Результат", result], ["Ошибка", t.error || ""]]) +
          lesson(lessonKey) +
          `</div>`,
      );
    }
    if (it.text) {
      steps.push(`<div class="ev-step word"><div class="action-subject">${esc(clip(it.text, 300))}</div>${details("text:" + key, [["Текст", it.text]])}</div>`);
    }
  }
  const term = d.manifest?.terminal || {};
  if (term.status) {
    steps.push(
      `<div class="ev-step"><b class="action-title">${esc(TERMINAL[term.status] || term.status)}</b>${details("terminal", [["Причина", term.reason || ""]])}${lesson("terminal")}</div>`,
    );
  }
  const origin = readable(d.origin?.text || "", "Повод запуска", "origin") || (d.origin?.source === "unknown" ? '<div class="muted">Повод запуска не записан.</div>' : "");
  const outcome = readable(d.outcome?.text || d.outcome?.note || "", "Итог", "outcome");
  if (opts.limit && steps.length > opts.limit) {
    const hidden = steps.length - opts.limit;
    return origin + `<details class="action-earlier" data-detail="earlier"><summary>Показать предыдущие действия · ${hidden}</summary>${steps.slice(0, -opts.limit).join("")}</details>` + steps.slice(-opts.limit).join("") + outcome;
  }
  return origin + (steps.join("") || `<div class="muted">${live ? "Работа началась. Первые действия ещё не записаны." : "Подробности действий не записаны."}</div>`) + outcome;
}

/** Обновлять содержимое только при изменении, сохраняя раскрытие и фокус. */
const rendered = new WeakMap<HTMLElement, string>();
export function renderSteps(box: HTMLElement, d: RunDetail, opts: StepsOptions = {}): void {
  const html = stepsHTML(d, opts);
  if (rendered.get(box) === html) return;
  const open = new Set([...box.querySelectorAll<HTMLDetailsElement>("details[open][data-detail]")].map((el) => el.dataset.detail));
  const focused = box.contains(document.activeElement) ? document.activeElement?.closest<HTMLElement>("[data-detail]")?.dataset.detail : undefined;
  box.innerHTML = html;
  rendered.set(box, html);
  for (const el of box.querySelectorAll<HTMLDetailsElement>("details[data-detail]")) {
    if (open.has(el.dataset.detail)) el.open = true;
    if (focused === el.dataset.detail) el.querySelector("summary")?.focus({ preventScroll: true });
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
