// Шаги одного прогона агента — одна разметка для окна (панель хода, урок на
// экране «Система») и для телефона. Данные — `GET /api/run/{id}`.
import { esc, fmtK } from "./text";

export interface RunDetail {
  manifest?: {
    status?: string;
    created_at?: string;
    goal?: string;
    terminal?: { status?: string; reason?: string };
    context?: { chat_id?: string | number; delivery_chat_id?: string | number; origin_chat_id?: string | number; kind?: string };
  };
  iterations?: Array<{
    at?: string;
    model?: string;
    ms?: number;
    text?: string;
    usage?: { in?: number; cache_read?: number; out?: number };
    tools?: Array<{ tool?: string; args?: unknown; result?: { head?: string; tail?: string } }>;
  }>;
}

export interface StepsOptions {
  /** Показать только последние N шагов (живой ход). */
  limit?: number;
  /** Пояснения под шагами (урок на экране «Система»): ключи think_first, think, hand, reply, end_turn, terminal. */
  lesson?: Record<string, string>;
}

/** Шаги прогона: повороты мысли, руки с расписками, слово, итог. */
export function stepsHTML(d: RunDetail, opts: StepsOptions = {}): string {
  const steps: string[] = [];
  const L = opts.lesson;
  const lesson = (key: string) => (L && L[key] ? `<div class="lesson">${esc(L[key])}</div>` : "");
  let thought = 0;
  for (const it of d.iterations || []) {
    const u = it.usage || {};
    thought += 1;
    const cached = u.cache_read || 0;
    const total = (u.in || 0) + cached;
    const share = total ? Math.round((100 * cached) / total) : 0;
    steps.push(
      `<div class="ev-step think"><span><span class="kind">думает</span>${esc(it.model || "")}` +
        `${it.ms != null ? ` · ${(it.ms / 1000).toFixed(1)} с` : ""}` +
        `${u.in != null ? ` · вход ${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ${fmtK(u.out || 0)}` : ""}</span>` +
        lesson(thought === 1 ? "think_first" : "think") +
        `</div>`,
    );
    for (const t of it.tools || []) {
      const args = t.args ? JSON.stringify(t.args) : "";
      const head = (t.result && (t.result.head || t.result.tail)) || "";
      const key = t.tool === "reply" ? "reply" : t.tool === "end_turn" ? "end_turn" : "hand";
      steps.push(
        `<div class="ev-step"><span><span class="kind">рука</span><b class="tool">${esc(t.tool || "?")}</b>` +
          `${args ? ` <span class="args">${esc(args.slice(0, 90))}${args.length > 90 ? "…" : ""}</span>` : ""}</span>` +
          `${head ? `<div class="res">→ ${esc(String(head).slice(0, 140))}</div>` : ""}` +
          lesson(key) +
          `</div>`,
      );
    }
    if (it.text) {
      steps.push(`<div class="ev-step word"><span><span class="kind">слово</span>${esc(it.text.slice(0, 160))}${it.text.length > 160 ? "…" : ""}</span></div>`);
    }
  }
  const term = d.manifest?.terminal || {};
  if (term.status) {
    steps.push(
      `<div class="ev-step"><span><span class="kind">итог</span><b>${esc(term.status)}</b>${term.reason ? ` <span class="res">· ${esc(String(term.reason).slice(0, 100))}</span>` : ""}</span>${lesson("terminal")}</div>`,
    );
  }
  if (opts.limit && steps.length > opts.limit) {
    const hidden = steps.length - opts.limit;
    return `<div class="ev-step think"><span class="muted">…ещё ${hidden} шагов выше</span></div>` + steps.slice(-opts.limit).join("");
  }
  return steps.join("") || '<div class="muted">шагов ещё нет</div>';
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
