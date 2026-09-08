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
import { esc, fmtDay, fmtDur, fmtTime, md } from "./text";
import { frameStripHTML, setResultFetcher, stepsHTML, type RunDetail } from "./steps";
import { activityHTML, selectActivity, updateActivity } from "./activity";
import contract from "./contract.json";
import { mountUsage, usageShell } from "./usage";

export type Level = "ok" | "live" | "warn" | "error" | "off";

export interface AgentState {
  agent: string;
  owner?: string;
  level: Level;
  phrase: string;
  runner?: { alive: boolean; busy: boolean; run: string; since: number };
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
  kind?: string;
  topic_id?: number | string;
  topic_title?: string;
  media?: string;
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
  created_at?: string;
}

interface Turn {
  run_id: string;
  kind?: string;
  who?: string;
  in?: string;
  out?: string;
  note?: string;
  ts?: string | number;
  delivery?: string;
  held?: string;
}

/** Чем кончился обмен на ключ. */
export type Redeem = "ok" | "spent" | "closed" | "broke" | "offline" | "foreign" | "none";

export interface PhoneAuth {
  storageKey: string;
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

type Tab = "now" | "chats" | "tasks" | "wakes";

const WINDOW_ROOM: string = contract.rooms.default;
const WINDOW_PREFIX: string = contract.rooms.pattern.replace(/^\^/, "").split("[")[0];
const isWindowRoom = (key: string) => key === WINDOW_ROOM || key === "pult" || key.startsWith(WINDOW_PREFIX);

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
  more: '<circle cx="5" cy="10" r="1.4"/><circle cx="10" cy="10" r="1.4"/><circle cx="15" cy="10" r="1.4"/>',
  send: '<path d="M10 15.5v-11M5.8 8.7 10 4.5l4.2 4.2"/>',
  back: '<path d="M12.5 4.5 7 10l5.5 5.5"/>',
};

const TABS: Array<[Tab, string]> = [
  ["now", "Сейчас"],
  ["chats", "Чаты"],
  ["tasks", "Задачи"],
  ["wakes", "Вейки"],
];

const RUN_KIND: Record<string, string> = {
  chat_turn: "сообщение",
  wake: "пробуждение",
  task_window: "окно задачи",
  moderation: "модерация",
};

const AGENDA_KIND: Record<string, string> = {
  wake: "будильник",
  window: "окно",
  message: "доставка",
  note: "записка",
  email: "письмо",
};

/** Подпись хода из слова агента или из входа: без скобок кадра и разметки. */
const clean = (s: string) =>
  s
    .replace(/^\s*…?\s*\[[^\]]{0,200}\]\s*/, "")
    .replace(/^[^:\n]{1,40}:\s*/, "")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();

/** Цель прогона годится в подпись, только если это не обрезок кадра. */
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
  const head = m ? (m[1].toLowerCase() === "wait" ? "Жду: " : m[1].toLowerCase() === "blocked" ? "Препятствие: " : "") : "";
  return body.trim() ? head + clean(body) : "";
};

