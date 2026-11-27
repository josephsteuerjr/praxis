// Дорога к её каналу и общие мелочи окна. Тот же протокол, что у её телефона:
// ключ приезжает печеньем `desk_key`, ответы — JSON, отказ — словами в теле.
import { esc, fmtTime, fmtDay, fmtN, fmtK, fmtTs, fmtDur, md, humanError } from "../../ui-kit/text";
import { el, q, toast, card, button, receipt, toggle, failHTML, bindFail } from "../../ui-kit/dom";

export { esc, fmtTime, fmtDay, fmtN, fmtK, fmtTs, fmtDur, md, humanError, el, q, toast, card, button, receipt, toggle, failHTML, bindFail };

/** Отказ канала: статус — чтобы решить, что дальше, текст — чтобы показать человеку. */
export class ApiError extends Error {
  constructor(public status: number, message: string, public code = "") {
    super(message);
  }
}

export class Offline extends Error {
  constructor() {
    super("нет связи");
  }
}

type Request = RequestInit & { signal?: AbortSignal };

export async function call(path: string, init: Request = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...init });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new Offline();
  }
  if (!response.ok) {
    let text = "";
    try {
      text = (await response.text()).trim();
    } catch {
      text = "";
    }
    let detail = "";
    try {
      detail = String(JSON.parse(text).error || "");
    } catch {
      detail = text;          // канал отвечает отказом простым текстом
    }
    throw new ApiError(response.status, detail || `${path}: ${response.status}`);
  }
  return response;
}

export async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await call(path, { signal });
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, `${path}: не разобрал ответ`);
  }
}

export async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await call(path, {
    method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, `${path}: не разобрал ответ`);
  }
}

/** Запрос для её готовых блоков (ходы, управление Пракс): у них своя сигнатура — путь, тело, отмена. */
export function requestOf<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return body === undefined ? api<T>(path, signal) : post<T>(path, body, signal);
}

/** Адрес вложения из её дерева для проигрывателя. Браузер идёт за ним сам, печенье приложит. */
export const artifactUrl = (path: string, preview = true) =>
  "/api/media?path=" + encodeURIComponent(path) + (preview ? "&preview=1" : "");

// ------------------------------------------------------------------ её состояние

export interface AgentState {
  agent: string;
  owner?: string;
  level: "ok" | "live" | "warn" | "error" | "off" | string;
  phrase: string;
  action?: { label: string; target?: string } | null;
  runner?: { alive: boolean; busy: boolean; run?: string; since?: number; age_s?: number | null; ever?: boolean };
  brain?: { configured?: boolean; model?: string; last_call_at?: number | null; last_error?: string | null };
  next_wake?: string | null;
  alarms?: Array<{ kind?: string; text: string }>;
  mode?: { title?: string; text?: string; sandbox?: boolean };
  telegram?: { enabled?: boolean };
  relay?: { used?: boolean; authorized?: boolean };
}

let current: AgentState | null = null;
let lastGood = 0;
const listeners = new Set<() => void>();
let timer: number | undefined;

export function agentState(): AgentState | null {
  return current;
}

/** Сколько времени назад модель отвечала в последний раз, словами; пусто, если канал не знает. */
export function modelAgo(state: AgentState | null): string {
  const at = state?.brain?.last_call_at;
  if (!at) return "";
  const minutes = Math.max(0, Math.round((Date.now() - at * 1000) / 60_000));
  if (minutes < 1) return "только что";
  if (minutes === 1) return "минуту назад";
  if (minutes < 5) return `${minutes} минуты назад`;
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "час назад" : hours < 5 ? `${hours} часа назад` : `${hours} ч назад`;
}

export interface LiveRun { id: string; chat_title?: string; goal_head?: string; kind?: string }

/** Идёт ли сейчас ход — по записям ходов, а не по квитанции читателя. */
export function liveRun(): LiveRun | null {
  return live;
}

let live: LiveRun | null = null;

const RUN_KIND: Record<string, string> = { chat_turn: "Ответ в чате", wake: "Пробуждение", task_window: "Окно работы" };

/** Чем занят живой ход — её же слова: название хода, род хода или комната. */
export const liveLabel = (run: LiveRun | null | undefined): string =>
  (run && (run.goal_head || RUN_KIND[run.kind || ""] || run.chat_title)) || "";

/** Одна фраза о её состоянии — правило её телефона. Когда раннер не пишет квитанции
 *  читателя (так живёт она на сервере), канал говорит «Не запущен», хотя она ходит;
 *  тогда окно судит по тому, что видно: живой ход или последний ответ модели. */
