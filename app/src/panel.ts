// Панель хода справа от переписки: идущий ход живьём, полоса кадра (сколько
// модель получила из кэша, сколько заново), прошлые ходы шагами. Это и есть
// «погружение в то, как строятся агенты»: расписки рук, повороты мысли и
// цена каждого — рядом с разговором, а не в отдельной таблице.
import { api } from "./api";
import { bindFail, esc, failHTML, fmtDur, fmtK, fmtTime, humanError, q } from "./lib";
import { S, foreignHarness, isWindowRoom, runIsLive, runIsRecent, type Run } from "./state";

interface Turn {
  run_id: string;
  kind?: string;
  who?: string;
  in?: string;
  out?: string;
  ts?: string | number;
  delivery?: string;
  held?: string;
  note?: string;
}

/**
 * Подпись хода: слово агента, а без него — то, на что он отвечал. Служебные
 * скобки кадра (`[thread #898 «Курилка»; message #34191; …]`) и подпись
 * «Имя: » в начале — не подпись, а адрес; их снимаем.
 */
function turnLabel(t: Turn, run: Run): string {
  const clean = (s: string) =>
    s
      .replace(/^\s*\[[^\]]{0,200}\]\s*/, "")
      .replace(/^[^:\n]{1,40}:\s*/, "")
      .replace(/\s+/g, " ")
      .trim();
  const out = clean(t.out || "");
  const inn = clean(t.in || "");
  const text = out || inn;
  if (text) return text.slice(0, 72);
  return `${EV_KIND[run.kind || t.kind || ""] || "сообщение"} · ${fmtTime(run.created_at)}`;
}

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

const EV_KIND: Record<string, string> = {
  chat_turn: "сообщение",
  wake: "пробуждение",
  task_window: "окно",
  moderation: "модерация",
};

let liveTimer = 0;
let liveBusy = false;

function panelBox(): HTMLElement {
  return q<HTMLElement>("#panel");
}

