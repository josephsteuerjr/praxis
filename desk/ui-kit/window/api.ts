// Связь с харнессом: канал frame.desk.v1 (WebSocket: запросы + живые события)
// с HTTP-фолбэком, и мост к нативной оболочке (Tauri) там, где она есть.

export interface Cfg {
  base: string;
  key: string;
  agent?: string;
  /** Имя продукта для подписи и заголовка (хостинг Пульта Праксис — «Praxis»). */
  product?: string;
  /** Пульт распакован, но адрес сервера ещё не вписан: окно спрашивает его само. */
  needs_remote?: boolean;
}

declare global {
  interface Window {
    PULT_CONFIG_OVERRIDE?: Cfg;
    PULT_CONFIG?: Cfg | null;
  }
}

// Приоритет: локальный конфиг оболочки (helene.json → init-скрипт) выше
// вшитого сборкой config.js; в вебе — same-origin.
//
// ⚠ 17.09, ЖИВАЯ ПРОБА. Одного init-скрипта оказалось мало. На чистой установке 0.7.0
// оболочка честно писала в журнал «ключ канала для окна задан», а окно всё равно
// получало 403 на КАЖДЫЙ запрос и вечно показывало «не на связи» — при живом харнессе,
// который тем же ключом отвечал 200 из командной строки. То есть `PULT_CONFIG_OVERRIDE`
// до страницы не доезжал: окно грузится своим протоколом (`http://helene.localhost`), и
// скрипт инициализации к моменту чтения конфига здесь не виден.
//
// Второй путь для ключа — адрес окна. Он не новый и не обход: тем же `?key=` оболочка
// стучится в занятый порт (`home_probe`), и так же ключ приезжает на телефон. Адресной
// строки у окна нет (`decorations(false)`), в журнал канала ключ не попадает — он там
// маскируется. Порядок: что дал init-скрипт, то и главное; адрес — запасная нога.
function keyFromUrl(): string {
  try {
    return new URLSearchParams(location.search).get("key") || "";
  } catch {
    return "";
  }
}

const shellCfg = window.PULT_CONFIG_OVERRIDE || window.PULT_CONFIG;
export const cfg: Cfg = shellCfg
  ? { ...shellCfg, key: shellCfg.key || keyFromUrl() }
  : { base: "", key: keyFromUrl() };
export const inTauri = "__TAURI_INTERNALS__" in window;

function url(path: string): string {
  const full = (cfg.base || "") + path;
  if (!cfg.key) return full;
  return full + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(cfg.key);
}

/**
 * Отказ трубы вместе с КОДОМ, а не только словами.
 *
 * Без него окно не могло отличить «файл изменился под тобой» (409, code
 * `conflict`) от любой другой неудачи записи и молча затирало правку агента —
 * серверная защита стояла, а читателя у неё не было.
 */
/**
 * Адрес вложения для тега `<audio>`/`<img>`: тот же канал и тот же ключ.
 *
 * Отдельная функция, а не `url()`: та частная и добавляет ключ к ЛЮБОМУ пути, а
 * здесь важно, что путь вложения — не наш, он приехал строкой ленты, и его
 * надо экранировать как параметр, а не приклеивать к адресу.
 */
export function mediaURL(rel: string): string {
  const base = (cfg.base || "") + "/api/media?path=" + encodeURIComponent(rel);
  return cfg.key ? base + "&key=" + encodeURIComponent(cfg.key) : base;
}

export class ApiError extends Error {
  status?: number;
  code?: string;
  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export type LiveEvent = { t: string; [k: string]: unknown };
type Waiter = { res: (v: unknown) => void; rej: (e: Error) => void };

let sock: WebSocket | null = null;
let ready = false;
let seq = 1;
const waiting = new Map<number, Waiter>();
const eventHandlers = new Set<(ev: LiveEvent) => void>();
const connHandlers = new Set<(ok: boolean) => void>();

export function onEvent(fn: (ev: LiveEvent) => void) {
  eventHandlers.add(fn);
}

export function onConnection(fn: (ok: boolean) => void) {
  connHandlers.add(fn);
}

function tunnelURL(): string {
  const base = cfg.base || location.origin;
  const ws = base.replace(/^http/, "ws") + "/tunnel";
  return cfg.key ? ws + "?key=" + encodeURIComponent(cfg.key) : ws;
}

export function connect() {
  let s: WebSocket;
  try {
    s = new WebSocket(tunnelURL());
  } catch {
    for (const fn of connHandlers) fn(false);
    setTimeout(connect, 4000);
    return;
  }
  sock = s;
  s.onopen = () => {
    ready = true;
  };
  s.onmessage = (m) => {
    let d: { hello?: string; event?: LiveEvent; id?: number; status?: number; body?: unknown; error?: string; code?: string };
    try {
      d = JSON.parse(m.data);
    } catch {
      return;
    }
    if (d.hello) {
      for (const fn of connHandlers) fn(true);
      return;
    }
    if (d.event) {
      for (const fn of eventHandlers) fn(d.event);
      return;
    }
    const w = waiting.get(d.id ?? -1);
    if (w) {
      waiting.delete(d.id ?? -1);
      if (d.status && d.status < 400) w.res(d.body);
      // Ответ без поля status давал владельцу литеральное « (undefined)».
      // Код отказа несём отдельным полем: по нему окно отличает конфликт
      // правок от любой другой неудачи.
      else w.rej(new ApiError((d.error || "канал ответил отказом") + (d.status ? " (" + d.status + ")" : ""), d.status, d.code));
    }
  };
  s.onclose = s.onerror = () => {
    if (sock !== s) return;
    sock = null;
    ready = false;
    for (const fn of connHandlers) fn(false);
    for (const w of waiting.values()) w.rej(new Error("связь оборвалась"));
    waiting.clear();
    setTimeout(connect, 4000);
  };
}

function tunnelCall<T>(path: string, method: string, body: unknown): Promise<T> {
  return new Promise<T>((res, rej) => {
    const id = seq++;
    waiting.set(id, { res: res as (v: unknown) => void, rej });
    try {
      sock!.send(JSON.stringify({ id, path, method, body }));
    } catch (e) {
      waiting.delete(id);
      rej(e as Error);
      return;
    }
    setTimeout(() => {
      if (waiting.delete(id)) rej(new Error(path + ": нет ответа"));
    }, 25000);
  });
}

export async function api<T = any>(path: string): Promise<T> {
  if (ready && sock) {
    try {
      return await tunnelCall<T>(path, "GET", null);
    } catch (e) {
      if (ready) throw e;
    }
  }
  const r = await fetch(url(path));
  if (!r.ok) throw new Error(path + ": " + r.status);
  return r.json();
}

export async function post<T = any>(path: string, body: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

/** DELETE — комнаты (КОНТРАКТ-B→A §1). По каналу тем же конвертом, что POST. */
export async function del<T = any>(path: string): Promise<T> {
  return request<T>("DELETE", path, null);
}

async function request<T>(method: string, path: string, body: unknown): Promise<T> {
  if (ready && sock) return tunnelCall<T>(path, method, body);
  const r = await fetch(url(path), {
    method,
    headers: body == null ? {} : { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  // HTTP-половина кода не несёт — его заменяет статус: 409 у канала означает
  // ровно конфликт правок (deskapp.api_md_write → HTTPConflict).
  if (!r.ok) throw new ApiError(await r.text(), r.status, r.status === 409 ? "conflict" : undefined);
  return r.json();
}

/** Команда оболочки; вне Tauri — честная ошибка, а не тишина. */
export async function shell<T = unknown>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!inTauri) throw new Error("доступно только в приложении Hélène");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}
