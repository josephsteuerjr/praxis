// «Сейчас» — главный экран (слово владельца 07.09: «я должен видеть сразу
// текущий ход»): идущий ход во всю ширину, шаги живьём, кадр, недавние ходы
// по всем комнатам с раскрывающимися шагами. Писать здесь нечего — это монитор.
import { esc, fmtDur, fmtTime } from "../lib";
import { api } from "../api";
import { mountUsage, usageShell } from "../../../ui-kit/usage";
import { frameStripHTML, renderSteps, stepsHTML, type RunDetail } from "../../../ui-kit/steps";
import { S, foreignHarness, runIsRecent, type Run } from "../state";
import { bindRuns, cleanLabel, loadWords, roomKeyOf, runDetail, runRowHTML } from "../runlist";

let root: HTMLElement | null = null;
let liveTimer = 0;
let liveBusy = false;
let gen = 0;

/** Идущий ход по любой комнате: сердцебиение руннера, а у чужого харнесса — свежий running-манифест. */
export function liveRun(): Run | undefined {
  if (foreignHarness()) return S.runs.find((r) => r.status === "running" && runIsRecent(r));
  const r = S.agentState?.runner;
  if (!r || !r.alive || !r.busy || !r.run) return undefined;
  return S.runs.find((x) => x.id === r.run) || { id: r.run, kind: "chat_turn", status: "running" };
}

function since(run: Run): string {
  const r = S.agentState?.runner;
  if (r?.since && r.busy && r.run === run.id) return fmtDur(Date.now() / 1000 - r.since);
  const at = new Date(run.created_at ?? "").getTime();
  return isNaN(at) ? "" : fmtDur((Date.now() - at) / 1000);
}

function idleHTML(): string {
  const s = S.agentState;
  if (!s) return `<div class="now-idle"><span class="dot"></span><span>Подключение…</span></div>`;
  const at = s.brain?.last_call_at;
  const age = at ? Math.round((Date.now() / 1000 - at) / 60) : null;
  const when = age == null ? "" : age < 1 ? "модель отвечала только что" : age < 60 ? `модель отвечала ${age} мин назад` : `модель отвечала ${Math.round(age / 60)} ч назад`;
  const phrase = foreignHarness() ? `Нет текущих действий${when ? " · " + when : ""}` : `${s.phrase}${when ? " · " + when : ""}`;
  const wake = s.next_wake ? ` · следующее пробуждение ${fmtTime(s.next_wake) || s.next_wake}` : "";
  return `<div class="now-idle"><span class="dot ${!foreignHarness() && s.level === "error" ? "failed" : "ok"}"></span><span>${esc(phrase)}${esc(wake)}</span></div>`;
}

export async function render(container: HTMLElement): Promise<void> {
  root = container;
  const my = ++gen;
  const live = liveRun();
  let liveDetail: RunDetail | undefined;
  if (live) {
    try {
      liveDetail = await runDetail(live.id, true);
    } catch {
      liveDetail = undefined;
    }
  }
  if (my !== gen) return;
  const list = S.runs.filter((r) => r.kind !== "wake").slice(0, 40);
  await loadWords(list.slice(0, 15));
  if (my !== gen) return;
  let strip = liveDetail;
  if (!strip) {
    const last = list.find((r) => r.id !== live?.id && r.kind === "chat_turn" && r.status !== "running");
    if (last) {
      try {
        strip = await runDetail(last.id);
      } catch {
        strip = undefined;
      }
      if (my !== gen) return;
    }
  }
  const liveHTML = live
    ? `<div class="turn-live now-live" id="turn-live" data-run="${esc(live.id)}">
        <div class="turn-live-head"><span class="dot live"></span><span>Действия сейчас</span><span class="t" id="turn-live-t">${esc(since(live))}</span></div>
        ${live.chat_title ? `<div class="turn-live-sub"><a href="#" data-room="${esc(roomKeyOf(live))}" data-room-name="${esc(live.chat_title)}">${esc(live.chat_title)}</a>${live.goal_head ? " · " + esc(cleanLabel(live.goal_head).slice(0, 90)) : ""}</div>` : ""}
        <div class="ev-steps" id="turn-live-steps">${liveDetail ? stepsHTML(liveDetail, { limit: 16 }) : '<div class="muted">читаю шаги…</div>'}</div>
      </div>`
    : idleHTML();
  const rest = list.filter((r) => r.id !== live?.id);
  container.innerHTML = `<div class="center now">
    ${liveHTML}
    ${usageShell(true)}
    ${frameStripHTML(strip, live ? "Контекст сейчас" : "Контекст последнего ответа", '<a href="#" data-go="frame">Посмотреть кадр →</a>')}
    <h3 class="section-title">Последние действия <span class="muted">${rest.length}</span></h3>
    ${rest.map((r) => runRowHTML(r, { showRoom: true })).join("") || '<div class="empty">Здесь появятся действия и результаты</div>'}
  </div>`;
  for (const a of container.querySelectorAll<HTMLElement>("[data-go]")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      dispatchEvent(new CustomEvent("frame-go", { detail: a.dataset.go }));
    });
  }
  bindRuns(container, () => void render(container), (key, name) => dispatchEvent(new CustomEvent("frame-open-room", { detail: { key, name } })));
  mountUsage(container, api);
  scheduleLive(!!live);
}

function scheduleLive(on: boolean) {
  clearTimeout(liveTimer);
  if (!on) return;
  liveTimer = window.setTimeout(() => void refreshLive(), 1500);
}

async function refreshLive() {
  if (S.view !== "now" || !root) return;
  if (liveBusy) { scheduleLive(true); return; }
  const live = liveRun();
  const card = root.querySelector<HTMLElement>("#turn-live");
  if (!live || !card || card.dataset.run !== live.id) {
    if (card || live) void render(root);
    return;
  }
  liveBusy = true;
  try {
    const d = await runDetail(live.id, true);
    if (!card.isConnected || liveRun()?.id !== live.id) return;
    const steps = card.querySelector<HTMLElement>("#turn-live-steps");
    const t = card.querySelector<HTMLElement>("#turn-live-t");
    if (t) t.textContent = since(live);
    if (steps && d) renderSteps(steps, d, { limit: 16 });
  } catch {
    // следующий такт перечитает
  } finally {
    liveBusy = false;
  }
  scheduleLive(true);
}

/** Событие прогона или вызова модели — обновить экран, если он открыт. */
export function onEvent(kind: string) {
  if (S.view !== "now" || !root) return;
  const card = root.querySelector<HTMLElement>("#turn-live");
  if (kind === "run" && (!card || card.dataset.run !== liveRun()?.id)) void render(root);
  else if (kind === "llm" || kind === "run") {
    clearTimeout(liveTimer);
    liveTimer = window.setTimeout(() => void refreshLive(), 300);
  }
}

/** Состояние обновилось: ход начался или кончился. */
export function tick() {
  if (S.view !== "now" || !root) return;
  const id = root.querySelector<HTMLElement>("#turn-live")?.dataset.run;
  if (liveRun()?.id !== id) void render(root);
  else if (!id) {
    const idle = root.querySelector<HTMLElement>(".now-idle");
    if (idle) idle.outerHTML = idleHTML();
  }
}