/** Шаги одного прогона — единая разметка для живого хода, прошлых и урока. */
export function stepsHTML(d: RunDetail, opts: { limit?: number; lesson?: Record<string, string> } = {}): string {
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
function lastUsage(d: RunDetail | undefined): { in: number; cached: number; out: number; model: string } | null {
  const its = d?.iterations || [];
  for (let i = its.length - 1; i >= 0; i--) {
    const u = its[i].usage;
    if (u && (u.in != null || u.cache_read != null)) {
      return { in: u.in || 0, cached: u.cache_read || 0, out: u.out || 0, model: its[i].model || "" };
    }
  }
  return null;
}

function frameStripHTML(d: RunDetail | undefined, live: boolean): string {
  const u = lastUsage(d);
  if (!u) return "";
  const total = u.in + u.cached;
  const share = total ? Math.round((100 * u.cached) / total) : 0;
  const all = total + u.out || 1;
  const w = (n: number) => `${Math.max(0, (100 * n) / all).toFixed(2)}%`;
  return `<div class="frame-strip">
    <div class="frame-strip-head"><span>Кадр${live ? " сейчас" : " последнего хода"}</span><a href="#" data-go="frame">зоны K·E·A·T →</a></div>
    <div class="frame-bar"><i class="cached" style="width:${w(u.cached)}"></i><i class="fresh" style="width:${w(u.in)}"></i><i class="out" style="width:${w(u.out)}"></i></div>
    <div class="frame-legend">
      <span class="cached"><i></i>из кэша ${fmtK(u.cached)} (${share}%)</span>
      <span class="fresh"><i></i>заново ${fmtK(u.in)}</span>
      <span class="out"><i></i>ответ ${fmtK(u.out)}</span>
    </div>
  </div>`;
}

async function runDetail(runId: string, fresh = false): Promise<RunDetail | undefined> {
  if (!fresh) {
    const cached = S.evCache.get(runId) as RunDetail | undefined;
    if (cached) return cached;
  }
  try {
    const d = await api<RunDetail>("/api/run/" + encodeURIComponent(runId));
    S.evCache.set(runId, d);
    return d;
  } catch {
    return undefined;
  }
}

/** Идёт ли сейчас ход в текущей комнате (по состоянию руннера и манифесту). */
function liveRunId(): string {
  if (foreignHarness()) {
    // Чужой харнесс: сердцебиения нет — свежий прогон в статусе running этой
    // комнаты и есть идущий ход.
    const run = S.runs.find((x) => {
      if (x.status !== "running" || !runIsRecent(x)) return false;
      let key = String(x.chat_id ?? "");
      if (key === "pult") key = "window";
      return key === S.room;
    });
    return run ? run.id : "";
  }
  const r = S.agentState?.runner;
  if (!r || !r.alive || !r.busy || !r.run) return "";
  const run = S.runs.find((x) => x.id === r.run);
  if (!run) return r.run; // манифеста в списке ещё нет — покажем как есть
  let key = String(run.chat_id ?? "");
  if (key === "pult") key = "window";
  if (run.chat_id == null) return r.run;
  return key === S.room || (isWindowRoom(key) && isWindowRoom(S.room) && key === S.room) ? r.run : "";
}

/** Сколько идёт ход: сердцебиение руннера, а без него — время создания прогона. */
function liveSince(runId: string): string {
  const r = S.agentState?.runner;
  if (r?.since && r.busy) return fmtDur(Date.now() / 1000 - r.since);
  const run = S.runs.find((x) => x.id === runId);
  const at = new Date(run?.created_at ?? "").getTime();
  return isNaN(at) ? "" : fmtDur((Date.now() - at) / 1000);
}

function liveCardHTML(d: RunDetail | undefined, runId: string): string {
  const since = liveSince(runId);
  const goal = (d?.manifest?.goal || "").split("\n")[0].replace(/^[^:]{1,40}:\s*/, "").slice(0, 90);
  return `<div class="turn-live" id="turn-live">
    <div class="turn-live-head"><span class="dot live"></span><span>Ведёт ход</span><span class="t">${esc(since)}</span></div>
    ${goal ? `<div class="turn-live-sub">${esc(goal)}</div>` : ""}
    <div class="ev-steps" id="turn-live-steps">${d ? stepsHTML(d, { limit: 10 }) : '<div class="muted">читаю шаги…</div>'}</div>
  </div>`;
}

export async function render(): Promise<void> {
  const panel = panelBox();
  let turns: Turn[] = [];
  try {
    turns = await api<Turn[]>("/api/chat-turns/" + encodeURIComponent(S.room));
  } catch (e) {
    panel.innerHTML = `<div class="panel-head"><span>Ход · ${esc(S.roomName)}</span></div>` + failHTML(e);
    bindFail(panel, () => void render());
    return;
  }
  const byRun = new Map(S.runs.map((r) => [r.id, r]));
  const rows = turns.slice().reverse();
  const liveId = liveRunId();
  const liveDetail = liveId ? await runDetail(liveId, true) : undefined;
  // Полоса кадра: живой ход, а без него — последний завершённый в этой комнате.
  let stripDetail = liveDetail;
  if (!stripDetail) {
    const last = rows.find((t) => t.run_id && t.run_id !== liveId);
    if (last) stripDetail = await runDetail(last.run_id);
  }
  const list = rows
    .filter((t) => t.run_id !== liveId)
    .map((t) => {
      const run = byRun.get(t.run_id) ?? ({} as Run);
      const label = turnLabel(t, run);
      const status = run.status || (t.delivery === "failed" ? "failed" : "done");
      const at = run.created_at
        ? fmtTime(run.created_at)
        : t.ts
          ? fmtTime(new Date(parseFloat(String(t.ts)) * 1000).toISOString())
          : "";
      const unspoken = t.held === "unspoken" ? ' <span class="badge quiet">промолчала</span>' : "";
      const who = (t.who || "").trim();
      return `<div class="ev ${S.evOpen.has(t.run_id) ? "open" : ""}" data-ev="${esc(t.run_id)}">
        <div class="ev-head">
          <span class="ev-title" title="${esc(t.in || "")}">${esc(label)}${unspoken}</span>
          <span class="ev-time">${at}</span>
          <span class="dot ${runIsLive(status, run) ? "live" : status === "failed" ? "failed" : ""}"></span>
        </div>
        ${who ? `<div class="ev-sub">${esc(EV_KIND[run.kind || t.kind || ""] || "сообщение")} · ${esc(who.slice(0, 40))}</div>` : ""}
        <div class="ev-steps" ${S.evOpen.has(t.run_id) ? "" : "hidden"}></div>
      </div>`;
    })
    .join("");
  panel.innerHTML =
    `<div class="panel-head"><span>Ход · ${esc(S.roomName)}</span><span class="n">${rows.length ? rows.length : ""}</span></div>` +
    (liveId ? liveCardHTML(liveDetail, liveId) : "") +
    frameStripHTML(stripDetail, !!liveId) +
    (list || (liveId ? "" : '<div class="empty">Ходов ещё нет</div>'));
  for (const a of panel.querySelectorAll<HTMLElement>("[data-go]")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      dispatchEvent(new CustomEvent("frame-go", { detail: a.dataset.go }));
    });
  }
  for (const el of panel.querySelectorAll<HTMLElement>(".ev")) {
    const id = el.dataset.ev!;
    el.querySelector(".ev-head")!.addEventListener("click", () => {
      if (S.evOpen.has(id)) S.evOpen.delete(id);
      else S.evOpen.add(id);
      void render();
    });
    if (S.evOpen.has(id)) void fillSteps(panel, id);
  }
  scheduleLive(!!liveId);
}

