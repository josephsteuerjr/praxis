// Телефон и мини-апп Telegram — одно приложение на двоих. Разница только во
// входе (QR-пара или initData Telegram) и в хостинге; всё остальное — здесь:
// транспорт по ключу устройства, опрос с живыми событиями, комнаты, лента,
// композер, ход агента шагами, тема, отвязка.
//
// Транспорт: ключ уезжает в адрес только пока канал не поставил cookie
// `desk_key` (deskapp.auth_middleware), дальше — cookie; на 403 ключ
// возвращается в адрес. Область ключа устройства — КОНТРАКТ-B→A §4: ходы и
// прогоны телефон получает, когда канал их отдаст; до этого честно говорит.
import { applyTheme, el, q, readTheme, saveTheme, toast, type Theme } from "./dom";
import { esc, fmtDay, fmtDur, fmtTime, md } from "./text";
import { frameStripHTML, stepsHTML, type RunDetail } from "./steps";
import contract from "./contract.json";

export type Level = "ok" | "live" | "warn" | "error" | "off";

export interface AgentState {
  agent: string;
  owner?: string;
  level: Level;
  phrase: string;
  runner?: { alive: boolean; busy: boolean; run: string; since: number };
  brain?: { last_call_at: number | null };
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
}

export interface Run {
  id: string;
  kind: string;
  status: string;
  chat_id?: string | number | null;
  chat_title?: string;
  created_at?: string;
}

interface Turn {
  run_id: string;
  kind?: string;
  who?: string;
  in?: string;
  out?: string;
  ts?: string | number;
  delivery?: string;
  held?: string;
}

/** Чем кончился обмен на ключ. */
export type Redeem = "ok" | "spent" | "closed" | "broke" | "offline" | "foreign" | "none";

export interface PhoneAuth {
  /** Ключ в localStorage: у телефона и мини-аппа свои. */
  storageKey: string;
  /** Есть ли что обменять на ключ прямо сейчас (токен пары в адресе, initData). */
  hasCredential(): boolean;
  /** Обменять на ключ. */
  redeem(): Promise<{ result: Redeem; key?: string; agent?: string }>;
  /** Экран «нет доступа» по исходу. */
  pairScreen(pair: Redeem | null): { title: string; text: string; retry: boolean };
}

export interface PhoneOptions {
  auth: PhoneAuth;
  platform: "web" | "telegram";
  /** Куда ходить: пусто — свой origin. */
  base?: string;
  /** Имя агента, если канал не знает (дерево без снимка харнесса). */
  agentFallback?: string;
  /** Подпись внизу экрана «нет доступа» (имя продукта). */
  sign?: string;
  /** Кнопка «назад» платформы (Telegram BackButton): открытие/закрытие листа. */
  onSheet?(open: boolean): void;
  /** Тема платформы: telegram навязывает свою. */
  theme?: () => Theme;
}

export interface PhoneApp {
  closeSheet(): void;
  sheetOpen(): boolean;
}

const WINDOW_ROOM: string = contract.rooms.default;
const WINDOW_PREFIX: string = contract.rooms.pattern.replace(/^\^/, "").split("[")[0];
const isWindowRoom = (key: string) => key === WINDOW_ROOM || key === "pult" || key.startsWith(WINDOW_PREFIX);

/** 403: ни ключа, ни cookie не хватило. */
export class Denied extends Error {}
/** До компьютера не дозвонились. */
export class Offline extends Error {}
/** Компьютер ответил, но ошибкой. */
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
  rooms: '<path d="M4 6h12M4 10h12M4 14h8"/>',
  turns: '<path d="M3.5 10h3l2-5 3 10 2-5h3"/>',
  more: '<circle cx="5" cy="10" r="1.4"/><circle cx="10" cy="10" r="1.4"/><circle cx="15" cy="10" r="1.4"/>',
  send: '<path d="M10 15.5v-11M5.8 8.7 10 4.5l4.2 4.2"/>',
};

