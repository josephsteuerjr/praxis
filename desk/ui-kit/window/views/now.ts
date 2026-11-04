// «Сейчас» — главный экран (слово владельца 07.09: «я должен видеть сразу
// текущий ход»): идущий ход во всю ширину, шаги живьём, кадр, недавние ходы
// по всем комнатам с раскрывающимися шагами. Писать здесь нечего — это монитор.
import { esc, fmtDur, fmtTime } from "../lib";
import { api, cfg } from "../api";
import { mountUsage, usageShell } from "../../usage";
import { frameStripHTML, type RunDetail } from "../../steps";
import { activityHTML, updateActivity } from "../../activity";
import { S, foreignHarness, runIsRecent, type Run } from "../state";
import { bindRuns, loadWords, runDetail, runRowHTML } from "../runlist";
import { forgePanel } from './forge-panel';
import { observeActivity } from '../work-activity';
import './forge-panel.css';
import './now-overview.css';

let root: HTMLElement | null = null;
let liveTimer = 0;
let liveFlight: object | undefined;
let gen = 0;
let focusedRun: Run | undefined;
let showPrevious=false,currentOpen=true,currentId='',scope='';
let agentDrawer: HTMLDetailsElement | undefined, agents: ReturnType<typeof forgePanel> | undefined;
let agentsRunning = false, unobserveAgents: (() => void) | undefined;
let pendingAgentsOpen = false;

function stopAgents() {
  unobserveAgents?.();unobserveAgents=undefined;
  agents?.stop();agentsRunning=false;
}

/** The cached disclosure survives navigation, but its reads/listener do not. */
function syncAgents() {
  if (!root || S.view !== 'now' || !agentDrawer?.isConnected || !agentDrawer.open) {
    stopAgents();return;
  }
  if (agentsRunning) return;
  agentsRunning=true;agents?.start();
  unobserveAgents=observeActivity(()=>cfg.base+'\n'+cfg.key+'\n'+cfg.agent,value=>agents?.setReviewers(value));
}

/** Same captured records as the action panel; no duplicated task or status store. */
function mountAgents(container: HTMLElement) {
  const slot=container.querySelector<HTMLElement>('[data-now-agents]');
  if(!slot)return;
  if(!agentDrawer){
    agentDrawer=document.createElement('details');agentDrawer.className='now-agents';
    const summary=document.createElement('summary');summary.textContent='☰ Субагенты';
    const host=document.createElement('section');agentDrawer.append(summary,host);
    agents=forgePanel(host,undefined,2500,{compact:true,controls:true});
    agentDrawer.addEventListener('toggle',syncAgents);
  }
  slot.append(agentDrawer);
  if (pendingAgentsOpen) { agentDrawer.open=true;pendingAgentsOpen=false; }
  syncAgents();
}

export function dispose() { ++gen;clearTimeout(liveTimer);stopAgents();liveFlight=undefined;pendingAgentsOpen=false;root=null; }

/** Идущий ход по любой комнате: сердцебиение руннера, а у чужого харнесса — свежий running-манифест. */
export function liveRun(): Run | undefined {
  if (foreignHarness()) return S.runs.find((r) => r.status === "running" && runIsRecent(r));
  const r = S.agentState?.runner;
  // Сон — не ход: выдуманного «chat_turn» с id «sleep» здесь быть не должно (ревью 26.09).
  if (!r || !r.alive || !r.busy || !r.run || r.run === "sleep") return undefined;
  return S.runs.find((x) => x.id === r.run) || { id: r.run, kind: "chat_turn", status: "running" };
}

function since(run: Run): string {
  const r = S.agentState?.runner;
  if (r?.since && r.busy && r.run === run.id) return fmtDur(Date.now() / 1000 - r.since);
  const at = new Date(run.created_at ?? "").getTime();
  return isNaN(at) ? "" : fmtDur((Date.now() - at) / 1000);
}

function idleHTML(): string {
  if (cfg.needs_local_setup) return `<div class="now-idle"><span class="dot"></span><span>Настрой модель, чтобы начать работу.</span><button class="btn btn-quiet" type="button" data-go="settings">Настроить модель →</button></div>`;
  const s = S.agentState;
  if (!s) return `<div class="now-idle"><span class="dot"></span><span>Подключение…</span></div>`;
  const at = s.brain?.last_call_at;
  const age = at ? Math.round((Date.now() / 1000 - at) / 60) : null;
  const when = age == null ? "" : age < 1 ? "модель отвечала только что" : age < 60 ? `модель отвечала ${age} мин назад` : `модель отвечала ${Math.round(age / 60)} ч назад`;
  const phrase = foreignHarness() ? `Нет текущих действий${when ? " · " + when : ""}` : `${s.phrase}${when ? " · " + when : ""}`;
  const wake = s.next_wake ? ` · следующее пробуждение ${fmtTime(s.next_wake) || s.next_wake}` : "";
  return `<div class="now-idle"><span class="dot ${!foreignHarness() && s.level === "error" ? "failed" : "ok"}"></span><span>${esc(phrase)}${esc(wake)}</span></div>`;
}