export function statePhrase(state: AgentState | null, room = ""): string {
  if (!state) return "Подключение…";
  const doing = room || liveLabel(live);
  if (state.runner?.ever !== false) {
    if (state.runner?.busy) return `Ведёт ход${doing ? " · " + doing : ""}`;
    return state.phrase || "";
  }
  if (live) return `Ведёт ход${doing ? " · " + doing : ""}`;
  const at = state.brain?.last_call_at;
  if (!at) return "Вызовов модели ещё не было";
  const minutes = (Date.now() / 1000 - at) / 60;
  const age = minutes < 1 ? "только что" : minutes < 60 ? `${Math.round(minutes)} мин назад` : `${Math.round(minutes / 60)} ч назад`;
  return minutes < 15 ? `На связи · модель отвечала ${age}` : `Модель молчит · ${age}`;
}

let announce: (() => void) | undefined;
const stateReady = new Promise<void>((resolve) => { announce = resolve; });

/** Первое состояние Праксис получено: её блоки ходов можно запускать — имя агента уже известно. */
export function whenStateReady(): Promise<void> {
  return stateReady;
}

async function watchLive() {
  try {
    const rows = await api<Array<{ id: string; status: string; updated_at?: string; chat_title?: string; goal_head?: string; kind?: string }>>("/api/runs?limit=5&audience=all");
    const now = Date.now();
    live = rows.find((row) => {
      const at = Date.parse(row.updated_at || "");
      return row.status === "running" && Number.isFinite(at) && now - at >= -5000 && now - at < 10 * 60_000;
    }) ?? null;
  } catch { /* прежняя запись о живом ходе остаётся */ }
}

export function onState(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

async function tick() {
  try {
    current = await api<AgentState>("/api/state");
    lastGood = Date.now();
    announce?.();
    // без квитанции читателя живой ход виден только в записях; а название живого хода — оттуда же
    if (current.runner?.ever === false || current.runner?.busy) await watchLive();
  } catch (error) {
    if (!(error instanceof Offline)) throw error;
    if (lastGood && Date.now() - lastGood > 20_000 && current) {
      current = { ...current, level: "off", phrase: "Нет связи с Праксис" };
    } else if (!current) {
      current = { agent: "Праксис", level: "off", phrase: "Нет связи с Праксис" };
    }
  }
  for (const listener of listeners) listener();
}

export function startStateLoop(): void {
  const schedule = () => {
    clearTimeout(timer);
    if (!document.hidden) timer = window.setTimeout(() => void tick().then(schedule).catch(() => schedule()), 5000);
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { void tick().then(schedule).catch(schedule); } else clearTimeout(timer); });
  void tick().then(schedule).catch(schedule);
}

// ------------------------------------------------------------------ окно как таковое

/** Что умеет сама оболочка (Electron). В браузере этого нет — и окно честно говорит об этом. */
export interface Shell {
  minimize(): void;
  toggleMaximize(): void;
  isMaximized(): Promise<boolean>;
  reload(): Promise<void>;
  settings(): Promise<ShellSettings>;
  setSettings(patch: Partial<ShellSettings>): Promise<ShellSettings>;
  /** Масштаб страницы: `{ step: ±1 }` — на шаг крупнее или мельче, `{ reset: true }` — 100 %. Отвечает новым масштабом. */
  zoom(change: { step?: number; reset?: boolean }): Promise<number>;
  /** Прокси без VPN (туннель до машины с tinyproxy): состояние; `{ network }` — какая дорога до сервера, `{ proxyTask }` — держать ли задачу. */
  proxyState(): Promise<ProxyState>;
  setProxy(patch: { network?: ProxyNetwork; proxyTask?: boolean }): Promise<ProxyState | null>;
  onMaximized(listener: (maximized: boolean) => void): void;
  titleBar(colors: { color: string; symbolColor: string }): void;
}

export interface ShellSettings {
  autostart: boolean;
  startMaximized: boolean;
  zoom?: number;
  version: string;
}

/** auto — начать с той дороги, что сработала в прошлый раз; direct — только напрямую; proxy — только через прокси. */
export type ProxyNetwork = "auto" | "direct" | "proxy";

export interface ProxyState {
  network: ProxyNetwork;
  route: string;                 // «direct» или «proxy»: та, что сработала в прошлый раз
  proxyTask: boolean;            // владелец держит задачу «Praxis Proxy»
  configured: boolean;           // прокси настроен (proxy/config.json)
  proxy_url: string;
  alive: boolean;                // местный порт прокси слушается
  keeper: { state: string; at: string; why: string; attempt: number } | null;
}

export function shell(): Shell | undefined {
  return (window as Window & { praxisWindow?: Shell }).praxisWindow;
}

// ------------------------------------------------------------------ мелочи разметки

export type Dispose = () => void;

