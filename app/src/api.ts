// Связь с харнессом: канал frame.desk.v1 (WebSocket: запросы + живые события)
// с HTTP-фолбэком, и мост к нативной оболочке (Tauri) там, где она есть.

export interface Cfg {
  base: string;
  key: string;
  agent?: string;
  /** Имя продукта для подписи и заголовка (хостинг Пульта Праксис — «Praxis»). */
  product?: string;
}

declare global {
  interface Window {
    PULT_CONFIG_OVERRIDE?: Cfg;
    PULT_CONFIG?: Cfg | null;
  }
}

// Приоритет: локальный конфиг оболочки (helene.json → init-скрипт) выше
// вшитого сборкой config.js; в вебе — same-origin.
export const cfg: Cfg = window.PULT_CONFIG_OVERRIDE || window.PULT_CONFIG || { base: "", key: "" };
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