export function mountPhone(root: HTMLElement, opts: PhoneOptions): PhoneApp {
  applyTheme(opts.theme ? opts.theme() : "system");
  const base = (opts.base || "").replace(/\/+$/, "");

  // ---------------------------------------------------------------- DOM
  root.innerHTML = `
    <header id="top">
      <div class="top-main">
        <div class="top-name" id="top-name">Агент</div>
        <div class="top-state" id="top-state"><span class="state-dot"></span><span id="top-phrase">Подключение…</span></div>
      </div>
      <div class="top-btns">
        <button class="icon-btn" id="btn-more" type="button" aria-label="Ещё"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.more}</svg></button>
      </div>
    </header>
    <section id="screen" class="screen"></section>
    <footer id="composer" class="composer" hidden>
      <div id="composer-target" class="composer-target"></div>
      <div class="composer-row">
        <textarea id="say" rows="1" placeholder="Написать…" enterkeyhint="send"></textarea>
        <button id="send" class="send" type="button" aria-label="Отправить"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.send}</svg></button>
      </div>
    </footer>
    <nav id="tabs" class="tabs" aria-label="Разделы">${TABS.map(
      ([id, label]) => `<button type="button" class="tab" data-tab="${id}"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON[id]}</svg><span>${label}</span></button>`,
    ).join("")}</nav>
    <div id="veil" class="veil" hidden></div>
    <div id="sheet" class="sheet" hidden><div class="sheet-grip"></div><div class="sheet-head" id="sheet-head"></div><div class="sheet-body" id="sheet-body"></div></div>
    <div id="pair" class="pair" hidden></div>
    <div id="toast" class="toast" hidden></div>`;

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
  let agent = opts.agentFallback || "Агент";
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
  const evCache = new Map<string, RunDetail>();
  /** run_id -> слово агента, заметка границы и собеседник (из chat-turns комнат). */
  const words = new Map<string, { out: string; note: string; who: string }>();
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
      throw new Offline("нет связи");
    }
    if (r.status === 403 && !sendKey && key) {
      sendKey = true;
      cookieWorks = false;
      try {
        r = await fetch(withKey(path), init);
      } catch {
        throw new Offline("нет связи");
      }
    }
    if (r.status === 403) throw new Denied("нет доступа");
    if (!r.ok) throw new Broke(path + ": " + r.status);
    if (sendKey && key && cookieWorks) sendKey = false;
    return r;
  }

  async function api<T = unknown>(path: string): Promise<T> {
    const r = await call(path);
    try {
      return (await r.json()) as T;
    } catch {
      throw new Broke(path + ": не разобрал ответ");
    }
  }

  async function post<T = unknown>(path: string, body: unknown): Promise<T> {
    const r = await call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    try {
      return (await r.json()) as T;
    } catch {
      throw new Broke(path + ": не разобрал ответ");
    }
  }

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
    `<div class="empty"><b>${esc(what)} телефону пока не отдаются</b>Канал откроет их телефону по контракту 0.3.3; в окне на компьютере они есть.</div>`;

  // ---------------------------------------------------------------- прогоны и живой ход
  const recent = (r: Run, minutes = 30) => {
    const at = new Date(r.updated_at || r.created_at || "").getTime();
    return !isNaN(at) && Date.now() - at < minutes * 60_000;
  };
  const runLive = (r: Run) => r.status === "running" && (foreign() ? recent(r) : !!state?.runner?.busy && state?.runner?.run === r.id);
  const roomKey = (r: Run) => {
    const k = String(r.chat_id ?? "");
    return k === "pult" ? WINDOW_ROOM : k;
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

  /** Слова агента для подписей: chat-turns тех комнат, где были недавние ходы. */
  async function loadWords(list: Run[]): Promise<void> {
    const keys = [...new Set(list.filter((r) => r.chat_id != null).map(roomKey))].slice(0, 6);
    await Promise.all(
      keys.map(async (k) => {
        const turns = await scoped<Turn[]>("/api/chat-turns/" + encodeURIComponent(k) + "?n=40", "turns");
        for (const t of turns || []) if (t.run_id) words.set(t.run_id, { out: t.out || "", note: t.note || "", who: t.who || "" });
      }),
    );
  }

  function runLabel(r: Run): string {
    const w = words.get(r.id);
    const text = clean(w?.out || "") || noteLabel(w?.note || "") || goalLabel(r.goal_head || "");
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
      <button type="button" class="ev-head" aria-expanded="${evOpen.has(r.id)}" aria-controls="run-${esc(r.id)}"><span class="ev-title">${esc(runLabel(r))}</span><span class="ev-time">${fmtDay(r.created_at) === "Сегодня" ? "" : fmtDay(r.created_at) + " "}${fmtTime(r.created_at)}</span><span class="dot ${dot}"></span><span class="ev-chevron" aria-hidden="true">›</span></button>
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
        steps.innerHTML = '<div class="muted">читаю шаги…</div>';
        void runDetail(id).then((d) => {
          if (!steps.isConnected) return;
          steps.innerHTML = d ? stepsHTML(d) : closed.has("run") ? '<div class="muted">шаги телефону пока не отдаются</div>' : '<div class="muted">шаги не прочитались</div>';
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
    if (live) return { level: "live", phrase: "Ведёт ход" + (live.chat_title ? ` · ${live.chat_title}` : "") };
    const at = state?.brain?.last_call_at;
    if (!at) return { level: "warn", phrase: "Вызовов модели ещё не было" };
    const min = (Date.now() / 1000 - at) / 60;
    const age = min < 1 ? "только что" : min < 60 ? `${Math.round(min)} мин назад` : `${Math.round(min / 60)} ч назад`;
    return min < 15 ? { level: "ok", phrase: `На связи · модель отвечала ${age}` } : { level: "warn", phrase: `Модель молчит · ${age}` };
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
        topPhrase.textContent = "Нет ключа";
        return "denied";
      }
      topPhrase.textContent = e instanceof Broke ? "Компьютер ответил ошибкой" : "Нет связи";
      return "offline";
    }
  }

  function paintTop() {
    topName.textContent = agent;
    topName.classList.add("hand");
    if (!state) return;
    const f = foreign() ? foreignPhrase() : { level: state.level, phrase: state.phrase };
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

  function render() {
    const my = ++gen;
    const guard = () => my === gen;
    if (tab === "now") void renderNow(guard);
    else if (tab === "chats") void (room ? renderRoom(guard) : renderChats(guard));
    else if (tab === "tasks") void renderTasks(guard);
    else void renderWakes(guard);
  }

  // ---------------------------------------------------------------- «Сейчас»
  let liveTimer = 0;
  let liveBusy = false;
  let focusedRun: Run | undefined;
  let nowGeneration = 0;

  async function renderNow(guard: () => boolean = () => tab === "now") {
    const mine = ++nowGeneration;
    const valid = () => mine === nowGeneration && guard() && tab === "now";
    if (!runs.length) await loadRuns();
    if (!valid()) return;
    const live = liveRun();
    const list = runs.filter((r) => r.kind !== "wake").slice(0, 30);
    focusedRun = selectActivity(focusedRun, live, list);
    const shown = focusedRun;
    const liveDetail = shown ? await runDetail(shown.id, true) || evCache.get(shown.id) : undefined;
    if (!valid()) return;
    const existing = screen.querySelector<HTMLElement>("#turn-live");
    if (shown && existing?.dataset.run === shown.id) {
      if (liveDetail) updateActivity(existing, liveDetail, live?.created_at ? fmtDur((Date.now() - new Date(live.created_at).getTime()) / 1000) : "");
      scheduleLive(!!live);
      return;
    }
    await loadWords(list.slice(0, 12));
    if (!valid()) return;
    let strip = liveDetail;
    if (!strip) {
      const last = list.find((r) => r.id !== live?.id && r.kind === "chat_turn");
      if (last) strip = await runDetail(last.id);
      if (!valid()) return;
    }
    const since = live?.created_at ? fmtDur((Date.now() - new Date(live.created_at).getTime()) / 1000) : "";
    const liveHTML = shown
      ? activityHTML(shown, liveDetail, live ? since : fmtTime(shown.created_at))
      : `<div class="now-idle"><span class="dot ${state && !foreign() && state.level === "error" ? "failed" : ""}"></span><span>${esc(state ? (foreign() ? "Нет текущих действий · " + foreignPhrase().phrase.replace(/^На связи · /, "") : state.phrase) : "Подключение…")}${state?.next_wake ? ` · пробуждение ${esc(fmtTime(state.next_wake) || state.next_wake)}` : ""}</span></div>`;
    const rest = list.filter((r) => r.id !== shown?.id);
    screen.innerHTML =
      liveHTML +
      usageShell(true) +
      frameStripHTML(strip, live ? "Контекст сейчас" : "Контекст последнего ответа") +
      `<div class="screen-title">Последние действия</div>` +
      (closed.has("runs") ? notGiven("Действия") : rest.map((r) => runRow(r, { showRoom: true })).join("") || '<div class="empty">Здесь появятся действия и результаты</div>');
    bindRuns(screen, () => void renderNow());
    mountUsage(screen, api);
    scheduleLive(!!live);
  }

  function scheduleLive(on: boolean) {
    clearTimeout(liveTimer);
    if (!on) return;
    liveTimer = window.setTimeout(() => void refreshLive(), 1500);
  }

  async function refreshLive() {
    if (tab !== "now" || document.hidden) return;
    if (liveBusy) { scheduleLive(true); return; }
    const live = liveRun();
    const card = screen.querySelector<HTMLElement>("#turn-live");
    if (!live || !card || card.dataset.run !== live.id) {
      if (card || live) void renderNow();
      return;
    }
    liveBusy = true;
    try {
      const d = await runDetail(live.id, true);
      if (!card.isConnected || liveRun()?.id !== live.id) return;
      const steps = card.querySelector<HTMLElement>("#turn-live-steps");
      const t = card.querySelector<HTMLElement>("#turn-live-t");
      if (t && live.created_at) t.textContent = fmtDur((Date.now() - new Date(live.created_at).getTime()) / 1000);
      if (steps && d) updateActivity(card, d, live.created_at ? fmtDur((Date.now() - new Date(live.created_at).getTime()) / 1000) : "");
    } catch {
      // После ошибки связи продолжаем перечитывать текущие действия.
    } finally {
      liveBusy = false;
    }
    scheduleLive(true);
  }

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
      if (k === WINDOW_ROOM || k === "pult") continue;
      const kind: Room["kind"] = c.kind === "window" || (c.kind !== "telegram" && isWindowRoom(k)) ? "window" : "telegram";
      next.push({ key: k, name: c.title || (kind === "window" ? "Новый чат" : "чат " + k), kind, live: false, count: c.messages || 0, at: Number(c.mtime_ns) / 1e6 || 0 });
    }
    for (const r of runs) {
      if (r.kind !== "chat_turn" || r.chat_id == null) continue;
      const k = roomKey(r);
      let found = next.find((x) => x.key === k);
      if (!found && isWindowRoom(k)) continue;
      if (!found) {
        found = { key: k, name: r.chat_title || (isWindowRoom(k) ? "Новый чат" : "чат " + k), kind: isWindowRoom(k) ? "window" : "telegram", live: false, count: 0, at: 0 };
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
      const when = r.at ? (fmtDay(new Date(r.at).toISOString()) === "Сегодня" ? fmtTime(new Date(r.at).toISOString()) : fmtDay(new Date(r.at).toISOString())) : "";
      b.innerHTML = `<span class="dot ${r.live ? "live" : ""}"></span><span class="room-name${r.key === WINDOW_ROOM ? " hand" : ""}">${esc(r.name)}</span><span class="room-count">${esc(when)}</span>`;
      b.addEventListener("click", () => openRoom(r.key, r.name));
      nodes.push(b);
    }
    screen.replaceChildren(el("div", "screen-title", "Чаты"), ...nodes);
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
    const k = key === "pult" || key === "window" ? WINDOW_ROOM : key;
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
    const m = peer.match(/^(.+)__topic__(\d+)$/);
    if (!m) return api<Msg[]>("/api/chat/" + encodeURIComponent(peer) + "?n=120");
    const rows = await api<Msg[]>("/api/chat/" + encodeURIComponent(m[1]) + "?n=400");
    return rows.filter((r) => String(r.topic_id ?? "") === m[2]).slice(-120);
  }

  async function renderRoom(guard: () => boolean = () => tab === "chats" && !!room) {
    if (!screen.querySelector("#feed")) {
      screen.innerHTML = `<div class="back-row"><button type="button" class="back" id="back-rooms"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.back}</svg>Чаты</button><span class="back-title">${esc(roomName)}</span></div><div id="feed" class="feed-in"></div>`;
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
        const why = e instanceof Denied ? "Компьютер не пускает этот телефон." : e instanceof Broke ? "Компьютер ответил ошибкой." : "Телефон не дозвонился до компьютера.";
        feed.innerHTML = `<div class="empty">Лента не прочиталась. ${esc(why)} Попробую сам.</div>`;
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
      const media = m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "";
      html.push(`<div class="msg ${cls}" data-at="${esc(m.timestamp || "")}"><div class="msg-head">${head}</div><div class="msg-body">${md(m.text || "")}${media}</div></div>`);
    }
    const next = html.join("");
    if (next === lastFeed && feedReady) return false;
    lastFeed = next;
    const nearBottom = screen.scrollHeight - screen.scrollTop - screen.clientHeight < 200;
    feed.innerHTML = next || `<div class="empty"><b>Здесь пока тихо</b>${windowish ? "Напиши первое сообщение внизу." : "Архива этой комнаты ещё нет."}</div>`;
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
    target.textContent = isWindowRoom(room) ? "" : `в «${roomName}»`;
    say.placeholder = isWindowRoom(room) ? `Написать ${agent}…` : `Написать в «${roomName}»…`;
  }
  say.addEventListener("input", () => {
    say.style.height = "auto";
    say.style.height = Math.min(say.scrollHeight, 140) + "px";
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
    if (!text || !room) return;
    send.disabled = true;
    try {
      await post("/api/say", room === WINDOW_ROOM ? { text } : { text, chat: room });
      say.value = "";
      say.style.height = "auto";
      toast(isWindowRoom(room) ? "Ушло — агент прочитает в следующий ход" : `Ушло в «${roomName}»`);
      setTimeout(() => bumpFeed(), 1200);
    } catch (e) {
      toast(
        e instanceof Denied
          ? "Не ушло: компьютер не пускает этот телефон. Текст остался в поле."
          : e instanceof Offline
            ? "Не ушло: нет связи с компьютером. Текст остался в поле."
            : "Не ушло: компьютер ответил ошибкой. Текст остался в поле.",
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
    agents?: Array<{ id: string; role?: string; status?: string; error?: string; result_head?: string }>;
  }

  async function renderTasks(guard: () => boolean = () => tab === "tasks") {
    screen.innerHTML = '<div class="empty">читаю…</div>';
    const [agenda, forge, board] = await Promise.all([
      scoped<Agenda>("/api/agenda", "agenda"),
      scoped<ForgeTask[]>("/api/forge", "forge"),
      scoped<{ board?: string }>("/api/board", "board"),
    ]);
    if (!guard()) return;
    if (!agenda && !forge && !board && (closed.has("agenda") || closed.has("board"))) {
      screen.innerHTML = `<div class="screen-title">Задачи</div>` + notGiven("Задачи");
      return;
    }
    const agendaHTML = agenda
      ? `<div class="screen-title">Намеченное <span class="n">активных ${agenda.active.length} из ${agenda.total}</span></div>` +
        (agenda.active
          .slice(0, 40)
          .map(
            (t) => `<div class="task-row">
              <div class="task-head"><span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>${t.when ? ` <b class="mono">${esc(t.when)}</b>` : ""}${t.recur ? ` <span class="muted mono">повтор: ${esc(t.recur)}</span>` : ""}<span class="muted"> · ${esc(t.status || "")}</span></div>
              ${t.target ? `<div class="muted">→ ${esc(String(t.target))}</div>` : ""}
              <div class="task-goal">${esc((t.goal || "").slice(0, 220))}</div>
            </div>`,
          )
          .join("") || '<div class="empty">Пока ничего не намечено.</div>')
      : `<div class="screen-title">Намеченное</div><div class="empty">не прочиталось</div>`;
    const forgeHTML = forge
      ? `<div class="screen-title">Субагенты <span class="n">${forge.length}</span></div>` +
        (forge
          .map((t) => {
            const live = t.status === "active" || (t.agents || []).some((a) => a.status === "running");
            const units = (t.agents || [])
              .map((a) => `<div class="task-unit"><span class="mono">${esc(a.id)}</span> <span class="badge">${esc(a.role || "?")}</span> <b class="${a.status === "error" || a.status === "failed" ? "err" : ""}">${esc(a.status || "")}</b>${a.error ? `<div class="err">${esc(a.error.slice(0, 120))}</div>` : ""}${a.result_head ? `<div class="muted">${esc(a.result_head.slice(0, 160))}</div>` : ""}</div>`)
              .join("");
            return `<details class="fold"><summary><span class="dot ${live ? "live" : ""}"></span> <b>${esc(t.id)}</b> · ${esc(t.status || "")} · ${(t.agents || []).length} — ${esc((t.goal || "").slice(0, 80))}</summary><div class="fold-body">${units || '<span class="muted">юнитов нет</span>'}</div></details>`;
          })
          .join("") || '<div class="empty">Субагентов сейчас нет.</div>')
      : "";
    const boardHTML = board ? `<div class="screen-title">Доска</div><div class="card md">${md(board.board || "Доска пуста.")}</div>` : "";
    screen.innerHTML = agendaHTML + forgeHTML + boardHTML;
  }

  // ---------------------------------------------------------------- «Вейки»
  async function renderWakes(guard: () => boolean = () => tab === "wakes") {
    screen.innerHTML = '<div class="empty">читаю…</div>';
    const got = await scoped<Run[]>("/api/runs?kind=wake&limit=40", "runs");
    if (!guard()) return;
    const next = state?.next_wake ? `<div class="now-idle"><span class="dot"></span><span>Следующее пробуждение: ${esc(fmtTime(state.next_wake) || state.next_wake)}</span></div>` : "";
    if (!got) {
      screen.innerHTML = `<div class="screen-title">Вейки</div>` + next + (closed.has("runs") ? notGiven("Пробуждения") : '<div class="empty">не прочиталось</div>');
      return;
    }
    screen.innerHTML =
      `<div class="screen-title">Пробуждения <span class="n">${got.length}</span></div>` +
      next +
      (got.map((r) => runRow(r)).join("") || '<div class="empty">Пробуждений ещё не было.</div>');
    bindRuns(screen, () => void renderWakes());
  }

  // ---------------------------------------------------------------- «Ещё»
  let sheetKind: "" | "more" = "";
  function openSheet() {
    sheetKind = "more";
    veil.hidden = false;
    sheet.hidden = false;
    requestAnimationFrame(() => {
      veil.classList.add("show");
      sheet.classList.add("show");
    });
    drawMore();
    opts.onSheet?.(true);
  }
  function closeSheet() {
    if (!sheetKind) return;
    sheetKind = "";
    veil.classList.remove("show");
    sheet.classList.remove("show");
    setTimeout(() => {
      if (!sheetKind) {
        veil.hidden = true;
        sheet.hidden = true;
      }
    }, 220);
    opts.onSheet?.(false);
  }
  veil.addEventListener("click", closeSheet);
  q("#btn-more", root).addEventListener("click", () => (sheetKind ? closeSheet() : openSheet()));
  let dragY = 0;
  sheet.addEventListener("touchstart", (e) => (dragY = e.touches[0].clientY), { passive: true });
  sheet.addEventListener("touchend", (e) => {
    if (e.changedTouches[0].clientY - dragY > 70 && sheetBody.scrollTop <= 0) closeSheet();
  });

  function drawMore() {
    sheetHead.innerHTML = `<span>Ещё</span>`;
    sheetBody.replaceChildren();
    const about = el("div", "more-row");
    about.append(el("h4", "", "Об этом телефоне"));
    const kind = opts.platform === "telegram" ? "мини-апп Telegram" : "приложение на экране «Домой»";
    about.append(el("p", "field-hint", `${agent} · ${kind} · ключ устройства ${key ? "есть" : "нет"} · канал ${base || location.host}. Тема — как в системе. Переписка, ходы и задачи читаются живьём с компьютера, где живёт агент; на телефоне ничего не хранится, кроме ключа.`));
    sheetBody.append(about);
    // Принудительное обновление руками — для iPhone, где WebView держит старую
    // сборку: та же сверка, что идёт сама при возврате на экран.
    const upd = el("div", "more-row");
    upd.append(el("h4", "", "Оболочка"));
    const updBtn = el("button", "btn btn-quiet", "Обновить оболочку");
    updBtn.type = "button";
    const updNote = el("p", "field-hint", `Сборка ${(document.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/index-"]')?.getAttribute("src") || "").split("/").pop()?.replace(/^index-|\.js$/g, "") || "?"}. Обновляется сама при открытии и возврате на экран; кнопка — если не дождался сам.`);
    updBtn.addEventListener("click", async () => {
      updBtn.disabled = true;
      updNote.textContent = "Сверяю с сервером…";
      const went = window.heleneCheckShell ? await window.heleneCheckShell() : false;
      if (!went) {
        updNote.textContent = "Это уже свежая сборка.";
        updBtn.disabled = false;
      }
    });
    upd.append(updBtn, updNote);
    sheetBody.append(upd);
    const unpair = el("div", "more-row");
    unpair.append(el("h4", "", "Отвязать"));
    const b = el("button", "btn btn-danger", "Забыть ключ на этом телефоне");
    b.type = "button";
    b.addEventListener("click", () => {
      remember(opts.auth.storageKey, "");
      key = "";
      sendKey = true;
      closeSheet();
      accessLost();
    });
    unpair.append(b, el("p", "field-hint", "Компьютер этот телефон помнит до отвязки в Настройках → Телефон; здесь только стирается ключ."));
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
    // Без живых событий (канал их телефону не отдаёт) прогоны перечитываются
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

  let live: EventSource | null = null;
  let liveTries = 0;
  function openLive() {
    if (live || !running || document.hidden || liveTries >= 1) return;
    liveTries += 1;
    try {
      live = new EventSource(withKey("/events"));
    } catch {
      live = null;
      return;
    }
    live.addEventListener("open", () => (liveTries = 0));
    live.addEventListener("message", (e) => {
      let ev: { t?: unknown; run_id?: unknown } = {};
      try {
        ev = JSON.parse(String(e.data));
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
    live.addEventListener("error", () => {
      if (live && live.readyState === EventSource.CLOSED) live = null;
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
    running = false;
    clearTimeout(feedTimer);
    clearTimeout(stateTimer);
    clearTimeout(runsTimer);
    clearTimeout(liveTimer);
    closeLive();
    const s = opts.auth.pairScreen(key ? "closed" : "none");
    showPair(s.title, s.text, { label: key ? "Проверить снова" : "Попробовать снова", onClick: () => void (key ? recheck() : start()) });
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
    let pair: Redeem | null = null;
    if (opts.auth.hasCredential()) {
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
      showPair(s.title, s.text, { label: "Попробовать снова", onClick: () => void start() });
      return;
    }
    if (pair && pair !== "ok") {
      const s = opts.auth.pairScreen(pair);
      showPair(s.title, s.text, s.retry ? { label: "Попробовать снова", onClick: () => void start() } : undefined);
      return;
    }
    if (key) {
      accessLost();
      return;
    }
    const s = opts.auth.pairScreen(null);
    showPair(s.title, s.text, { label: "Попробовать снова", onClick: () => void start() });
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
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