export function mountPhone(root: HTMLElement, opts: PhoneOptions): PhoneApp {
  applyTheme(opts.theme ? opts.theme() : readTheme());
  const base = (opts.base || "").replace(/\/+$/, "");

  // ---------------------------------------------------------------- DOM
  root.innerHTML = `
    <header id="top">
      <div class="top-main">
        <div class="top-name" id="top-name">Агент</div>
        <div class="top-state" id="top-state"><span class="state-dot"></span><span id="top-phrase">Подключение…</span></div>
      </div>
      <div class="top-btns">
        <button class="icon-btn" id="btn-rooms" type="button" aria-label="Чаты"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.rooms}</svg></button>
        <button class="icon-btn" id="btn-turns" type="button" aria-label="Ход агента"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.turns}</svg></button>
        <button class="icon-btn" id="btn-more" type="button" aria-label="Ещё"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.more}</svg></button>
      </div>
    </header>
    <section id="feed" class="feed"></section>
    <div id="notice" class="notice" hidden></div>
    <footer id="composer" class="composer">
      <div id="composer-target" class="composer-target"></div>
      <div class="composer-row">
        <textarea id="say" rows="1" placeholder="Написать агенту…" enterkeyhint="send"></textarea>
        <button id="send" class="send" type="button" aria-label="Отправить"><svg viewBox="0 0 20 20" aria-hidden="true">${ICON.send}</svg></button>
      </div>
    </footer>
    <div id="veil" class="veil" hidden></div>
    <div id="sheet" class="sheet" hidden><div class="sheet-grip"></div><div class="sheet-head" id="sheet-head"></div><div class="sheet-body" id="sheet-body"></div></div>
    <div id="pair" class="pair" hidden></div>
    <div id="toast" class="toast" hidden></div>`;

  const topName = q<HTMLElement>("#top-name", root);
  const topState = q<HTMLElement>("#top-state", root);
  const topPhrase = q<HTMLElement>("#top-phrase", root);
  const feed = q<HTMLElement>("#feed", root);
  const noticeBox = q<HTMLElement>("#notice", root);
  const say = q<HTMLTextAreaElement>("#say", root);
  const send = q<HTMLButtonElement>("#send", root);
  const target = q<HTMLElement>("#composer-target", root);
  const veil = q<HTMLElement>("#veil", root);
  const sheet = q<HTMLElement>("#sheet", root);
  const sheetHead = q<HTMLElement>("#sheet-head", root);
  const sheetBody = q<HTMLElement>("#sheet-body", root);
  const pairBox = q<HTMLElement>("#pair", root);
  const btnTurns = q<HTMLButtonElement>("#btn-turns", root);

  // ---------------------------------------------------------------- состояние
  let key = remembered(opts.auth.storageKey);
  let agent = opts.agentFallback || "Агент";
  let room = WINDOW_ROOM;
  let roomName = agent;
  let rooms: Room[] = [];
  let runs: Run[] = [];
  let state: AgentState | null = null;
  let running = false;
  let sendKey = true;
  let cookieWorks = true;
  const evOpen = new Set<string>();
  const evCache = new Map<string, RunDetail>();
  /** Ручки, которые канал телефону не отдаёт (403) — не биться в них каждый такт. */
  const closed = new Set<string>();

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

  /** Ручка из области окна: 403 запоминаем и больше не спрашиваем до перезапуска. */
  async function scoped<T>(path: string, mark: string): Promise<T | null> {
    if (closed.has(mark)) return null;
    try {
      return await api<T>(path);
    } catch (e) {
      if (e instanceof Denied) closed.add(mark);
      return null;
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

  const recent = (r: Run, minutes = 30) => {
    const at = new Date(r.created_at ?? "").getTime();
    return !isNaN(at) && Date.now() - at < minutes * 60_000;
  };

  const runLive = (r: Run) => r.status === "running" && (foreign() ? recent(r) : !!state?.runner?.busy);

  type Access = "ok" | "denied" | "offline";

  async function renderState(): Promise<Access> {
    try {
      const s = await api<AgentState>("/api/state");
      state = s;
      const named = s.anatomy === false && opts.agentFallback ? opts.agentFallback : s.agent;
      if (named) agent = named;
      const win = rooms.find((r) => r.key === WINDOW_ROOM);
      if (win) win.name = agent;
      if (room === WINDOW_ROOM) roomName = agent;
      paintTop();
      document.title = agent;
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
    const inWindow = room === WINDOW_ROOM;
    topName.textContent = inWindow ? agent : roomName;
    topName.classList.toggle("hand", inWindow);
    topName.classList.toggle("plain", !inWindow);
    if (!state) return;
    const f = foreign() ? foreignPhrase() : { level: state.level, phrase: state.phrase };
    topState.dataset.level = f.level;
    topPhrase.textContent = f.phrase;
    const liveHere = runs.some((r) => runLive(r) && roomKey(r) === room);
    btnTurns.querySelector(".live-mark")?.remove();
    if (liveHere) btnTurns.append(el("span", "live-mark"));
  }

  const roomKey = (r: Run) => {
    const k = String(r.chat_id ?? "");
    return k === "pult" ? WINDOW_ROOM : k;
  };

  // ---------------------------------------------------------------- комнаты
  async function loadRooms(): Promise<void> {
    let chats: Array<{ peer_id: string; title?: string; kind?: string; messages?: number }>;
    try {
      chats = await api("/api/chats");
    } catch {
      return; // не прочиталось — оставляем список как был
    }
    const got = await scoped<Run[]>("/api/runs?limit=120", "runs");
    if (got) runs = got;
    const next: Room[] = [{ key: WINDOW_ROOM, name: agent, kind: "window", live: false, count: 0 }];
    for (const c of chats) {
      const k = String(c.peer_id);
      if (k === WINDOW_ROOM || k === "pult") continue;
      const kind: Room["kind"] = c.kind === "window" || (c.kind !== "telegram" && isWindowRoom(k)) ? "window" : "telegram";
      next.push({ key: k, name: c.title || (kind === "window" ? "Новый чат" : "чат " + k), kind, live: false, count: c.messages || 0 });
    }
    for (const r of runs) {
      if (r.kind !== "chat_turn" || r.chat_id == null) continue;
      const k = roomKey(r);
      let found = next.find((x) => x.key === k);
      if (!found) {
        found = { key: k, name: r.chat_title || (isWindowRoom(k) ? "Новый чат" : "чат " + k), kind: isWindowRoom(k) ? "window" : "telegram", live: false, count: 0 };
        next.push(found);
      }
      if (runLive(r)) found.live = true;
    }
    rooms = next.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === "window" ? -1 : 1));
    const cur = rooms.find((r) => r.key === room);
    if (cur) roomName = cur.name;
    paintTop();
    if (sheetKind === "rooms") drawRooms();
  }

  function drawRooms() {
    sheetHead.innerHTML = `<span>Чаты</span><span class="n">${rooms.length}</span>`;
    const nodes: HTMLElement[] = [];
    let group = "";
    for (const r of rooms) {
      if (r.kind === "telegram" && group !== "telegram") {
        group = "telegram";
        nodes.push(el("div", "rooms-group", "Telegram"));
      }
      const b = el("button", "room");
      b.type = "button";
      b.setAttribute("aria-current", String(r.key === room));
      b.innerHTML = `<span class="dot ${r.live ? "live" : ""}"></span><span class="room-name${r.key === WINDOW_ROOM ? " hand" : ""}">${esc(r.name)}</span>${r.count && r.key !== WINDOW_ROOM ? `<span class="room-count">${r.count}</span>` : ""}`;
      b.addEventListener("click", () => {
        room = r.key;
        roomName = r.name;
        lastFeed = "";
        feed.dataset.ready = "";
        closeSheet();
        paintTop();
        syncComposer();
        bumpFeed();
      });
      nodes.push(b);
    }
    sheetBody.replaceChildren(...nodes);
  }

  // ---------------------------------------------------------------- лента
  let lastFeed = "";

  async function readArchive(peer: string): Promise<Msg[]> {
    const m = peer.match(/^(.+)__topic__(\d+)$/);
    if (!m) return api<Msg[]>("/api/chat/" + encodeURIComponent(peer) + "?n=120");
    const rows = await api<Msg[]>("/api/chat/" + encodeURIComponent(m[1]) + "?n=400");
    return rows.filter((r) => String(r.topic_id ?? "") === m[2]).slice(-120);
  }

  async function renderFeed(): Promise<boolean> {
    let rows: Msg[];
    try {
      rows = await readArchive(room);
    } catch (e) {
      if (!feed.dataset.ready) {
        const why = e instanceof Denied ? "Компьютер не пускает этот телефон." : e instanceof Broke ? "Компьютер ответил ошибкой." : "Телефон не дозвонился до компьютера.";
        feed.innerHTML = `<div class="empty">Лента не прочиталась. ${esc(why)} Попробую сам.</div>`;
      }
      return false;
    }
    const html: string[] = [];
    let day = "";
    const windowish = isWindowRoom(room);
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
      const topic = m.topic_title && !/__topic__/.test(room) ? ` <span class="badge">${esc(m.topic_title)}</span>` : "";
      const head = m.outgoing
        ? `<span class="who-hand">${esc(agent)}</span><span>${fmtTime(m.timestamp)}</span>`
        : `${showName ? `<b>${esc(name)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}</span>`;
      const media = m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "";
      html.push(`<div class="msg ${cls}"><div class="msg-head">${head}</div><div class="msg-body">${md(m.text || "")}${media}</div></div>`);
    }
    const next = html.join("");
    if (next === lastFeed && feed.dataset.ready) return false;
    lastFeed = next;
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 200;
    feed.innerHTML = next || `<div class="empty"><b>Здесь пока тихо</b>${windowish ? "Напиши первое сообщение внизу." : "Архива этой комнаты ещё нет."}</div>`;
    if (nearBottom || !feed.dataset.ready) feed.scrollTop = feed.scrollHeight;
    feed.dataset.ready = "1";
    return true;
  }

  // ---------------------------------------------------------------- композер
  function syncComposer() {
    target.textContent = isWindowRoom(room) ? "" : `в «${roomName}»`;
    say.placeholder = isWindowRoom(room) ? "Написать агенту…" : `Написать в «${roomName}»…`;
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
    if (!text) return;
    send.disabled = true;
    try {
      await post("/api/say", room === WINDOW_ROOM ? { text } : { text, chat: room });
      say.value = "";
      say.style.height = "auto";
      toast(isWindowRoom(room) ? "Ушло — агент прочитает в следующий ход" : `Ушло в «${roomName}»`);
      setTimeout(() => bumpFeed(), 1200);
    } catch (e) {
      notice(
        e instanceof Denied
          ? "Не ушло: компьютер не пускает этот телефон. Текст остался в поле."
          : e instanceof Offline
            ? "Не ушло: нет связи с компьютером. Текст остался в поле."
            : "Не ушло: компьютер ответил ошибкой. Текст остался в поле.",
      );
    }
    send.disabled = false;
  }

  function notice(text: string, action?: { label: string; onClick: () => void }) {
    noticeBox.hidden = false;
    noticeBox.innerHTML = esc(text);
    if (action) {
      const b = el("button", "", action.label);
      b.type = "button";
      b.addEventListener("click", () => {
        noticeBox.hidden = true;
        action.onClick();
      });
      noticeBox.append(document.createElement("br"), b);
    }
  }

  // ---------------------------------------------------------------- листы
  let sheetKind: "" | "rooms" | "turns" | "more" = "";

  function openSheet(kind: "rooms" | "turns" | "more") {
    sheetKind = kind;
    veil.hidden = false;
    sheet.hidden = false;
    requestAnimationFrame(() => {
      veil.classList.add("show");
      sheet.classList.add("show");
    });
    if (kind === "rooms") drawRooms();
    else if (kind === "turns") void drawTurns();
    else drawMore();
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
  q("#btn-rooms", root).addEventListener("click", () => (sheetKind === "rooms" ? closeSheet() : openSheet("rooms")));
  btnTurns.addEventListener("click", () => (sheetKind === "turns" ? closeSheet() : openSheet("turns")));
  q("#btn-more", root).addEventListener("click", () => (sheetKind === "more" ? closeSheet() : openSheet("more")));

  // свайп вниз по грипу закрывает лист
  let dragY = 0;
  sheet.addEventListener("touchstart", (e) => (dragY = e.touches[0].clientY), { passive: true });
  sheet.addEventListener("touchend", (e) => {
    const dy = e.changedTouches[0].clientY - dragY;
    if (dy > 70 && sheetBody.scrollTop <= 0) closeSheet();
  });

  // ---------------------------------------------------------------- ход
  async function runDetail(id: string, fresh = false): Promise<RunDetail | undefined> {
    if (!fresh && evCache.has(id)) return evCache.get(id);
    const d = await scoped<RunDetail>("/api/run/" + encodeURIComponent(id), "run");
    if (d) evCache.set(id, d);
    return d || undefined;
  }

  const clean = (s: string) =>
    s
      .replace(/^\s*\[[^\]]{0,200}\]\s*/, "")
      .replace(/^[^:\n]{1,40}:\s*/, "")
      .replace(/[*_`#>]+/g, "")
      .replace(/\s+/g, " ")
      .trim();

  async function drawTurns() {
    sheetHead.innerHTML = `<span>Ход · ${esc(roomName)}</span>`;
    sheetBody.innerHTML = '<div class="empty">читаю…</div>';
    const turns = await scoped<Turn[]>("/api/chat-turns/" + encodeURIComponent(room), "turns");
    if (sheetKind !== "turns") return;
    if (!turns) {
      sheetBody.innerHTML = `<div class="empty"><b>Ходы телефону пока не отдаются</b>Канал открывает их телефону с версии по контракту 0.3.3; в окне на компьютере они есть.</div>`;
      return;
    }
    const rows = turns.slice().reverse();
    const byRun = new Map(runs.map((r) => [r.id, r]));
    const live = runs.find((r) => runLive(r) && roomKey(r) === room);
    const liveDetail = live ? await runDetail(live.id, true) : undefined;
    let strip = liveDetail;
    if (!strip) {
      const last = rows.find((t) => t.run_id && t.run_id !== live?.id);
      if (last) strip = await runDetail(last.run_id);
    }
    if (sheetKind !== "turns") return;
    const liveHTML = live
      ? `<div class="turn-live"><div class="turn-live-head"><span class="dot live"></span><span>Ведёт ход</span><span class="t">${esc(live.created_at ? fmtDur((Date.now() - new Date(live.created_at).getTime()) / 1000) : "")}</span></div>
         <div class="ev-steps">${liveDetail ? stepsHTML(liveDetail, { limit: 8 }) : '<div class="muted">читаю шаги…</div>'}</div></div>`
      : "";
    const list = rows
      .filter((t) => t.run_id !== live?.id)
      .map((t) => {
        const run = byRun.get(t.run_id) ?? ({} as Run);
        const label = clean(t.out || "") || clean(t.in || "") || fmtTime(run.created_at);
        const at = run.created_at ? fmtTime(run.created_at) : t.ts ? fmtTime(new Date(parseFloat(String(t.ts)) * 1000).toISOString()) : "";
        const status = run.status || (t.delivery === "failed" ? "failed" : "done");
        const who = (t.who || "").trim();
        return `<div class="ev ${evOpen.has(t.run_id) ? "open" : ""}" data-ev="${esc(t.run_id)}">
          <div class="ev-head"><span class="ev-title">${esc(label.slice(0, 72))}${t.held === "unspoken" ? ' <span class="badge">промолчала</span>' : ""}</span><span class="ev-time">${at}</span><span class="dot ${runLive(run) ? "live" : status === "failed" ? "failed" : ""}"></span></div>
          ${who ? `<div class="ev-sub">${esc(who.slice(0, 40))}</div>` : ""}
          <div class="ev-steps" ${evOpen.has(t.run_id) ? "" : "hidden"}></div></div>`;
      })
      .join("");
    sheetBody.innerHTML = liveHTML + frameStripHTML(strip, live ? "Кадр сейчас" : "Кадр последнего хода") + (list || (live ? "" : '<div class="empty">Ходов ещё нет</div>'));
    for (const node of sheetBody.querySelectorAll<HTMLElement>(".ev")) {
      const id = node.dataset.ev!;
      node.querySelector(".ev-head")!.addEventListener("click", () => {
        if (evOpen.has(id)) evOpen.delete(id);
        else evOpen.add(id);
        void drawTurns();
      });
      if (evOpen.has(id)) {
        const box = node.querySelector<HTMLElement>(".ev-steps")!;
        box.innerHTML = '<div class="muted">читаю шаги…</div>';
        void runDetail(id).then((d) => {
          if (!box.isConnected) return;
          box.innerHTML = d ? stepsHTML(d) : '<div class="muted">шаги не прочитались</div>';
        });
      }
    }
  }

  // ---------------------------------------------------------------- ещё
  function drawMore() {
    sheetHead.innerHTML = `<span>Ещё</span>`;
    sheetBody.replaceChildren();
    if (!opts.theme) {
      const row = el("div", "more-row");
      row.append(el("h4", "", "Тема"));
      const choice = el("div", "choice");
      choice.setAttribute("role", "radiogroup");
      const current = readTheme();
      for (const [value, title, text] of [
        ["system", "Как на телефоне", "днём светлая, ночью тёмная"],
        ["light", "Светлая", "всегда"],
        ["dark", "Тёмная", "всегда"],
      ] as Array<[Theme, string, string]>) {
        const b = el("button", "choice-item");
        b.type = "button";
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(value === current));
        b.append(el("span", "choice-title", title), el("span", "choice-text", text));
        b.addEventListener("click", () => {
          saveTheme(value);
          for (const o of choice.querySelectorAll(".choice-item")) o.setAttribute("aria-checked", String(o === b));
        });
        choice.append(b);
      }
      row.append(choice);
      sheetBody.append(row);
    }
    const about = el("div", "more-row");
    about.append(el("h4", "", "Об этом телефоне"));
    const kind = opts.platform === "telegram" ? "мини-апп Telegram" : "приложение на экране «Домой»";
    about.append(el("p", "field-hint", `${agent} · ${kind} · ключ устройства ${key ? "есть" : "нет"} · канал ${base || location.host}. Переписка и ходы читаются живьём с компьютера, где живёт агент; на телефоне ничего не хранится, кроме ключа.`));
    sheetBody.append(about);
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
  const feedCeiling = () => (live ? 60000 : 30000);
  let feedDelay = FEED_MIN;
  let stateDelay = STATE_MIN;
  let feedTimer = 0;
  let stateTimer = 0;
  let feedBusy = false;
  let feedAgain = false;
  let stateBusy = false;

  function scheduleFeed() {
    clearTimeout(feedTimer);
    if (!running || document.hidden) return;
    feedTimer = window.setTimeout(() => void tickFeed(), feedDelay);
  }
  function scheduleState() {
    clearTimeout(stateTimer);
    if (!running || document.hidden) return;
    stateTimer = window.setTimeout(() => void tickState(), stateDelay);
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
  function bumpFeed() {
    feedDelay = FEED_MIN;
    clearTimeout(feedTimer);
    if (running && !document.hidden) void tickFeed();
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
      let kind = "";
      try {
        kind = String((JSON.parse(String(e.data)) as { t?: unknown })?.t ?? "");
      } catch {
        return;
      }
      if (kind === "run") {
        evCache.clear();
        bumpFeed();
        bumpState();
        void loadRooms();
        if (sheetKind === "turns") void drawTurns();
      } else if (kind === "health" || kind === "llm") {
        bumpState();
        if (kind === "llm" && sheetKind === "turns") void drawTurns();
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
    syncComposer();
    bumpFeed();
    void loadRooms();
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
      closeLive();
      return;
    }
    if (!running) return;
    openLive();
    bumpState();
    bumpFeed();
  });

  void start();

  return {
    closeSheet,
    sheetOpen: () => !!sheetKind,
  };
}