/** Сложить disposers вида в один: снимаются все, даже если один бросил. */
export function disposers(): { add(fn: Dispose | undefined): void; run(): void } {
  const list: Dispose[] = [];
  return {
    add(fn) { if (fn) list.push(fn); },
    run() { for (const fn of list.splice(0).reverse()) { try { fn(); } catch { /* вид уже ушёл */ } } },
  };
}

/** Панель с заголовком раздела и подзаголовком-пояснением. */
export function titled(kicker: string, title: string, ...children: Array<HTMLElement | string>): HTMLElement {
  const box = el("section", "titled");
  box.append(el("div", "top-kicker", kicker), el("h2", "section-title", title));
  box.append(...children);
  return box;
}

export function note(text: string, kind: "" | "err" | "ok" = ""): HTMLElement {
  const node = el("p", kind || "muted", text);
  node.setAttribute("role", "status");
  return node;
}

/** Кнопка-переключатель списка слева (комнаты, файлы, ходы). Список уходит, а открытое
 *  в нём остаётся на месте — чтение не сбивается. Положение помнится до следующего запуска. */
export function listToggle(key: string, name: string, columns: HTMLElement): HTMLButtonElement {
  const button = el("button", "edge-btn list-toggle");
  button.type = "button";
  button.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.75" y="3.75" width="14.5" height="12.5" rx="2.5" /><path d="M8 3.75v12.5" /></svg>`;
  const set = (on: boolean) => {
    columns.classList.toggle("no-list", !on);
    button.setAttribute("aria-pressed", String(on));
    button.title = `${on ? "Скрыть" : "Показать"} ${name}`;
    button.setAttribute("aria-label", button.title);
    try { localStorage.setItem(key, on ? "1" : "0"); } catch { /* без хранилища список просто не запомнится */ }
  };
  let on = true;
  try { on = localStorage.getItem(key) !== "0"; } catch { /* хранилища нет — список открыт */ }
  set(on);
  button.addEventListener("click", () => set(columns.classList.contains("no-list")));
  return button;
}

