// Панель хода справа от переписки: идущий ход живьём, полоса кадра (сколько
// модель получила из кэша, сколько заново), прошлые ходы шагами. Это и есть
// «погружение в то, как строятся агенты»: расписки рук, повороты мысли и
// цена каждого — рядом с разговором, а не в отдельной таблице.
import { api } from "./api";
import { bindFail, esc, failHTML, fmtDur, fmtTime, humanError, q } from "./lib";
import { frameStripHTML, stepsHTML, type RunDetail } from "../../ui-kit/steps";
export { stepsHTML, type RunDetail } from "../../ui-kit/steps";
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
      .replace(/[*_`#>]+/g, "")
      .replace(/\s+/g, " ")
      .trim();
  const out = clean(t.out || "");
  const inn = clean(t.in || "");
  const text = out || inn;
  if (text) return text.slice(0, 72);
  return `${EV_KIND[run.kind || t.kind || ""] || "сообщение"} · ${fmtTime(run.created_at)}`;
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

function frameStrip(d: RunDetail | undefined, live: boolean): string {
  return frameStripHTML(d, live ? "Кадр сейчас" : "Кадр последнего хода", '<a href="#" data-go="frame">зоны K·E·A·T →</a>');
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
    frameStrip(stripDetail, !!liveId) +
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
