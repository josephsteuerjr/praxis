import { t as uiText } from "../lang/lang";
// Телефон и мини-апп Telegram — одно приложение на двоих. Прежде всего это
// МОНИТОР агента (слово владельца 07.09): открывается на «Сейчас» — идущий
// ход живьём, кадр, недавние ходы; отдельно «Чаты» (переписка, писать можно
// внутри открытого чата), «Задачи» (агенда с таймерами, доска, субагенты) и
// «Вейки» (пробуждения по расписанию). Разница между телефоном и мини-аппом —
// только вход (QR-пара или initData Telegram) и хостинг.
//
// Транспорт: ключ уезжает в адрес только пока канал не поставил cookie
// `desk_key`, дальше — cookie; на 403 ключ возвращается в адрес. Ручки из
// области окна (ходы, прогоны, задачи) телефон получает, когда канал их
// отдаёт (КОНТРАКТ-B→A §4); на 403 честно говорит «телефону не отдаётся».
import { applyTheme, el, q, toast, type Theme } from "./dom";
import "./version";
import { esc, fmtDay, fmtTime, isToday, md, runTitle, systemNoticeText } from "./text";
import {agentStatePhrase} from './status';
import { setResultFetcher, stepsHTML, type RunDetail } from "./steps";
import contract from "./contract.json";
import { RecentMap } from "./recent-map";
import { mountUsage, usageShell } from "./usage";
import { artifactCaption, mediaDescriptor, paperMediaHTML, mountPaperMedia } from "./paper-media";
import { artifactBytes, browserSave } from "./paper-transfer";
import { mountAutonomy, type AutonomyState } from "./autonomy";
import { phoneSettings } from './phone-settings';
import { phoneActions } from './phone-actions';
import { mountPaperSelects } from './paper-select';
import { apply as applyLook,load as loadLook } from './window/look';
import { FAST_MODE_HINT, serviceTierChoices, serviceTierReceipt } from './service-tier';
import { bindBrainModelControls, brainModelProblem, tierClearedByRoute, type ModelCatalog } from './model-controls';

export type Level = "ok" | "live" | "warn" | "error" | "off";

export interface AgentState {
  agent: string;
  owner?: string;
  level: Level;
  phrase: string;
  display_phrase?: unknown;
  runner?: { alive: boolean; busy: boolean; run: string; since: number; ever?: boolean };
  brain?: { last_call_at: number | null };
  next_wake?: string | null;
  anatomy?: boolean;
}

export interface Msg {
  timestamp?: string;
  outgoing?: boolean;
  text?: string;
  sender_name?: string;
  system?: boolean;
  display_notice?: unknown;
  kind?: string;
  topic_id?: number | string;
  topic_title?: string;
  media?: string;
  /** Путь ОТ ДЕРЕВА агента и вид вложения — по ним рисуется проигрыватель. */
  media_path?: string;
  media_kind?: string;
  media_name?: string;
  media_size?: number;
  media_original_path?: string;
  media_mime?: string;
  media_error?: string;
}

export interface Room {
  key: string;
  name: string;
  kind: "window" | "telegram";
  live: boolean;
  count: number;
  /** Свежесть последнего хода, мс; 0 — ходов не было. */
  at: number;
}

export interface Run {
  updated_at?: string;
  id: string;
  kind: string;
  status: string;
  terminal_status?: string;
  chat_id?: string | number | null;
  chat_title?: string;
  goal_head?: string;
  display_title?: unknown;
  created_at?: string;
}

/** Чем кончился обмен на ключ. */
export type Redeem = "ok" | "spent" | "closed" | "broke" | "offline" | "foreign" | "none";

export interface PhoneAuth {
  storageKey: string;
  /** Telegram must validate this launch's signed owner, even with a cookie. */
  requireCredential?: boolean;
  hasCredential(): boolean;
  redeem(): Promise<{ result: Redeem; key?: string; agent?: string }>;
  pairScreen(pair: Redeem | null): { title: string; text: string; retry: boolean };
}

export interface PhoneOptions {
  auth: PhoneAuth;
  platform: "web" | "telegram";
  base?: string;
  agentFallback?: string;
  sign?: string;
  onSheet?(open: boolean): void;
  theme?: () => Theme;
}

export interface PhoneApp {
  closeSheet(): void;
  sheetOpen(): boolean;
  /** Кнопка «назад» платформы: из открытого чата — к списку, из листа — закрыть. */
  back(): boolean;
}

type Tab = "now" | "chats" | "tasks" | "wakes" | "system";

const WINDOW_ROOM: string = contract.rooms.default;
const WINDOW_PREFIX: string = contract.rooms.pattern.replace(/^\^/, "").split("[")[0];
/** Старый ключ комнаты окна (до 10.09.2026) — `contract.rooms.legacy`, см. window/state.ts. */
const LEGACY_WINDOW_KEY: string = contract.rooms.legacy;
const isWindowRoom = (key: string) => key === WINDOW_ROOM || key === LEGACY_WINDOW_KEY || key.startsWith(WINDOW_PREFIX);

export class Denied extends Error {}
export class Offline extends Error {}
export class Broke extends Error {}

export function remembered(name: string): string {
  try {
    return localStorage.getItem(name) || "";
  } catch {
    return "";
  }
}

export function remember(name: string, value: string): void {
  try {
    if (value) localStorage.setItem(name, value);
    else localStorage.removeItem(name);
  } catch {
    // приватный режим: живём без хранилища
  }
}

const ICON = {
  now: '<path d="M3.5 10h3l2-5 3 10 2-5h3"/>',
  chats: '<path d="M4 5.5h12v8H8l-4 3z"/>',
  tasks: '<rect x="3.5" y="4" width="13" height="12" rx="2"/><path d="M6.5 2.8v2.5M13.5 2.8v2.5M6.5 8h7M6.5 11h4"/>',
  wakes: '<circle cx="10" cy="10.5" r="5.5"/><path d="M10 7.5v3l2 1.5M6 3.5 3.5 5.5M14 3.5l2.5 2"/>',
  system: '<rect x="3.5" y="4.5" width="13" height="5" rx="1.5"/><rect x="3.5" y="11" width="13" height="4.5" rx="1.5"/><path d="M6.2 7h.01M6.2 13.2h.01"/>',
  more: '<circle cx="5" cy="10" r="1.4"/><circle cx="10" cy="10" r="1.4"/><circle cx="15" cy="10" r="1.4"/>',
  send: '<path d="M10 15.5v-11M5.8 8.7 10 4.5l4.2 4.2"/>',
  attach: '<path d="M13.5 7.2 8.3 12.4a1.6 1.6 0 0 1-2.3-2.3l5.6-5.6a3 3 0 0 1 4.2 4.2l-6 6a4.4 4.4 0 0 1-6.2-6.2L9.2 3"/>',
  back: '<path d="M12.5 4.5 7 10l5.5 5.5"/>',
};

/** Подпись хода из слова агента или из входа: без скобок кадра и разметки. */
const clean = (s: string) =>
  s
    .replace(/^\s*…?\s*\[[^\]]{0,200}\]\s*/, "")
    .replace(/^[^:\n]{1,40}:\s*/, "")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();

/** Цель запуска годится в подпись, только если это не обрезок кадра. */
const goalLabel = (goal: string) => {
  if (/ЛЕНТА ОБРЕЗАНА|^\s*…|^\s*\[/.test(goal || "")) return "";
  const t = clean(goal || "");
  return /[\]»]$/.test(t) && t.length > 60 ? "" : t;
};

/** Заметка границы хода (`done: …`, `wait: …`) — словами. */
const noteLabel = (note: string) => {
  const raw = String(note || "").trim();
  const m = raw.match(/^(done|wait|blocked|silence)\s*:\s*(.*)$/i);
  const body = m ? m[2] : raw;
  const head = m ? (m[1].toLowerCase() === "wait" ? uiText("ui.ui.kit.phone.eb451ec6a0") : m[1].toLowerCase() === "blocked" ? uiText("ui.ui.kit.phone.82a25559f5") : "") : "";
  return body.trim() ? head + clean(body) : "";
};