/** Кнопка-строка с иконкой для полки и меню. */
export function iconButton(label: string, path: string, onClick: () => void): HTMLButtonElement {
  const node = el("button", "icon-btn");
  node.type = "button";
  node.title = label;
  node.setAttribute("aria-label", label);
  node.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${path}</svg>`;
  node.addEventListener("click", onClick);
  return node;
}

export const relTime = (iso?: string | null) => {
  if (!iso) return "";
  const at = new Date(iso).getTime();
  if (Number.isNaN(at)) return "";
  const day = fmtDay(iso);
  const time = fmtTime(iso);
  const same = new Date(iso).toDateString() === new Date().toDateString();
  return same ? time : `${day}, ${time}`;
};

export const pct = (value: number | null | undefined) => (value == null ? "—" : Math.round(value) + "%");

export const bytes = (value: number | null | undefined) => {
  if (value == null) return "—";
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return (value / 1024).toFixed(value < 10 * 1024 ? 1 : 0).replace(".", ",") + " КБ";
  return (value / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
};

export const ms = (value: number | null | undefined) => {
  if (value == null) return "—";
  if (value < 1000) return Math.round(value) + " мс";
  return (value / 1000).toFixed(value < 10 * 1000 ? 1 : 0).replace(".", ",") + " с";
};

/** Строка «метка: значение» для таблиц-ключей; значение уходит в текст как есть. */
export function kv(label: string, value: unknown): HTMLElement {
  const row = el("div", "kv");
  row.append(el("span", "kv-label", label), el("span", "kv-value", typeof value === "string" ? value : value == null ? "—" : JSON.stringify(value)));
  return row;
}

/** Полоска доли (0…1): доля кэша, остаток лимита. */
export function bar(share: number | null | undefined, tone = ""): HTMLElement {
  const node = el("div", "bar" + (tone ? " " + tone : ""));
  const fill = el("div", "bar-fill");
  fill.style.width = (Math.max(0, Math.min(1, share ?? 0)) * 100).toFixed(1) + "%";
  node.append(fill);
  node.title = pct(share == null ? null : share * 100);
  return node;
}

/** Свернутый блок с ленивым содержимым: содержимое строится при первом раскрытии. */
export function fold(title: string, build: (body: HTMLElement) => void | Promise<void>, open = false): HTMLDetailsElement {
  const details = el("details", "fold");
  details.open = open;
  details.append(el("summary", "", title));
  const body = el("div", "fold-body");
  details.append(body);
  let built = false;
  details.addEventListener("toggle", () => {
    if (details.open && !built) {
      built = true;
      void Promise.resolve(build(body)).catch((error) => { body.textContent = humanError(error).text; });
    }
  });
  if (open) { built = true; void Promise.resolve(build(body)).catch((error) => { body.textContent = humanError(error).text; }); }
  return details;
}

/** Место, где вид рисует «читаю…», результат или отказ с кнопкой повтора. */
export function failNode(error: unknown, retry: () => void): HTMLElement {
  const box = el("div");
  box.innerHTML = failHTML(error, { retry: true });
  bindFail(box, retry);
  return box;
}

/** Текст отказа словами — её `humanError` уже знает все обороты канала. */
export function why(error: unknown): string {
  return error instanceof Offline ? "Нет связи с Праксис." : error instanceof ApiError ? error.message || "Канал отказал." : humanError(error).text;
}

export function code(text: string): HTMLElement {
  const node = el("pre", "mono code");
  node.textContent = text;
  return node;
}

/** Таблица: заголовки и строки; ячейки — текст, безопасно уходит в разметку. */
export function table(columns: string[], rows: unknown[][], rowClass?: (row: unknown[]) => string): HTMLElement {
  const wrap = el("div", "table-wrap");
  const node = el("table", "table");
  const head = el("thead"), line = el("tr");
  for (const column of columns) line.append(el("th", "", column));
  head.append(line);
  const body = el("tbody");
  for (const row of rows) {
    const cells = el("tr");
    if (rowClass) cells.className = rowClass(row);
    for (const value of row) cells.append(el("td", "", value == null ? "—" : String(value)));
    body.append(cells);
  }
  node.append(head, body);
  wrap.append(node);
  return wrap;
}

export const AGENT = "Праксис";

// ------------------------------------------------------------------ спящие разделы

/** Раздел живёт в памяти, пока владелец не на нём, и открывается мгновенно — содержимое уже
 *  на месте. Пока раздел скрыт, его опросы спят: таймеры не тикают, запросы ждут; когда он
 *  показывается, всё просыпается разом и данные обновляются тут же. Так окно не опрашивает
 *  её сервер за семью разделами сразу, а щелчок по полке ничего не подгружает. */
export interface Gate {
  /** Опрос: сработает через ms; если раздел спит — проснётся вместе с ним и сработает сразу. */
  every(fn: () => void, ms: number): void;
  /** Разовый таймер с тем же сном. */
  later(fn: () => void, ms: number): void;
  /** Её канал с тем же сном: запрос скрытого раздела уйдёт, когда раздел покажется. */
  api: typeof api;
  post: typeof post;
  request: typeof requestOf;
}

interface Sleeper { gate: Gate; awake: boolean; ticks: Array<() => void>; calls: Array<() => void> }
const sleepers = new WeakMap<HTMLElement, Sleeper>();

export function gate(root: HTMLElement): Gate {
  const known = sleepers.get(root);
  if (known) return known.gate;
  const me: Sleeper = { gate: null as unknown as Gate, awake: true, ticks: [], calls: [] };
  const untilShown = (run: () => void) => { if (me.awake) run(); else me.calls.push(run); };
  const after = (fn: () => void, ms: number) => {
    window.setTimeout(() => { if (me.awake) fn(); else me.ticks.push(fn); }, ms);
  };
  const deferred = <T>(run: () => Promise<T>): Promise<T> => new Promise<T>((resolve, reject) => {
    untilShown(() => { run().then(resolve, reject); });
  });
  me.gate = {
    every: (fn, ms) => after(fn, ms),
    later: (fn, ms) => after(fn, ms),
    api: (path, signal) => deferred(() => api(path, signal)),
    post: (path, body, signal) => deferred(() => post(path, body, signal)),
    request: (path, body, signal) => deferred(() => requestOf(path, body, signal)),
  };
  sleepers.set(root, me);
  return me.gate;
}

/** С раздела ушли: его опросы засыпают. */
export function lull(root: HTMLElement): void {
  const me = sleepers.get(root);
  if (me) me.awake = false;
}

/** Раздел показан: отложенные запросы уходят, спящие опросы срабатывают сразу. */
export function wake(root: HTMLElement): void {
  const me = sleepers.get(root);
  if (!me || me.awake) return;
  me.awake = true;
  for (const call of me.calls.splice(0)) call();
  for (const tick of me.ticks.splice(0)) tick();
}

// ------------------------------------------------------------------ переходы

export type View = "now" | "chats" | "tasks" | "wakes" | "instruments" | "files" | "system";

let navigator: (view: View, intent?: unknown) => void = () => undefined;

export function setNavigator(goTo: (view: View, intent?: unknown) => void): void {
  navigator = goTo;
}

/** Перейти в раздел; `intent` — с чем открыться (номер хода, комната) — раздел читает при входе. */
export function go(view: View, intent?: unknown): void {
  navigator(view, intent);
}