async function fillSteps(panel: HTMLElement, runId: string) {
  const box = panel.querySelector<HTMLElement>(`.ev[data-ev="${CSS.escape(runId)}"] .ev-steps`);
  if (!box) return;
  let d = S.evCache.get(runId) as RunDetail | undefined;
  if (!d) {
    box.innerHTML = '<div class="muted">читаю шаги…</div>';
    try {
      d = await api<RunDetail>("/api/run/" + encodeURIComponent(runId));
    } catch (e) {
      if (!box.isConnected) return;
      box.innerHTML = `<div class="muted">шаги не прочитались: ${esc(humanError(e).text)}</div>`;
      return;
    }
    S.evCache.set(runId, d);
  }
  // Пока ждали ответ, панель могла перерисоваться целиком.
  if (!box.isConnected) return;
  box.innerHTML = stepsHTML(d);
}

// ---------------------------------------------------------------- живой ход

/** Пока руннер занят, шаги идущего хода перечитываются раз в полторы секунды. */
function scheduleLive(on: boolean) {
  clearTimeout(liveTimer);
  if (!on) return;
  liveTimer = window.setTimeout(() => void refreshLive(), 1500);
}

async function refreshLive() {
  if (liveBusy || S.view !== "talk") return;
  const id = liveRunId();
  const card = panelBox().querySelector<HTMLElement>("#turn-live");
  if (!id) {
    // Ход закончился — панель перерисуется целиком по событию run; если оно
    // не пришло, перерисуем сами.
    if (card) void render();
    return;
  }
  if (!card) {
    void render();
    return;
  }
  liveBusy = true;
  try {
    const d = await runDetail(id, true);
    const steps = card.querySelector<HTMLElement>("#turn-live-steps");
    const t = card.querySelector<HTMLElement>(".t");
    if (t) t.textContent = liveSince(id);
    if (steps && d) steps.innerHTML = stepsHTML(d, { limit: 10 });
  } finally {
    liveBusy = false;
  }
  scheduleLive(true);
}

/** Состояние обновилось: руннер занят или только что освободился. */
export function tick(busy: boolean) {
  if (S.view !== "talk") return;
  const card = panelBox().querySelector("#turn-live");
  if (busy && !card) void render();
  else if (!busy && card) void render();
}

/** Событие вызова модели — шаги живого хода могли прибавиться. */
export function onLlm() {
  if (S.view !== "talk") return;
  clearTimeout(liveTimer);
  liveTimer = window.setTimeout(() => void refreshLive(), 300);
}

export function onRunEvent(runId: string) {
  S.evCache.delete(runId);
}