export function mountPhone(root: HTMLElement, opts: PhoneOptions): PhoneApp {
  // Resolve captions after the client has selected its UI language.
  const TABS: Array<[Tab, string]> = [
    ["now", uiText("ui.ui.kit.phone.460b59b99f")],
    ["chats", uiText("ui.ui.kit.phone.b5121e526d")],
    ["tasks", uiText("ui.ui.kit.phone.03cc1c2d5a")],
    ["wakes", uiText("ui.ui.kit.phone.9643f15ecb")],
    // Пятая вкладка — управление в кармане: перезапустить упавшее, посмотреть ошибки,
    // сменить модель. Она появляется, только если рядом с каналом объявлена
    // служба управления; у телефона к домашней Элен её нет, и вкладка молчит об
    // этом словами, а не пустым экраном.
    ["system", uiText("ui.ui.kit.phone.6da215b1bd")],
  ];

  const RUN_KIND: Record<string, string> = {
    chat_turn: uiText("ui.ui.kit.phone.8eb484df7b"),
    wake: uiText("ui.ui.kit.phone.0f3c3b6b95"),
    task_window: uiText("ui.ui.kit.phone.871470f451"),
    moderation: uiText("ui.ui.kit.phone.5cb789e9aa"),
  };

  const AGENDA_KIND: Record<string, string> = {
    wake: uiText("ui.ui.kit.phone.601efa51bd"),
    window: uiText("ui.ui.kit.phone.5943c4045e"),
    message: uiText("ui.ui.kit.phone.682556de90"),
    note: uiText("ui.ui.kit.phone.efc562bc9a"),
    email: uiText("ui.ui.kit.phone.8f9e4e204d"),
  };

  applyTheme(opts.theme ? opts.theme() : "system");
  applyLook(loadLook());
  if(opts.theme)applyTheme(opts.theme());
  const base = (opts.base || "").replace(/\/+$/, "");

  // ---------------------------------------------------------------- DOM
  root.innerHTML = uiText("ui.ui.kit.phone.75101b23a1", {p0: String(ICON.more), p1: String(ICON.attach), p2: String(ICON.send), p3: String(TABS.map(
      ([id, label]) => `<button type="button" class="tab" data-tab="${id}"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON[id]}</svg><span>${label}</span></button>`,
    ).join(""))});

  const topName = q<HTMLElement>("#top-name", root);
  const topState = q<HTMLElement>("#top-state", root);
  const topPhrase = q<HTMLElement>("#top-phrase", root);
  const screen = q<HTMLElement>("#screen", root);
  const composer = q<HTMLElement>("#composer", root);
  const say = q<HTMLTextAreaElement>("#say", root);
  const send = q<HTMLButtonElement>("#send", root);
  const target = q<HTMLElement>("#composer-target", root);
  const tabsNav = q<HTMLElement>("#tabs", root);
  const veil = q<HTMLElement>("#veil", root);
  const sheet = q<HTMLElement>("#sheet", root);
  const sheetHead = q<HTMLElement>("#sheet-head", root);
  const sheetBody = q<HTMLElement>("#sheet-body", root);
  const pairBox = q<HTMLElement>("#pair", root);

  // ---------------------------------------------------------------- состояние
  let key = remembered(opts.auth.storageKey);
  let agent = opts.agentFallback || uiText("ui.ui.kit.phone.d5fd08be28");
  let tab: Tab = "now";
  /** Открытый чат на вкладке «Чаты»; "" — список. */
  let room = "";
  let roomName = "";
  let rooms: Room[] = [];
  let runs: Run[] = [];
  let state: AgentState | null = null;
  let running = false;
  let sendKey = true;
  let cookieWorks = true;
  const evOpen = new Set<string>();
  const evCache = new RecentMap<string, RunDetail>(16);
  /** run_id -> слово агента, заметка границы и собеседник (из chat-turns комнат). */
  const words = new RecentMap<string, { out: string; note: string; who: string }>(400);
  /** Ручки, которые канал телефону не отдаёт (403). */
  const closed = new Set<string>();
  let gen = 0;

  const foreign = () => !!state && state.anatomy === false && !state.runner?.alive;

  // ---------------------------------------------------------------- транспорт
  function withKey(path: string): string {
    const full = base + path;
    if (!key || !sendKey) return full;
    return full + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(key);
  }

  async function call(path: string, init?: RequestInit): Promise<Response> {
    let r: Response;
    try {
      r = await fetch(withKey(path), init);
    } catch {
      throw new Offline(uiText("ui.ui.kit.phone.39562febe7"));
    }
    if (r.status === 403 && !sendKey && key) {
      sendKey = true;
      cookieWorks = false;
      try {
        r = await fetch(withKey(path), init);
      } catch {
        throw new Offline(uiText("ui.ui.kit.phone.39562febe7"));
      }
    }
    if (r.status === 403) throw new Denied(uiText("ui.ui.kit.phone.82a9ee6629"));
    if (!r.ok) {
      const text=await r.text();let detail='';
      try{detail=JSON.parse(text).error||''}catch{/* non-JSON refusal */}
      throw new Broke(detail||path+': '+r.status);
    }
    if (sendKey && key && cookieWorks) sendKey = false;
    return r;
  }

  async function api<T = unknown>(path: string): Promise<T> {
    const r = await call(path);
    try {
      return (await r.json()) as T;
    } catch {
      throw new Broke(path + uiText("ui.ui.kit.phone.12304c5de4"));
    }
  }

  async function post<T = unknown>(path: string, body: unknown): Promise<T> {
    const r = await call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    try {
      return (await r.json()) as T;
    } catch {
      throw new Broke(path + uiText("ui.ui.kit.phone.12304c5de4"));
    }
  }

  /** Адрес файла из дерева агента для `<audio>`/`<img>`.
   *
   * ⚠ За этим адресом браузер идёт САМ, мимо `call`, — значит ни повтора с
   * ключом, ни куки-догадки тут не будет. Поэтому ключ кладём в адрес всегда,
   * когда он есть: иначе тег молча покажет пустоту, и это читалось бы как
   * «файла нет», а не как «не пустили». Так же устроен и адрес в окне.
   */

  /** Вложение реплики: звук — проигрывателем, картинка — картинкой, иначе ссылкой.
   *
   * Пусто, если пути от дерева нет: файл СНАРУЖИ дерева каналом не отдаётся, и
   * подсовывать ему адрес значило бы обещать то, чего не будет. Для такого
   * остаётся прежняя строка с именем.
   */
  function mediaBlock(m: Msg): string {
    const rel = String(m.media_path || "").trim();
    if (!rel) return "";
    return paperMediaHTML(mediaDescriptor(m)!);
  }
  const artifact = (path: string) => withKey("/api/artifact?path=" + encodeURIComponent(path) + "&preview=1");
  const disposeMedia = mountPaperMedia(root, { key: m => artifact(m.path), bytes: m => artifactBytes(artifact(m.path)), save: browserSave, voice: (m,retry) => retry ? post('/api/voice/attachment',{path:m.path}) : api('/api/voice/attachment?path='+encodeURIComponent(m.path)) });
  const disposeSelects=mountPaperSelects(root);
  window.addEventListener('pagehide',e=>{if(!e.persisted){disposeMedia();disposeSelects()}},{once:true});

  /** Ручка из области окна: 403 запоминаем и больше не спрашиваем. null — не отдаётся / не прочиталось. */
  async function scoped<T>(path: string, mark: string): Promise<T | null> {
    if (closed.has(mark)) return null;
    try {
      return await api<T>(path);
    } catch (e) {
      if (e instanceof Denied) closed.add(mark);
      return null;
    }
  }

  setResultFetcher((run, rid) => scoped(`/api/run/${encodeURIComponent(run)}/result/${encodeURIComponent(rid)}`, "run"));

  const notGiven = (what: string) =>
    uiText("ui.ui.kit.phone.b920f9dae4", {p0: String(esc(what))});

  // ---------------------------------------------------------------- запуски и живой ход
  const recent = (r: Run, minutes = 30) => {
    const at = new Date(r.updated_at || r.created_at || "").getTime();
    return !isNaN(at) && Date.now() - at < minutes * 60_000;
  };
  const runLive = (r: Run) => r.status === "running" && (foreign() ? recent(r) : !!state?.runner?.busy && state?.runner?.run === r.id);
  const roomKey = (r: Run) => {
    const k = String(r.chat_id ?? "");
    return k === LEGACY_WINDOW_KEY ? WINDOW_ROOM : k;
  };
  const liveRun = (): Run | undefined => runs.find((r) => runLive(r));

  async function loadRuns(): Promise<void> {
    const got = await scoped<Run[]>("/api/runs?limit=80", "runs");
    if (got) {
      for (const r of got) if (runs.find(old => old.id === r.id)?.status !== r.status) evCache.delete(r.id);
      runs = got;
    }
  }

  async function runDetail(id: string, fresh = false): Promise<RunDetail | undefined> {
    if (!fresh && evCache.has(id)) return evCache.get(id);
    const d = await scoped<RunDetail>("/api/run/" + encodeURIComponent(id), "run");
    if (d) evCache.set(id, d);
    return d || undefined;
  }

  function runLabel(r: Run): string {
    const text = runTitle(r);
    return text || `${RUN_KIND[r.kind] || r.kind} · ${fmtTime(r.created_at)}`;
  }

  function runRow(r: Run, opts2: { showRoom?: boolean } = {}): string {
    const status = r.terminal_status || r.status || "";
    const dot = runLive(r) ? "live" : status === "failed" ? "failed" : "";
    const who = (words.get(r.id)?.who || "").trim();
    const roomTitle = opts2.showRoom && r.chat_title ? r.chat_title : "";
    // В личке собеседник и комната — одно имя: не повторять.
    const sub = [RUN_KIND[r.kind] || r.kind, roomTitle, who && who !== roomTitle ? who : ""].filter(Boolean).join(" · ");
    return `<div class="ev ${evOpen.has(r.id) ? "open" : ""}" data-ev="${esc(r.id)}">
      <button type="button" class="ev-head" aria-expanded="${evOpen.has(r.id)}" aria-controls="run-${esc(r.id)}"><span class="ev-title">${esc(runLabel(r))}</span><span class="ev-time">${isToday(r.created_at) ? "" : fmtDay(r.created_at) + " "}${fmtTime(r.created_at)}</span><span class="dot ${dot}"></span><span class="ev-chevron" aria-hidden="true">›</span></button>
      ${sub ? `<div class="ev-sub">${esc(sub)}</div>` : ""}
      <div class="ev-steps" id="run-${esc(r.id)}" ${evOpen.has(r.id) ? "" : "hidden"}></div></div>`;
  }

  /** Оживить раскрывающиеся ходы внутри узла. */
  function bindRuns(box: HTMLElement, _redraw: () => void) {
    for (const node of box.querySelectorAll<HTMLElement>(".ev[data-ev]")) {
      const id = node.dataset.ev!;
      const head = node.querySelector<HTMLButtonElement>(".ev-head")!;
      const steps = node.querySelector<HTMLElement>(".ev-steps")!;
      const load = () => {
        steps.innerHTML = uiText("ui.ui.kit.phone.45f2d8405e");
        void runDetail(id).then((d) => {
          if (!steps.isConnected) return;
          steps.innerHTML = d ? stepsHTML(d) : closed.has("run") ? uiText("ui.ui.kit.phone.65c3a5f588") : uiText("ui.ui.kit.phone.71f13803e3");
        });
      };
      head.addEventListener("click", () => {
        const open = !evOpen.has(id);
        if (open) evOpen.add(id); else evOpen.delete(id);
        node.classList.toggle("open", open);
        head.setAttribute("aria-expanded", String(open));
        steps.hidden = !open;
        if (open) load();
      });
      if (evOpen.has(id)) load();
    }
    for (const a of box.querySelectorAll<HTMLElement>("[data-room]")) {
      a.addEventListener("click", () => openRoom(a.dataset.room!, a.dataset.roomName || a.dataset.room!));
    }
  }

  // ---------------------------------------------------------------- шапка
  function foreignPhrase(): { level: Level; phrase: string } {
    const live = runs.find((r) => r.status === "running" && recent(r));
    if (live) return { level: "live", phrase: uiText("ui.ui.kit.phone.533b75df32") + (live.chat_title ? ` · ${live.chat_title}` : "") };
    const at = state?.brain?.last_call_at;
    if (!at) return { level: "warn", phrase: uiText("ui.ui.kit.phone.b0bb72c7e7") };
    const min = (Date.now() / 1000 - at) / 60;
    const age = min < 1 ? uiText("ui.ui.kit.phone.556f1e91d5") : min < 60 ? uiText("ui.ui.kit.phone.8303e995b7", {p0: String(Math.round(min))}) : uiText("ui.ui.kit.phone.b158de05c1", {p0: String(Math.round(min / 60))});
    return min < 15 ? { level: "ok", phrase: uiText("ui.ui.kit.phone.c1f3a6d603", {p0: String(age)}) } : { level: "warn", phrase: uiText("ui.ui.kit.phone.90fd9bb4d5", {p0: String(age)}) };
  }

  type Access = "ok" | "denied" | "offline";

  async function renderState(): Promise<Access> {
    try {
      const s = await api<AgentState>("/api/state");
      const wasLive = !!liveRun();
      state = s;
      const named = s.anatomy === false && opts.agentFallback ? opts.agentFallback : s.agent;
      if (named) agent = named;
      const win = rooms.find((r) => r.key === WINDOW_ROOM);
      if (win) win.name = agent;
      if (room === WINDOW_ROOM) roomName = agent;
      document.title = agent;
      paintTop();
      if (tab === "now" && wasLive !== !!liveRun()) void renderNow();
      return "ok";
    } catch (e) {
      topState.dataset.level = "off";
      if (e instanceof Denied) {
        topPhrase.textContent = uiText("ui.ui.kit.phone.2eeb676774");
        return "denied";
      }
      topPhrase.textContent = e instanceof Broke ? uiText("ui.ui.kit.phone.2dfb8651c5") : uiText("ui.ui.kit.phone.9f0c20cc22");
      return "offline";
    }
  }

  function paintTop() {
    topName.textContent = agent;
    topName.classList.add("hand");
    if (!state) return;
    const f = foreign() ? foreignPhrase() : { level: state.level, phrase: agentStatePhrase(state) };
    topState.dataset.level = f.level;
    topPhrase.textContent = f.phrase;
    const nowTab = tabsNav.querySelector<HTMLElement>('.tab[data-tab="now"]');
    nowTab?.querySelector(".live-mark")?.remove();
    if (liveRun()) nowTab?.append(el("span", "live-mark"));
  }

  // ---------------------------------------------------------------- вкладки
  function showTab(next: Tab) {
    tab = next;
    if (next !== "chats") room = "";
    for (const b of tabsNav.querySelectorAll<HTMLElement>(".tab")) b.setAttribute("aria-current", String(b.dataset.tab === next));
    composer.hidden = !(tab === "chats" && room);
    screen.scrollTop = 0;
    render();
  }
  for (const b of tabsNav.querySelectorAll<HTMLElement>(".tab")) b.addEventListener("click", () => showTab(b.dataset.tab as Tab));

  // Кнопки вкладки «Система» — делегированием на экран: разметка перерисовывается
  // целиком, и вешать слушателей на каждую кнопку значило бы копить их пачками.
  screen.addEventListener("click", async (ev) => {
    const spot = (ev.target as HTMLElement).closest<HTMLElement>(
      "[data-restart-box],[data-box-log],[data-brain-apply],[data-interrupt],[data-panic],[data-open-agent-settings]");
    if (!spot) return;
    if(spot.hasAttribute('data-open-agent-settings')){openSettings();return;}
    const note = screen.querySelector<HTMLElement>("#sys-note");
    const view = screen.querySelector<HTMLPreElement>("#sys-log");
    const say = (text: string, bad = false) => {
      if (!note) return;
      note.className = bad ? "err" : "muted";
      note.textContent = text;
    };
    if (spot.hasAttribute("data-panic")) {
      (spot as HTMLButtonElement).disabled = true;
      say(uiText("ui.ui.kit.phone.e04a7de0f0"));
      try {
        const answer = await post<{ ok: boolean; note: string }>("/api/panic", {});
        say(answer.note, !answer.ok);
        if (!answer.ok) (spot as HTMLButtonElement).disabled = false;
      } catch {
        say(uiText("ui.ui.kit.phone.44cdeca66e"), true);
        (spot as HTMLButtonElement).disabled = false;
      }
      return;
    }
    if (spot.hasAttribute("data-interrupt")) {
      say(uiText("ui.ui.kit.phone.402838e4c8"));
      try {
        const answer = await post<{ ok: boolean; note: string }>("/api/interrupt", {
          scope: spot.getAttribute("data-interrupt") || "all" });
        say(answer.note, !answer.ok);
      } catch (e) {
        say(e instanceof Denied ? uiText("ui.ui.kit.phone.3d8f416466") : uiText("ui.ui.kit.phone.4722c6562b"), true);
      }
      return;
    }
    const logName = spot.getAttribute("data-box-log");
    if (logName && view) {
      const errors = spot.hasAttribute("data-errors");
      view.hidden = false;
      view.textContent = uiText("ui.ui.kit.phone.b7eefd36b7");
      try {
        const got = await api<{ ok: boolean; text?: string; note?: string }>(
          `/api/container-log/${encodeURIComponent(logName)}?lines=120${errors ? "&errors=1" : ""}`);
        view.textContent = got.ok
          ? got.text || (errors ? uiText("ui.ui.kit.phone.3bcdfe94c2") : uiText("ui.ui.kit.phone.0b5781e580"))
          : got.note || uiText("ui.ui.kit.phone.d283094609");
      } catch (e) {
        view.textContent = e instanceof Denied ? uiText("ui.ui.kit.phone.9d550aced9") : uiText("ui.ui.kit.phone.2ad0ab044c");
      }
      return;
    }
    const boxName = spot.getAttribute("data-restart-box");
    if (boxName) {
      say(uiText("ui.ui.kit.phone.7b2658d842", {p0: String(boxName)}));
      try {
        const answer = await post<{ ok: boolean; note: string }>("/api/containers/restart", { name: boxName });
        say(answer.note, !answer.ok);
        if (answer.ok) setTimeout(() => void renderSystem(), 3000);
      } catch (e) {
        say(e instanceof Denied ? uiText("ui.ui.kit.phone.210cf7519a") : uiText("ui.ui.kit.phone.4722c6562b"), true);
      }
      return;
    }
    const role = spot.getAttribute("data-brain-apply");
    if (role) {
      const form = screen.querySelector<HTMLElement>(`[data-brain-form="${role}"]`);
      const problem = brainModelProblem(form);
      if (problem) {say(problem);return;}
      const fields: Record<string, string | boolean> = {};
      form?.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-f]").forEach((el) => {
        const key = el.getAttribute("data-f") || "";
        if (key && el.dataset.storedEffort === undefined && el.dataset.storedServiceTier === undefined && el.value.trim() !== (el.getAttribute("data-was") ?? "")) {
          fields[key] = role === "images" && key === "enabled" ? el.value === "true" : el.value.trim();
        }
      });
      if (tierClearedByRoute(form)) fields.service_tier = '';
      if (!Object.keys(fields).length) {
        say(uiText("ui.ui.kit.phone.f6dba6a0b4"));
        return;
      }
      say(role === "images" ? uiText("ui.ui.kit.phone.f943dab47d") : uiText("ui.ui.kit.phone.57366a8072", {p0: String(role), p1: String(Object.keys(fields).join(", "))}));
      try {
        const answer = await post<{ ok: boolean; note?: string }>("/api/brain", { role, fields });
        say(answer.ok
          ? role === "images" ? answer.note || uiText("ui.ui.kit.phone.1f67f65d25")
            : `${role}: ${Object.entries(fields).map(([k, v]) => `${k}=${v || "—"}`).join(", ")}. ${answer.note || ""}`
          : answer.note || uiText("ui.ui.kit.phone.8735f485c3"), !answer.ok);
        if (answer.ok) setTimeout(() => void renderSystem(), 1500);
      } catch (e) {
        say(e instanceof Denied ? uiText("ui.ui.kit.phone.ea856508d5") : uiText("ui.ui.kit.phone.4722c6562b"), true);
      }
    }
  });

  function render() {
    if(tab!=="now")nowView?.stop();
    const my = ++gen;
    const guard = () => my === gen;
    if (tab === "now") void renderNow(guard);
    else if (tab === "chats") void (room ? renderRoom(guard) : renderChats(guard));
    else if (tab === "tasks") void renderTasks(guard);
    else if (tab === "system") void renderSystem(guard);
    else void renderWakes(guard);
  }

  // ---------------------------------------------------------------- «Сейчас»
  let liveTimer = 0;
  let nowRoot:HTMLElement|undefined,nowView:ReturnType<typeof phoneActions>|undefined;
  async function renderNow(guard:()=>boolean=()=>tab==='now') {
    if(!guard()||tab!=='now')return;
    if(!nowRoot?.isConnected) {
      nowView?.stop();nowRoot=el('section');
      const usage=el('section','now-usage');usage.innerHTML=usageShell(true);
      screen.replaceChildren(usage,nowRoot);mountUsage(usage,api);
      nowView=phoneActions(nowRoot,settingsRequest,()=>base+'\n'+key,
        undefined,{history:true});
    }
    if(!sheetKind&&!document.hidden)nowView!.start();
  }
  async function refreshLive(){await renderNow()}

  // ---------------------------------------------------------------- «Чаты»
  async function loadRooms(): Promise<void> {
    let chats: Array<{ peer_id: string; title?: string; kind?: string; messages?: number; mtime_ns?: number | string }>;
    try {
      chats = await api("/api/chats");
    } catch {
      return;
    }
    const next: Room[] = [{ key: WINDOW_ROOM, name: agent, kind: "window", live: false, count: 0, at: 0 }];
    for (const c of chats) {
      const k = String(c.peer_id);
      if (k === WINDOW_ROOM || k === LEGACY_WINDOW_KEY) continue;
      const kind: Room["kind"] = c.kind === "window" || (c.kind !== "telegram" && isWindowRoom(k)) ? "window" : "telegram";
      next.push({ key: k, name: c.title || (kind === "window" ? uiText("ui.ui.kit.phone.c44ad59fc3") : uiText("ui.ui.kit.phone.f5db3a12b7")), kind, live: false, count: c.messages || 0, at: Number(c.mtime_ns) / 1e6 || 0 });
    }
    for (const r of runs) {
      if (r.kind !== "chat_turn" || r.chat_id == null) continue;
      const k = roomKey(r);
      let found = next.find((x) => x.key === k);
      if (!found && isWindowRoom(k)) continue;
      if (!found) {
        found = { key: k, name: r.chat_title || (isWindowRoom(k) ? uiText("ui.ui.kit.phone.c44ad59fc3") : uiText("ui.ui.kit.phone.f5db3a12b7")), kind: isWindowRoom(k) ? "window" : "telegram", live: false, count: 0, at: 0 };
        next.push(found);
      }
      const at = new Date(r.created_at ?? "").getTime();
      if (!isNaN(at) && at > found.at) found.at = at;
      if (runLive(r)) found.live = true;
    }
    rooms = next.sort((a, b) => (a.kind === b.kind ? b.at - a.at : a.kind === "window" ? -1 : 1));
    const cur = rooms.find((r) => r.key === room);
    if (cur) roomName = cur.name;
    if (tab === "chats" && !room) void renderChats();
  }

  async function renderChats(guard: () => boolean = () => tab === "chats" && !room) {
    if (!rooms.length) await loadRooms();
    if (!guard()) return;
    const nodes: HTMLElement[] = [];
    let group = "";
    for (const r of rooms) {
      if (r.kind === "telegram" && group !== "telegram") {
        group = "telegram";
        nodes.push(el("div", "rooms-group", "Telegram"));
      }
      const b = el("button", "room");
      b.type = "button";
      const when = r.at ? (isToday(new Date(r.at).toISOString()) ? fmtTime(new Date(r.at).toISOString()) : fmtDay(new Date(r.at).toISOString())) : "";
      b.innerHTML = `<span class="dot ${r.live ? "live" : ""}"></span><span class="room-name${r.key === WINDOW_ROOM ? " hand" : ""}">${esc(r.name)}</span><span class="room-count">${esc(when)}</span>`;
      b.addEventListener("click", () => openRoom(r.key, r.name));
      nodes.push(b);
    }
    screen.replaceChildren(el("div", "screen-title", uiText("ui.ui.kit.phone.b5121e526d")), ...nodes);
    composer.hidden = true;
  }

  function openRoom(key2: string, name: string) {
    tab = "chats";
    room = key2;
    roomName = name;
    lastFeed = "";
    feedReady = false;
    for (const b of tabsNav.querySelectorAll<HTMLElement>(".tab")) b.setAttribute("aria-current", String(b.dataset.tab === "chats"));
    closeSheet();
    syncComposer();
    composer.hidden = false;
    render();
    bumpFeed();
    // Открытый чат — «внутренний» экран: платформе (Telegram) нужна кнопка «назад».
    opts.onSheet?.(true);
  }

  // Ссылки с карточки хода (steps.ts): открыть место в чате / надиктовать просьбу агенту в композер.
  let pendingJump = "";
  const roomByKey = (key: string) => {
    const k = key === LEGACY_WINDOW_KEY || key === WINDOW_ROOM ? WINDOW_ROOM : key;
    return { key: k, name: rooms.find((r) => r.key === k)?.name || (isWindowRoom(k) ? agent : k) };
  };
  window.addEventListener("steps-open", (e) => {
    const d = (e as CustomEvent<{ room: string; at: string }>).detail;
    const r = roomByKey(d.room);
    pendingJump = d.at;
    openRoom(r.key, r.name);
  });
  window.addEventListener("steps-compose", (e) => {
    const d = (e as CustomEvent<{ room: string; text: string }>).detail;
    const r = roomByKey(d.room);
    openRoom(r.key, r.name);
    window.setTimeout(() => { say.value = d.text; say.dispatchEvent(new Event("input")); say.focus(); }, 250);
  });

  let lastFeed = "";
  let feedReady = false;

  async function readArchive(peer: string): Promise<Msg[]> {
    return api<Msg[]>("/api/chat/" + encodeURIComponent(peer) + "?n=120");
  }

  async function renderRoom(guard: () => boolean = () => tab === "chats" && !!room) {
    if (!screen.querySelector("#feed")) {
      screen.innerHTML = uiText("ui.ui.kit.phone.9429695b38", {p0: String(ICON.back), p1: String(esc(roomName))});
      q("#back-rooms", screen).addEventListener("click", () => {
        room = "";
        composer.hidden = true;
        render();
        opts.onSheet?.(false);
      });
    }
    await renderFeed(guard);
  }

  async function renderFeed(guard: () => boolean = () => tab === "chats" && !!room): Promise<boolean> {
    const peer = room;
    const feed = screen.querySelector<HTMLElement>("#feed");
    if (!feed || !peer) return false;
    let rows: Msg[];
    try {
      rows = await readArchive(peer);
    } catch (e) {
      if (!feedReady && guard()) {
        const why = e instanceof Denied ? uiText("ui.ui.kit.phone.13a99504fe") : e instanceof Broke ? uiText("ui.ui.kit.phone.1e0d9015cc") : uiText("ui.ui.kit.phone.1548e60ff9");
        feed.innerHTML = uiText("ui.ui.kit.phone.3656c9bedb", {p0: String(esc(why))});
      }
      return false;
    }
    if (!guard() || peer !== room) return false;
    const html: string[] = [];
    let day = "";
    const windowish = isWindowRoom(peer);
    for (const m of rows) {
      const d = fmtDay(m.timestamp);
      if (d && d !== day) {
        html.push(`<div class="day">${esc(d)}</div>`);
        day = d;
      }
      const system = !!m.system || (windowish && !m.outgoing && m.sender_name === "Hélène");
      const name = m.outgoing ? agent : system ? "Hélène" : m.sender_name || "?";
      const showName = !m.outgoing && !system && !windowish && name !== state?.owner;
      const foreignRow = showName && !!state?.owner;
      const own = !m.outgoing && !system && !foreignRow && (windowish || !!state?.owner);
      const silence = system && m.kind === "silence";
      const cls = own ? "own" : system ? "system" + (silence ? " silence" : "") : m.outgoing ? "agent" : "";
      const topic = m.topic_title && !/__topic__/.test(peer) ? ` <span class="badge">${esc(m.topic_title)}</span>` : "";
      const head = m.outgoing
        ? `<span class="who-hand">${esc(agent)}</span><span>${fmtTime(m.timestamp)}</span>`
        : `${showName ? `<b>${esc(name)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}</span>`;
      const media = mediaBlock(m)
        || (m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "");
      const mediaError = m.media_error ? `<p class="media-error">${esc(m.media_error)}</p>` : "";
      const text = artifactCaption(m.text || "", m.media_path);
      const notice=systemNoticeText(m),body=notice?esc(notice).replace(/\n/g,"<br>"):md(text);
      html.push(`<div class="msg ${cls}" data-at="${esc(m.timestamp || "")}"><div class="msg-head">${head}</div><div class="msg-body">${body}${media}${mediaError}</div></div>`);
    }
    const next = html.join("");
    if (next === lastFeed && feedReady) return false;
    lastFeed = next;
    const nearBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight < 200;
    feed.innerHTML = next || uiText("ui.ui.kit.phone.a0e02db634", {p0: String(windowish ? uiText("ui.ui.kit.phone.38455fc008") : uiText("ui.ui.kit.phone.4b71346fa2"))});
    if (nearBottom || !feedReady) screen.scrollTop = screen.scrollHeight;
    feedReady = true;
    if (pendingJump) {
      const want = Date.parse(pendingJump);
      pendingJump = "";
      let best: HTMLElement | null = null;
      let gap = Infinity;
      for (const el of feed.querySelectorAll<HTMLElement>(".msg[data-at]")) {
        const t = Date.parse(el.dataset.at || "");
        const d2 = Math.abs(t - want);
        if (!isNaN(t) && d2 < gap) { gap = d2; best = el; }
      }
      if (best && gap <= 5 * 60_000) {
        best.scrollIntoView({ block: "center" });
        best.classList.add("flash");
        const hit = best;
        window.setTimeout(() => hit.classList.remove("flash"), 2400);
      }
    }
    return true;
  }

  // ---------------------------------------------------------------- композер (только в открытом чате)
  function syncComposer() {
    target.textContent = isWindowRoom(room) ? "" : uiText("ui.ui.kit.phone.5a6cd515aa", {p0: String(roomName)});
    say.placeholder = isWindowRoom(room) ? uiText("ui.ui.kit.phone.c32d557b4c", {p0: String(agent)}) : uiText("ui.ui.kit.phone.6862dbe92b");
    say.setAttribute("aria-label", isWindowRoom(room) ? uiText("ui.ui.kit.phone.0335ba9443", {p0: String(agent)}) : uiText("ui.ui.kit.phone.dffef81287", {p0: String(roomName)}));
  }
  say.addEventListener("input", () => {
    say.style.height = "auto";
    say.style.height = Math.min(say.scrollHeight, 140) + "px";
  });
  // -------------------------------------------------------------- картинка с телефона
  // Тот же контракт, что у окна (/api/say с `attachments`): камера или галерея —
  // одна кнопка. Снимок едет в зрение, документ — в рабочую папку хода.
  const attach = q<HTMLButtonElement>("#attach", root);
  const attachInput = q<HTMLInputElement>("#attach-input", root);
  const filesBox = q<HTMLElement>("#composer-files", root);
  const SHOT_MIME = ["image/png", "image/jpeg", "image/webp", "image/gif"];
  let shots: Array<{ name: string; mime: string; data: string; url: string; size: number }> = [];

  function clearShots() {
    for (const s of shots) URL.revokeObjectURL(s.url);
    shots = [];
    paintShots();
  }

  function paintShots() {
    filesBox.hidden = shots.length === 0;
    filesBox.innerHTML = shots
      .map((s, i) => uiText("ui.ui.kit.phone.09ce3e09e5", {p0: String(SHOT_MIME.includes(s.mime) ? `<img src="${s.url}" alt="">` : `<span>${esc(s.name)}</span>`), p1: String(i)}))
      .join("");
    filesBox.querySelectorAll<HTMLButtonElement>("button[data-i]").forEach((b) => {
      b.addEventListener("click", () => {
        const [gone] = shots.splice(Number(b.dataset.i), 1);
        if (gone) URL.revokeObjectURL(gone.url);
        paintShots();
      });
    });
  }

  attach.setAttribute("aria-label", uiText("ui.ui.kit.phone.8bec1fdea7"));
  attach.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", async () => {
    for (const f of Array.from(attachInput.files || [])) {
      if (f.size > 64 * 1024 * 1024) { toast(uiText("ui.ui.kit.phone.c949bcf5c9")); continue; }
      if (shots.reduce((sum, s) => sum + s.size, 0) + f.size > 128 * 1024 * 1024) { toast(uiText("ui.ui.kit.phone.ba1f58135c")); continue; }
      if (shots.length >= 16) { toast(uiText("ui.ui.kit.phone.2016a0a424")); break; }
      try {
        const data = await new Promise<string>((resolve, reject) => {
          const r = new FileReader();
          r.onerror = () => reject(r.error);
          r.onload = () => resolve(String(r.result || "").split(",", 2)[1] || "");
          r.readAsDataURL(f);
        });
        shots.push({ name: f.name || uiText("ui.ui.kit.phone.48d8c3e9ee"), mime: f.type || "application/octet-stream", data, url: URL.createObjectURL(f), size: f.size });
      } catch {
        toast(uiText("ui.ui.kit.phone.d45a4c3109"));
      }
    }
    attachInput.value = "";
    paintShots();
  });

  say.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void doSend();
    }
  });
  send.addEventListener("click", () => void doSend());

  async function doSend() {
    const text = say.value.trim();
    const files = shots.slice();
    if ((!text && !files.length) || !room) return;
    send.disabled = true;
    try {
      const payload: Record<string, unknown> = { text };
      if (!isWindowRoom(room)) payload.chat = room;
      if (files.length) payload.attachments = files.map((s) => ({ name: s.name, mime: s.mime, data: s.data }));
      await post("/api/say", payload);
      say.value = "";
      say.style.height = "auto";
      if (files.length) clearShots();
      const unread = state?.runner?.ever === false;
      toast(
        !isWindowRoom(room)
          ? uiText("ui.ui.kit.phone.79e8352341", {p0: String(roomName)})
          : unread
            ? uiText("ui.ui.kit.phone.0584573586")
            : uiText("ui.ui.kit.phone.8e8ad00b82"),
      );
      setTimeout(() => bumpFeed(), 1200);
    } catch (e) {
      toast(
        e instanceof Denied
          ? uiText("ui.ui.kit.phone.d3394ba2c9")
          : e instanceof Offline
            ? uiText("ui.ui.kit.phone.7f037cf79c")
            : uiText("ui.ui.kit.phone.577168a5b5"),
      );
    }
    send.disabled = false;
  }

  // ---------------------------------------------------------------- «Задачи»
  interface Agenda {
    active: Array<{ id?: string; kind?: string; goal?: string; target?: string; when?: string; recur?: string; status?: string }>;
    total: number;
  }
  interface ForgeTask {
    id: string;
    status?: string;
    priority?: string;
    goal?: string;
    agents?: Array<{ id: string; role?: string; status?: string; parent_unit_id?: string;
      custody?: "result" | "unknown" | "unresolved_stop"; ancestry_unknown?: boolean }>;
  }

  async function renderSystem(guard: () => boolean = () => tab === "system") {
    screen.innerHTML = uiText("ui.ui.kit.phone.fe732bbd85");
    const [boxes, brain, models, panicState] = await Promise.all([
      scoped<{ available: boolean; why: string; containers: Array<{ name: string; up: boolean; status: string; image: string }> }>("/api/containers", "containers"),
      scoped<{ ok: boolean; note?: string; roles?: Record<string, Record<string, string>>; service_tiers?:Record<string,{requested?:string|null;actual?:string|null;available?:boolean|null;codex?:boolean}>; frameworks?: Record<string, unknown>; images?: Record<string, string | boolean>; image_models?: string[] }>("/api/brain", "brain"),
      scoped<{ ok: boolean; by_framework?: Record<string, ModelCatalog & { ok: boolean; models?: string[] }> }>("/api/brain-models", "brain"),
      api<{ stopped: boolean | null }>("/api/panic").catch(() => null),
    ]);
    if (!guard()) return;
    // 12.09: прерывание живого хода — просьба в memory/.control, движок снимает ход на тике.
    const interruptHTML = uiText("ui.ui.kit.phone.be5278eedd", {p0: String(panicState?.stopped ? " disabled" : ""), p1: String(panicState?.stopped ? uiText("ui.ui.kit.phone.d217567ac9") : uiText("ui.ui.kit.phone.0d4f3f205a"))});
    const mountPause = () => {
      const slot=screen.querySelector<HTMLElement>("[data-autonomy-box]");
      if (slot) mountAutonomy(slot,{read:()=>api<AutonomyState>("/api/autonomy"),write:action=>post("/api/autonomy",{action}),identity:()=>location.origin+location.pathname,buttonClass:"chip"});
    };
    if (!boxes || !boxes.available) {
      screen.innerHTML = interruptHTML + `<p class="muted" id="sys-note" style="padding:0 14px"></p>` +
        (closed.has("containers")
          ? notGiven(uiText("ui.ui.kit.phone.6da215b1bd"))
          : `<div class="empty">${esc(boxes?.why || uiText("ui.ui.kit.phone.07edf33241"))}</div>`);
      mountPause(); return;
    }
    const rows = boxes.containers
      .map(
        (c) => uiText("ui.ui.kit.phone.fb4f73ddb3", {p0: String(esc(c.name)), p1: String(esc(c.name)), p2: String(c.up ? "muted" : "err"), p3: String(esc(c.status)), p4: String(esc(c.image)), p5: String(esc(c.name)), p6: String(esc(c.name)), p7: String(esc(c.name))}),
      )
      .join("");
    const live: string[] = [];
    for (const spec of Object.values(models?.by_framework || {})) if (spec.ok && spec.models) live.push(...spec.models);
    // 12.09: у роли четыре свои ручки (модель, фреймворк, запасная модель и её фреймворк)
    // плюс усилие — раньше отсюда менялась только основная модель, и запасная жила
    // лишь в llm.json руками. Шлём только то, что изменили.
    const frameworks = Object.keys(brain?.frameworks || {});
    const efforts = ["", "low", "medium", "high", "xhigh"];
    const opt = (values: string[], current: string) =>
      [...new Set([current, ...values])]
        .map((v) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(v || "—")}</option>`)
        .join("");
    const field = (label: string, control: string) =>
      `<label style="display:flex;flex-direction:column;gap:2px;padding:4px 0"><span class="muted">${label}</span>${control}</label>`;
    const datalist = `<datalist id="brain-models">${[...new Set(live)].map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`;
    const brainRows = brain?.ok
      ? datalist + Object.entries(brain.roles || {}).filter(([role]) => role !== "evaluator" && role !== "memory")
          .map(([role, spec]) => {
            const f = (k: string) => String(spec[k] ?? "");
            const input = (k: string, placeholder = "") =>
              `<input list="brain-models" data-f="${k}" data-was="${esc(f(k))}" value="${esc(f(k))}" placeholder="${esc(placeholder)}" autocapitalize="off" autocorrect="off" spellcheck="false">`;
            const select = (k: string, values: string[]) =>
              `<select data-f="${k}" data-was="${esc(f(k))}">${opt(values, f(k))}</select>`;
            const tier = `<select data-f="service_tier" data-was="${esc(f('service_tier'))}">${serviceTierChoices(f('service_tier')).map(([value,title])=>`<option value="${esc(value)}"${value===f('service_tier')?' selected':''}>${esc(title)}</option>`).join('')}</select>`;
            return uiText("ui.ui.kit.phone.9ea3780dec", {p0: String(esc(role)), p1: String(brain.service_tiers?.[role]?.codex===true || brain.service_tiers?.[role]?.available===true), p2: String(brain.service_tiers?.[role]?.available), p3: String(esc(role)), p4: String(esc(f("framework"))), p5: String(esc(f("model"))), p6: String(field(uiText("ui.ui.kit.phone.f3bdf7cce3"), input("model"))), p7: String(field(uiText("ui.ui.kit.phone.f1d6279c5a"), select("framework", frameworks))), p8: String(field(uiText("ui.ui.kit.phone.af919964b2"), input("fallback_model", uiText("ui.ui.kit.phone.e2efc81fb0")))), p9: String(field(uiText("ui.ui.kit.phone.058447cd97"), select("fallback_framework", ["", ...frameworks]))), p10: String(field(uiText("ui.ui.kit.phone.9397f57b74"), select("reasoning_effort", efforts))), p11: String(brain.service_tiers?.[role]?.codex || brain.service_tiers?.[role]?.available?'':' hidden'), p12: String(field(uiText("ui.ui.kit.phone.0a2afcf188"), tier)), p13: String(esc(serviceTierReceipt(brain.service_tiers?.[role]?.actual))), p14: String(esc(role))});
          })
          .join("")
      : `<div class="empty">${esc(brain?.note || uiText("ui.ui.kit.phone.5121320c17"))}</div>`;
    const imageConfig = brain?.images || { enabled: false, model: "gpt-image-2", quality: "auto", size: "auto", background: "opaque" };
    const imageValue = (key: string) => String(imageConfig[key] ?? "");
    const imageSelect = (key: string, choices: Array<[string, string]>) => `<select data-f="${key}" data-was="${esc(imageValue(key))}">${choices.map(([value, title]) => `<option value="${esc(value)}"${imageValue(key) === value ? " selected" : ""}>${esc(title)}</option>`).join("")}</select>`;
    const imageRows = brain?.ok ? uiText("ui.ui.kit.phone.ccc53d11b4", {p0: String(field(uiText("ui.ui.kit.phone.44477977e7"), imageSelect("enabled", [["true", uiText("ui.ui.kit.phone.4b043d0b16")], ["false", uiText("ui.ui.kit.phone.1e4b686990")]]))), p1: String(field(uiText("ui.ui.kit.phone.f3bdf7cce3"), `<input data-f="model" data-was="${esc(imageValue("model"))}" value="${esc(imageValue("model"))}" list="image-models" autocapitalize="off" autocorrect="off" spellcheck="false"><datalist id="image-models">${(brain.image_models || ["gpt-image-2"]).map(model => `<option value="${esc(model)}"></option>`).join("")}</datalist>`)), p2: String(field(uiText("ui.ui.kit.phone.43c6960012"), imageSelect("quality", [["auto", uiText("ui.ui.kit.phone.353cf589e9")], ["low", uiText("ui.ui.kit.phone.65fd24f1cb")], ["medium", uiText("ui.ui.kit.phone.cca5c7ff9e")], ["high", uiText("ui.ui.kit.phone.71abc1e1f0")]]))), p3: String(field(uiText("ui.ui.kit.phone.572095d4a7"), uiText("ui.ui.kit.phone.eab971027c", {p0: String(esc(imageValue("size"))), p1: String(esc(imageValue("size")))}))), p4: String(field(uiText("ui.ui.kit.phone.2084425180"), imageSelect("background", [["opaque", uiText("ui.ui.kit.phone.7c0ef1469b")], ["transparent", uiText("ui.ui.kit.phone.504001466b")], ["auto", uiText("ui.ui.kit.phone.353cf589e9")]])))}) : "";
    screen.innerHTML = interruptHTML +
      uiText("ui.ui.kit.phone.ed15ed17b2", {p0: String(boxes.containers.length), p1: String(rows)}) +
      uiText("ui.ui.kit.phone.0be498f1ba") +
      uiText("ui.ui.kit.phone.f25403400c", {p0: String(esc(FAST_MODE_HINT())), p1: String(brainRows), p2: String(imageRows)}) +
      `<p class="muted" id="sys-note" style="padding:0 14px"></p>` +
      `<pre class="mono" id="sys-log" style="padding:0 14px;white-space:pre-wrap;overflow:auto;max-height:50vh" hidden></pre>`;
    bindBrainModelControls(screen, models?.by_framework || {});
    mountPause();
  }

  async function renderTasks(guard: () => boolean = () => tab === "tasks") {
    screen.innerHTML = uiText("ui.ui.kit.phone.fe732bbd85");
    const [agenda, forge, board] = await Promise.all([
      scoped<Agenda>("/api/agenda", "agenda"),
      scoped<ForgeTask[]>("/api/forge", "forge"),
      scoped<{ board?: string }>("/api/board", "board"),
    ]);
    if (!guard()) return;
    if (!agenda && !forge && !board && (closed.has("agenda") || closed.has("forge") || closed.has("board"))) {
      screen.innerHTML = uiText("ui.ui.kit.phone.1fb2c91e1a") + notGiven(uiText("ui.ui.kit.phone.03cc1c2d5a"));
      return;
    }
    const agendaHTML = agenda
      ? uiText("ui.ui.kit.phone.e6d12e2a42", {p0: String(agenda.active.length), p1: String(agenda.total)}) +
        (agenda.active
          .slice(0, 40)
          .map(
            (t) => `<div class="task-row">
              <div class="task-head"><span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>${t.when ? ` <b class="mono">${esc(t.when)}</b>` : ""}${t.recur ? uiText("ui.ui.kit.phone.2e7cadc42b", {p0: String(esc(t.recur))}) : ""}<span class="muted"> · ${esc(t.status || "")}</span></div>
              ${t.target ? `<div class="muted">→ ${esc(String(t.target))}</div>` : ""}
              <div class="task-goal">${esc((t.goal || "").slice(0, 220))}</div>
            </div>`,
          )
          .join("") || uiText("ui.ui.kit.phone.bcccd4700b"))
      : uiText("ui.ui.kit.phone.93b3a7a320");
    const forgeHTML = forge
      ? uiText("ui.ui.kit.phone.a80d93dab6", {p0: String(forge.length)}) +
        (forge
          .map((t) => {
            const units = (t.agents || [])
              .map((a) => `<div class="task-unit">
                <span class="mono">${esc(a.id)}</span> <span class="badge">${esc(a.role || uiText("ui.ui.kit.phone.5dcaaef2e4"))}</span>
                <b class="${a.status === "error" || a.status === "failed" || a.status === "unknown" ? "err" : ""}">${esc(a.status === "unknown" ? uiText("ui.ui.kit.phone.dc0a8a936d") : a.status || uiText("ui.ui.kit.phone.222b4bc59f"))}</b>
                ${a.custody === "unresolved_stop" ? uiText("ui.ui.kit.phone.d4980af364") : ""}
                ${a.parent_unit_id ? uiText("ui.ui.kit.phone.89498b0745", {p0: String(esc(a.parent_unit_id))}) : ""}
                ${a.ancestry_unknown ? uiText("ui.ui.kit.phone.d56e69fc3d") : ""}
              </div>`)
              .join("");
            return uiText("ui.ui.kit.phone.5175045eae", {p0: String(esc(t.id)), p1: String(esc(t.status === "done" ? uiText("ui.ui.kit.phone.5f9b6607de") : t.status || uiText("ui.ui.kit.phone.222b4bc59f"))), p2: String((t.agents || []).length), p3: String(units || uiText("ui.ui.kit.phone.0919e6780c"))});
          })
          .join("") || uiText("ui.ui.kit.phone.de9c047de7"))
      : uiText("ui.ui.kit.phone.a7ad297b8a", {p0: String(closed.has("forge") ? notGiven(uiText("ui.ui.kit.phone.cf5873b9b1")) : uiText("ui.ui.kit.phone.284c7e8638"))});
    const boardHTML = board ? uiText("ui.ui.kit.phone.d737ca31bf", {p0: String(md(board.board || uiText("ui.ui.kit.phone.9df3b9ee07")))}) : "";
    screen.innerHTML = agendaHTML + forgeHTML + boardHTML;
  }

  // ---------------------------------------------------------------- «Пробуждения»
  async function renderWakes(guard: () => boolean = () => tab === "wakes") {
    screen.innerHTML = uiText("ui.ui.kit.phone.fe732bbd85");
    const got = await scoped<Run[]>("/api/runs?kind=wake&limit=40", "runs");
    if (!guard()) return;
    const next = state?.next_wake ? uiText("ui.ui.kit.phone.b432327026", {p0: String(esc(fmtTime(state.next_wake) || state.next_wake))}) : "";
    if (!got) {
      screen.innerHTML = uiText("ui.ui.kit.phone.3640965d8e") + next + (closed.has("runs") ? notGiven(uiText("ui.ui.kit.phone.9643f15ecb")) : uiText("ui.ui.kit.phone.284c7e8638"));
      return;
    }
    screen.innerHTML =
      uiText("ui.ui.kit.phone.eb534b522c", {p0: String(got.length)}) +
      next +
      (got.map((r) => runRow(r)).join("") || uiText("ui.ui.kit.phone.feb9c5c582"));
    bindRuns(screen, () => void renderWakes());
  }

  // ---------------------------------------------------------------- «Ещё»
  let sheetKind: "" | "more" | "settings" = "";
  let settingsRoot:HTMLElement|undefined;
  let dialogFocus:HTMLElement|null=null;
  let sheetEpoch=0,sheetCloseTimer:ReturnType<typeof setTimeout>|undefined;
  async function settingsRequest<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T> {
    const response=await call(path,body===undefined?{signal,cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal,cache:'no-store'});
    return response.json();
  }
  function openDialog(kind:'settings',title:string) {
    const opening=++sheetEpoch;clearTimeout(sheetCloseTimer);
    dialogFocus=document.activeElement instanceof HTMLElement?document.activeElement:null;
    nowView?.stop();sheetKind=kind;sheet.classList.add('phone-dialog');
    sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-label',title);
    sheetHead.replaceChildren(el('span','',title));
    const close=el('button','phone-dialog-close','×') as HTMLButtonElement;close.type='button';close.setAttribute('aria-label',uiText("ui.ui.kit.phone.6e5154d19e")+title.toLowerCase());close.addEventListener('click',closeSheet);sheetHead.append(close);
    veil.hidden=false;sheet.hidden=false;requestAnimationFrame(()=>{if(opening!==sheetEpoch||sheetKind!==kind)return;veil.classList.add('show');sheet.classList.add('show');close.focus()});opts.onSheet?.(true);
  }
  function openSettings() {
    openDialog('settings',uiText("ui.ui.kit.phone.985b5e0f2c"));
    if(!settingsRoot){settingsRoot=el('div');phoneSettings(settingsRoot,settingsRequest)}
    sheetBody.replaceChildren(settingsRoot);
  }
  function openSheet() {
    const opening=++sheetEpoch;clearTimeout(sheetCloseTimer);
    nowView?.stop();sheet.classList.remove('phone-dialog');sheet.removeAttribute('aria-modal');sheet.removeAttribute('role');
    sheetKind = "more";
    veil.hidden = false;
    sheet.hidden = false;
    requestAnimationFrame(() => {
      if(opening!==sheetEpoch||sheetKind!=='more')return;
      veil.classList.add("show");
      sheet.classList.add("show");
    });
    drawMore();
    opts.onSheet?.(true);
  }
  function closeSheet() {
    if (!sheetKind) return;
    const closing=++sheetEpoch;clearTimeout(sheetCloseTimer);

    sheetKind = "";
    if(dialogFocus?.isConnected)dialogFocus.focus({preventScroll:true});dialogFocus=null;
    veil.classList.remove("show");
    sheet.classList.remove("show");
    sheetCloseTimer=setTimeout(() => {
      if (closing===sheetEpoch&&!sheetKind) {
        veil.hidden = true;
        sheet.hidden = true;
        sheet.classList.remove('phone-dialog');sheet.removeAttribute('aria-modal');sheet.removeAttribute('role');sheet.removeAttribute('aria-label');
        sheetBody.replaceChildren();
      }
    }, 220);
    opts.onSheet?.(false);
    if(tab==='now'&&running&&!document.hidden)nowView?.start();
  }
  veil.addEventListener("click", () => { if(sheetKind==='more')closeSheet(); });
  root.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&sheetKind){event.preventDefault();closeSheet();return}
    if(event.key==='Tab'&&sheetKind==='settings'){
      const items=[...sheet.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]')].filter(node=>node.getClientRects().length>0);
      const first=items[0],last=items.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
    }
  });
  window.addEventListener('helene:shell-update',event=>{
    if(sheetKind==='settings'&&settingsRoot?.dataset.dirty==='true'){
      event.preventDefault();toast(uiText("ui.ui.kit.phone.de8013f46a"));
    }
  });
  q("#btn-more", root).addEventListener("click", () => (sheetKind ? closeSheet() : openSheet()));
  let dragY = 0;
  sheet.addEventListener("touchstart", (e) => (dragY = e.touches[0].clientY), { passive: true });
  sheet.addEventListener("touchend", (e) => {
    if(sheetKind==='settings')return;
    if (e.changedTouches[0].clientY - dragY > 70 && sheetBody.scrollTop <= 0) closeSheet();
  });

  function drawMore() {
    sheetHead.innerHTML = uiText("ui.ui.kit.phone.f7e4d55258");
    sheetBody.replaceChildren();
    const about = el("div", "more-row");
    about.append(el("h4", "", uiText("ui.ui.kit.phone.6a17df41fb")));
    const kind = opts.platform === "telegram" ? uiText("ui.ui.kit.phone.4f038f6ee0") : uiText("ui.ui.kit.phone.7d17732d49");
    about.append(el("p", "field-hint", uiText("ui.ui.kit.phone.92fef093d2", {p0: String(agent), p1: String(kind), p2: String(key ? uiText("ui.ui.kit.phone.747f78e5a0") : uiText("ui.ui.kit.phone.dc9337ff5b")), p3: String(base || location.host)})));
    sheetBody.append(about);
    // Принудительное обновление руками — для iPhone, где WebView держит старую
    // сборку: та же сверка, что идёт сама при возврате на экран.
    const upd = el("div", "more-row");
    upd.append(el("h4", "", uiText("ui.ui.kit.phone.baba778a13")));
    const updBtn = el("button", "btn btn-quiet", uiText("ui.ui.kit.phone.d473fe8f09"));
    updBtn.type = "button";
    const updNote = el("p", "field-hint", uiText("ui.ui.kit.phone.412f95c00c", {p0: String((document.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/index-"]')?.getAttribute("src") || "").split("/").pop()?.replace(/^index-|\.js$/g, "") || "?")}));
    updBtn.addEventListener("click", async () => {
      updBtn.disabled = true;
      updNote.textContent = uiText("ui.ui.kit.phone.2543857420");
      const went = window.heleneCheckShell ? await window.heleneCheckShell() : false;
      if (!went) {
        updNote.textContent = uiText("ui.ui.kit.phone.ccfba118ff");
        updBtn.disabled = false;
      }
    });
    upd.append(updBtn, updNote);
    sheetBody.append(upd);
    const unpair = el("div", "more-row");
    unpair.append(el("h4", "", uiText("ui.ui.kit.phone.7ba56eea85")));
    const b = el("button", "btn btn-danger", uiText("ui.ui.kit.phone.c6a2929dae"));
    b.type = "button";
    b.addEventListener("click", () => {
      remember(opts.auth.storageKey, "");
      key = "";
      sendKey = true;
      closeSheet();
      accessLost();
    });
    unpair.append(b, el("p", "field-hint", uiText("ui.ui.kit.phone.6e1d35df60")));
    sheetBody.append(unpair);
  }

  // ---------------------------------------------------------------- опрос
  const FEED_MIN = 6000;
  const STATE_MIN = 8000;
  const STATE_MAX = 60000;
  const RUNS_MIN = 20000;
  const feedCeiling = () => (live ? 60000 : 30000);
  let feedDelay = FEED_MIN;
  let stateDelay = STATE_MIN;
  let feedTimer = 0;
  let stateTimer = 0;
  let runsTimer = 0;
  let feedBusy = false;
  let feedAgain = false;
  let stateBusy = false;

  function scheduleFeed() {
    clearTimeout(feedTimer);
    if (!running || document.hidden || !(tab === "chats" && room)) return;
    feedTimer = window.setTimeout(() => void tickFeed(), feedDelay);
  }
  function scheduleState() {
    clearTimeout(stateTimer);
    if (!running || document.hidden) return;
    stateTimer = window.setTimeout(() => void tickState(), stateDelay);
  }
  function scheduleRuns() {
    clearTimeout(runsTimer);
    if (!running || document.hidden) return;
    runsTimer = window.setTimeout(() => void tickRuns(), RUNS_MIN);
  }
  async function tickFeed() {
    if (feedBusy) {
      feedAgain = true;
      return;
    }
    feedBusy = true;
    let changed = false;
    try {
      changed = await renderFeed();
    } finally {
      feedBusy = false;
    }
    if (feedAgain) {
      feedAgain = false;
      feedDelay = FEED_MIN;
      void tickFeed();
      return;
    }
    feedDelay = changed ? FEED_MIN : Math.min(feedCeiling(), Math.round(feedDelay * 1.6));
    scheduleFeed();
  }
  async function tickState() {
    if (stateBusy) return;
    openLive();
    stateBusy = true;
    let st: Access;
    try {
      st = await renderState();
    } finally {
      stateBusy = false;
    }
    if (st === "denied") {
      accessLost();
      return;
    }
    stateDelay = st === "ok" ? STATE_MIN : Math.min(STATE_MAX, Math.round(stateDelay * 1.6));
    scheduleState();
  }
  async function tickRuns() {
    // Без живых событий (канал их телефону не отдаёт) запуски перечитываются
    // сами — иначе «Сейчас» узнал бы о ходе только после перезагрузки.
    await loadRuns();
    paintTop();
    if (tab === "now") void renderNow();
    scheduleRuns();
  }
  function bumpFeed() {
    feedDelay = FEED_MIN;
    clearTimeout(feedTimer);
    if (running && !document.hidden && tab === "chats" && room) void tickFeed();
  }
  function bumpState() {
    stateDelay = STATE_MIN;
    clearTimeout(stateTimer);
    if (running && !document.hidden) void tickState();
  }

  let live: WebSocket | null = null;
  let liveTries = 0;
  function openLive() {
    if (live || !running || document.hidden || liveTries >= 1) return;
    liveTries += 1;
    try {
      const url = new URL(withKey("/tunnel"), location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      live = new WebSocket(url.toString());
    } catch {
      live = null;
      return;
    }
    live.addEventListener("open", () => (liveTries = 0));
    const socket = live;
    live.addEventListener("message", (e) => {
      let ev: { t?: unknown; run_id?: unknown } = {};
      try {
        ev = JSON.parse(String(e.data)).event || {};
      } catch {
        return;
      }
      const kind = String(ev.t ?? "");
      if (kind === "run") {
        if (ev.run_id) evCache.delete(String(ev.run_id));
        void loadRuns().then(() => {
          void loadRooms();
          paintTop();
          if (tab === "now") {
            const card = screen.querySelector<HTMLElement>("#turn-live");
            if (card && card.dataset.run === liveRun()?.id) void refreshLive();
            else void renderNow();
          }
          else if (tab === "wakes") void renderWakes();
        });
        bumpFeed();
        bumpState();
      } else if (kind === "llm") {
        bumpState();
        if (tab === "now") {
          clearTimeout(liveTimer);
          liveTimer = window.setTimeout(() => void refreshLive(), 300);
        }
      } else if (kind === "health") {
        bumpState();
      }
    });
    live.addEventListener("close", () => {
      if (live !== socket) return;
      live = null;
      liveTries = 0;
      if (running && !document.hidden) window.setTimeout(openLive, 2000);
    });
  }
  function closeLive() {
    if (live) live.close();
    live = null;
  }

  // ---------------------------------------------------------------- доступ
  function showPair(title: string, text: string, action?: { label: string; onClick: () => void }) {
    pairBox.hidden = false;
    pairBox.innerHTML = `<h1>${esc(title)}</h1><p>${esc(text)}</p>`;
    if (action) {
      const b = el("button", "btn btn-primary", action.label);
      b.type = "button";
      b.addEventListener("click", action.onClick);
      pairBox.append(b);
    }
    if (opts.sign) pairBox.append(el("div", "pair-sign", opts.sign));
  }

  function accessLost() {
    nowView?.stop();
    running = false;
    clearTimeout(feedTimer);
    clearTimeout(stateTimer);
    clearTimeout(runsTimer);
    clearTimeout(liveTimer);
    closeLive();
    const s = opts.auth.pairScreen(key ? "closed" : "none");
    showPair(s.title, s.text, { label: key ? uiText("ui.ui.kit.phone.dc7dbeb809") : uiText("ui.ui.kit.phone.89dbfb9516"), onClick: () => void (key ? recheck() : start()) });
  }

  async function recheck() {
    if ((await renderState()) !== "ok") return;
    enter();
  }

  function enter() {
    pairBox.hidden = true;
    running = true;
    liveTries = 0;
    openLive();
    stateDelay = STATE_MIN;
    scheduleState();
    scheduleRuns();
    void loadRuns().then(() => {
      paintTop();
      void loadRooms();
      showTab("now");
    });
  }

  async function start() {
    if (opts.auth.requireCredential) {
      // A cookie/remembered device must never substitute for Telegram's
      // current signed user (e.g. a different Telegram account on this phone).
      key = '';
      remember(opts.auth.storageKey, '');
      const got = opts.auth.hasCredential() ? await opts.auth.redeem() : {result:'none' as Redeem};
      if (got.result !== 'ok' || !got.key) {
        const s = opts.auth.pairScreen(got.result);
        showPair(s.title,s.text,s.retry ? {label:uiText("ui.ui.kit.phone.89dbfb9516"),onClick:()=>void start()} : undefined);
        return;
      }
      key = got.key;
      sendKey = true;
      if (got.agent) agent = got.agent;
      remember(opts.auth.storageKey,key);
    }
    let pair: Redeem | null = null;
    // Legacy renderer clients can reuse an existing device. Telegram above
    // always validates the current launch before reaching this branch.
    if (key) {
      const current = await renderState();
      if (current === 'ok') { enter(); return; }
      if (current === 'offline') {
        const s=opts.auth.pairScreen('offline');
        showPair(s.title,s.text,{label:uiText("ui.ui.kit.phone.89dbfb9516"),onClick:()=>void start()});
        return;
      }
    }
    if (opts.auth.hasCredential() && !opts.auth.requireCredential) {
      const got = await opts.auth.redeem();
      pair = got.result;
      if (pair === "ok" && got.key) {
        key = got.key;
        sendKey = true;
        if (got.agent) agent = got.agent;
        remember(opts.auth.storageKey, key);
      }
    }
    const access = await renderState();
    if (access === "ok") {
      enter();
      return;
    }
    if (access === "offline") {
      const s = opts.auth.pairScreen("offline");
      showPair(s.title, s.text, { label: uiText("ui.ui.kit.phone.89dbfb9516"), onClick: () => void start() });
      return;
    }
    if (pair && pair !== "ok") {
      const s = opts.auth.pairScreen(pair);
      showPair(s.title, s.text, s.retry ? { label: uiText("ui.ui.kit.phone.89dbfb9516"), onClick: () => void start() } : undefined);
      return;
    }
    if (key) {
      accessLost();
      return;
    }
    const s = opts.auth.pairScreen(null);
    showPair(s.title, s.text, { label: uiText("ui.ui.kit.phone.89dbfb9516"), onClick: () => void start() });
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      nowView?.stop();
      clearTimeout(feedTimer);
      clearTimeout(stateTimer);
      clearTimeout(runsTimer);
      clearTimeout(liveTimer);
      closeLive();
      return;
    }
    if (!running) return;
    openLive();
    bumpState();
    void tickRuns();
    if (tab === "now") void renderNow();
    bumpFeed();
  });

  void start();

  return {
    closeSheet,
    sheetOpen: () => !!sheetKind,
    back: () => {
      if (sheetKind) {
        closeSheet();
        return true;
      }
      if (tab === "chats" && room) {
        room = "";
        composer.hidden = true;
        render();
        opts.onSheet?.(false);
        return true;
      }
      return false;
    },
  };
}