export async function render(container: HTMLElement, opts: {openAgents?: boolean} = {}): Promise<void> {
  const identity=JSON.stringify([cfg.base,cfg.key,cfg.agent]);
  if(scope!==identity){stopAgents();agents=undefined;agentDrawer=undefined;pendingAgentsOpen=false;scope=identity;showPrevious=false;currentOpen=true;currentId='';}
  // A refresh may supersede this read before a drawer exists. Keep the explicit
  // Overview intent until a winning render mounts it, but never across leave/scope.
  if(opts.openAgents)pendingAgentsOpen=true;
  root = container;
  const my = ++gen;
  const live = liveRun();
  const list = S.runs.filter((r) => r.kind !== "wake").slice(0, 40);
  focusedRun = live;
  const shown = focusedRun;
  let liveDetail: RunDetail | undefined;
  if (shown) {
    try {
      liveDetail = await runDetail(shown.id, true);
    } catch {
      liveDetail = S.evCache.get(shown.id) as RunDetail | undefined;
    }
  }
  if (my !== gen) return;
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
  const liveHTML = shown
    ? activityHTML(shown, liveDetail, live ? since(live) : fmtTime(shown.created_at))
    : idleHTML();
  const rest = list.filter((r) => r.id !== shown?.id);
  if(currentId!==(shown?.id || '')){currentId=shown?.id || '';currentOpen=true;}
  container.innerHTML = `<div class="center now">
    ${usageShell(true)}
    ${shown ? `<details class="now-current" ${currentOpen ? 'open' : ''}><summary>Текущий ход</summary>${liveHTML}</details>` : liveHTML}
    <div data-now-agents></div>
    <details class="now-context"><summary>Контекст ${live ? 'сейчас' : 'последнего ответа'}</summary>${frameStripHTML(strip, live ? "Контекст сейчас" : "Контекст последнего ответа", '<a href="#" data-go="frame">Посмотреть кадр →</a>')}</details>
    <label class="now-history-toggle"><input type="checkbox" data-now-history ${showPrevious ? 'checked' : ''}> Предыдущие ходы <span class="muted">${rest.length}</span></label>
    <section class="now-history" ${showPrevious ? '' : 'hidden'}>
    ${rest.map((r) => runRowHTML(r, { showRoom: true })).join("") || `<div class="empty"><b>Здесь появятся действия и результаты</b>
      <p>Здесь будут завершённые ходы. Поручи агенту дело в чате или выбери идею в онбординге.</p>
      <button class="btn btn-quiet" type="button" data-go="learn">Что ему можно поручить →</button></div>`}
    </section>
  </div>`;
  const previous=container.querySelector<HTMLInputElement>('[data-now-history]');
  previous?.addEventListener('change',()=>{showPrevious=previous.checked;const history=container.querySelector<HTMLElement>('.now-history');if(history)history.hidden=!showPrevious;});
  const current=container.querySelector<HTMLDetailsElement>('.now-current');
  current?.addEventListener('toggle',()=>{currentOpen=current.open;});
  mountAgents(container);
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
  if (liveFlight) { scheduleLive(true); return; }
  const my = gen, container = root;
  const live = liveRun();
  const card = root.querySelector<HTMLElement>("#turn-live");
  if (!live || !card || card.dataset.run !== live.id) {
    if (card || live) void render(root);
    return;
  }
  const flight = {};liveFlight = flight;
  try {
    const d = await runDetail(live.id, true);
    if (my !== gen || root !== container || !card.isConnected || liveRun()?.id !== live.id) return;
    const steps = card.querySelector<HTMLElement>("#turn-live-steps");
    const t = card.querySelector<HTMLElement>("#turn-live-t");
    if (t) t.textContent = since(live);
    if (steps && d) updateActivity(card, d, since(live));
  } catch {
    // следующий такт перечитает
  } finally {
    if (liveFlight === flight) liveFlight = undefined;
  }
  if (my === gen && root === container) scheduleLive(true);
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

/** A room read may recover after startup failed, without a new run event. */
export function runsChanged(previous: Run[]) {
  if (S.view !== "now" || !root) return;
  const visible = (rows: Run[]) => JSON.stringify(rows.filter((r) => r.kind !== "wake").slice(0, 40));
  // Identical idle polls must leave open details and scroll position alone.
  if (visible(previous) !== visible(S.runs)) void render(root);
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
