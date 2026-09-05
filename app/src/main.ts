// Hélène — основная программа. Полка слева (разделы и разговоры), разговор в
// центре, ходы справа; состояние агента одной фразой в шапке; настройки
// экраном, а не файлом.
import "./styles/app.css";
import { api, cfg, connect, inTauri, onConnection, onEvent, post, shell } from "./api";
import { bindFail, esc, failHTML, fmtN, fmtTs, humanError, q, toast } from "./lib";
import { PRODUCT_NAME, S, WINDOW_ROOM, runIsLive, type AgentState, type Pending, type Room, type Run, type View } from "./state";
import * as talk from "./views/talk";
import * as plans from "./views/plans";
import * as frame from "./views/frame";
import * as files from "./views/files";
import * as journal from "./views/journal";
import * as anatomy from "./views/anatomy";
import * as settings from "./views/settings";

const view = q<HTMLElement>("#view");
const app = q<HTMLElement>("#app");
const railNav = q<HTMLElement>("#rail-nav");
const railBottom = q<HTMLElement>("#rail-bottom");
const roomsBox = q<HTMLElement>("#rooms");
const headKicker = q<HTMLElement>("#head-kicker");
const headTitle = q<HTMLElement>("#head-title");
const statePill = q<HTMLElement>("#state");
const stateText = q<HTMLElement>("#state-text");
const stateAction = q<HTMLButtonElement>("#state-action");
const pulseBox = q<HTMLElement>("#pulse");
const alarmBox = q<HTMLElement>("#alarm");
const composer = q<HTMLElement>("#composer");
const composerTarget = q<HTMLElement>("#composer-target");
const say = q<HTMLTextAreaElement>("#say");
const send = q<HTMLButtonElement>("#send");
const panel = q<HTMLElement>("#panel");

// ---------------------------------------------------------------- тема

type Theme = "system" | "light" | "dark";
function applyTheme() {
  // По умолчанию как в Windows: светлая днём не слепит ночью. Светлый вариант —
  // самый светлый из набора (слово владельца 02.09).
  let mode: Theme = "system";
  try {
    const raw = localStorage.getItem("frame.theme");
    if (raw === "system" || raw === "dark" || raw === "light") mode = raw;
  } catch {
    // без хранилища — светлая
  }
  if (mode === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
}
applyTheme();
addEventListener("frame-theme", applyTheme);

// ---------------------------------------------------------------- окно

if (inTauri) {
  document.documentElement.classList.add("native");
  import("@tauri-apps/api/window").then(({ getCurrentWindow }) => {
    const w = getCurrentWindow();
    for (const b of document.querySelectorAll<HTMLButtonElement>(".win")) {
      const action = b.dataset.win;
      b.addEventListener("click", () => {
        if (action === "minimize") void w.minimize();
        else if (action === "maximize") void w.toggleMaximize();
        else void w.close();
      });
    }
  });
}

// ---------------------------------------------------------------- разделы

const ICONS: Record<View, string> = {
  talk: '<path d="M4 5.5h12v8H8l-4 3z"/>',
  plans: '<rect x="3.5" y="4" width="13" height="12" rx="2"/><path d="M6.5 2.8v2.5M13.5 2.8v2.5M6.5 8h7M6.5 11h4"/>',
  frame: '<path d="M3.5 6.5V4.8c0-.7.6-1.3 1.3-1.3h1.7M13.5 3.5h1.7c.7 0 1.3.6 1.3 1.3v1.7M16.5 13.5v1.7c0 .7-.6 1.3-1.3 1.3h-1.7M6.5 16.5H4.8c-.7 0-1.3-.6-1.3-1.3v-1.7"/><circle cx="10" cy="10" r="2.6"/>',
  files: '<path d="M5 3.5h7l3 3V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M11.8 3.8v3h3M6.7 10h6.6M6.7 13h4.5"/>',
  journal: '<path d="M10 3.2 17 16H3L10 3.2Z"/><path d="M10 7.5v4M10 14.1v.1"/>',
  anatomy: '<circle cx="10" cy="10" r="6.7"/><path d="M10 9v4M10 6.7v.1"/>',
  settings: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2M10 15.2v2M2.8 10h2M15.2 10h2M4.9 4.9l1.4 1.4M13.7 13.7l1.4 1.4M4.9 15.1l1.4-1.4M13.7 6.3l1.4-1.4"/>',
};

const SECTIONS: Array<{ id: View; label: string; kicker: string }> = [
  { id: "talk", label: "Чат", kicker: "Чат" },
  { id: "plans", label: "Задачи", kicker: "План агента" },
  { id: "frame", label: "Контекст", kicker: "Что видит модель" },
  { id: "files", label: "Файлы", kicker: "Память агента в файлах" },
  { id: "journal", label: "Журнал", kicker: "Ошибки и пропуски" },
  { id: "anatomy", label: "Система", kicker: "Как это устроено" },
];

function railButton(id: View, label: string): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "rail-item";
  b.dataset.view = id;
  b.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${ICONS[id]}</svg><span>${esc(label)}</span>`;
  b.addEventListener("click", () => void show(id));
  return b;
}

for (const s of SECTIONS) railNav.append(railButton(s.id, s.label));
railBottom.append(railButton("settings", "Настройки"));

// Панели сворачиваются как в IDE и помнят состояние.
const railBtn = q<HTMLButtonElement>("#toggle-rail");
const panelBtn = q<HTMLButtonElement>("#toggle-panel");
function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}
function setCollapsed(which: "rail" | "panel", on: boolean) {
  app.classList.toggle(which + "-collapsed", on);
  try {
    localStorage.setItem("frame." + which, on ? "1" : "0");
  } catch {
    // без хранилища панели просто не запомнятся
  }
  (which === "rail" ? railBtn : panelBtn).setAttribute("aria-pressed", String(!on));
}
setCollapsed("rail", readFlag("frame.rail"));
setCollapsed("panel", readFlag("frame.panel"));
railBtn.addEventListener("click", () => setCollapsed("rail", !app.classList.contains("rail-collapsed")));
panelBtn.addEventListener("click", () => setCollapsed("panel", !app.classList.contains("panel-collapsed")));
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
  if (e.code === "KeyB") {
    e.preventDefault();
    railBtn.click();
  } else if (e.code === "KeyJ") {
    e.preventDefault();
    panelBtn.click();
  }
});

function syncRail() {
  for (const b of document.querySelectorAll<HTMLButtonElement>(".rail-item")) {
    b.setAttribute("aria-current", b.dataset.view === S.view ? "page" : "false");
  }
}

const views: Record<View, { render: (root: HTMLElement) => Promise<void> }> = {
  talk,
  plans,
  frame,
  files,
  journal,
  anatomy,
  settings,
};

// Диктору говорим отдельной строкой: раньше aria-live висел на #view, и весь
// чат зачитывался заново каждые полторы секунды.
const liveRegion = q<HTMLElement>("#live");
function announce(text: string) {
  liveRegion.textContent = text;
}

// Два конкурирующих show() (клик по полке во время незавершённого чтения)
// писали в один #view; поколение отсекает опоздавшего.
let showSeq = 0;

/**
 * @param quiet — «обнови содержимое», а не «покажи другой раздел»: без
 *   промежуточного «читаю…» и с сохранением прокрутки. Нужен живым обновлениям
 *   (журнал под штормом откладываний иначе мигает и уезжает в начало).
 */
export async function show(id: View, opts: { quiet?: boolean } = {}) {
  const gen = ++showSeq;
  S.view = id;
  syncRail();
  const section = SECTIONS.find((s) => s.id === id);
  headKicker.textContent = section ? section.kicker : "Программа";
  if (id !== "talk") {
    headTitle.textContent = section?.label ?? "Настройки";
    // Почерк — только над комнатой агента; «Настройки» и остальные экраны —
    // обычной шапкой (иначе класс с комнаты переезжал на них, найдено живьём).
    headTitle.classList.remove("hand");
  }
  const talking = id === "talk";
  composer.hidden = !talking;
  panel.hidden = !talking;
  app.classList.toggle("with-panel", talking);
  panelBtn.hidden = !talking;
  const keepScroll = opts.quiet ? view.scrollTop : 0;
  if (!opts.quiet) view.innerHTML = '<div class="empty">читаю…</div>';
  try {
    await views[id].render(view);
    if (gen !== showSeq) return;
    if (opts.quiet) view.scrollTop = keepScroll;
    announce(section?.label ?? "Настройки");
  } catch (e) {
    if (gen !== showSeq) return;
    // Раньше сюда прилетало «Failed to fetch» английской строкой браузера как
    // единственное объяснение, без кнопки и без подсказки, что делать.
    view.innerHTML = failHTML(e);
    bindFail(view, () => void show(id));
    announce(humanError(e).text);
  }
}

// ---------------------------------------------------------------- комнаты

function roomsFromRuns(runs: Run[], chats: Array<{ peer_id: string; title?: string; messages?: number }>): Room[] {
  const byKey = new Map<string, Room>();
  // Комната окна носит имя агента, а не слово «Окно» (слово владельца 06.09):
  // это её голос здесь, а не место.
  byKey.set(WINDOW_ROOM, { key: WINDOW_ROOM, name: S.agent, live: false, count: 0 });
  for (const c of chats) {
    const key = String(c.peer_id);
    if (key === "pult") continue;
    if (!byKey.has(key)) byKey.set(key, { key, name: c.title || "чат " + key, live: false, count: c.messages || 0 });
  }
  for (const r of runs) {
    if (r.kind !== "chat_turn" || r.chat_id == null) continue;
    let key = String(r.chat_id);
    if (key === "pult") key = WINDOW_ROOM;
    const room = byKey.get(key) ?? { key, name: r.chat_title || "чат " + key, live: false, count: 0 };
    if (runIsLive(r.status)) room.live = true;
    room.count += 1;
    if (!byKey.has(key)) byKey.set(key, room);
  }
  return [...byKey.values()];
}

function renderRooms() {
  roomsBox.replaceChildren(
    ...S.rooms.map((room) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "room";
      b.setAttribute("aria-current", String(room.key === S.room));
      // Имя агента над его комнатой — его почерком (.hand); чужие комнаты
      // (чаты Telegram) остаются гротеском интерфейса.
      b.innerHTML = `<span class="room-name${room.key === WINDOW_ROOM ? " hand" : ""}">${esc(room.name)}</span>` +
        (room.count ? `<span class="room-count">${room.count}</span>` : "") +
        `<span class="dot ${room.live ? "live" : ""}"></span>`;
      b.addEventListener("click", () => {
        S.room = room.key;
        S.roomName = room.name;
        renderRooms();
        void show("talk");
      });
      return b;
    }),
  );
  // Шапка полки — имя ПРОДУКТА, не агента: слово владельца 06.09 («оставил бы
  // Hélène в рабочем окне как ПО»). Имя агента живёт в подписи его слов.
  q<HTMLElement>("#rail-agent").textContent = PRODUCT_NAME;
}

export async function loadRooms() {
  const [runs, chats] = await Promise.all([
    api<Run[]>("/api/runs?limit=200"),
    api<Array<{ peer_id: string; title?: string; messages?: number }>>("/api/chats").catch(() => []),
  ]);
  S.runs = runs;
  S.rooms = roomsFromRuns(runs, chats);
  const current = S.rooms.find((r) => r.key === S.room);
  if (current) S.roomName = current.name;
  renderRooms();
}

// ---------------------------------------------------------------- состояние

/**
 * Перезапуск честно: если агента держит служба Windows, оболочка своих детей не
 * поднимала, и перезапуск окна — нулевое действие. Раньше владелец жал кнопку
 * и не получал ничего, без единого слова почему.
 */
export async function restartHarness() {
  let svc = "";
  try {
    svc = await shell<string>("service_state");
  } catch {
    // вне приложения (веб) — служба не при делах
  }
  if (svc === "running") {
    // Указатель на карточку: отдельной «Служба Windows» нет, службой управляет
    // секция внутри карточки «Режим». Служба при этом не режим и ограду не
    // снимает — она опция поверх выбранной ограды (см. ./mode).
    toast("Агента держит служба Windows: перезапуск окна её не тронет. Настройки → Режим: сними и поставь службу заново.");
    void show("settings");
    return;
  }
  try {
    await shell("restart_self");
  } catch (e) {
    toast("Не перезапустилось: " + humanError(e).text);
  }
}

function renderState(s: AgentState | null, connected: boolean) {
  paintPulse(connected);
  if (!connected) {
    statePill.dataset.level = "off";
    stateText.textContent = "Нет связи с харнессом";
    stateAction.hidden = true;
    return;
  }
  if (!s) return;
  statePill.dataset.level = s.level;
  stateText.textContent = s.phrase;
  statePill.title = s.phrase;
  if (s.action) {
    stateAction.hidden = false;
    stateAction.textContent = s.action.label;
    stateAction.onclick = () => {
      if (s.action?.target === "settings") void show("settings");
      else if (s.action?.target === "restart") void restartHarness();
    };
  } else {
    stateAction.hidden = true;
  }
  // «Долгое молчание» без долга — сигнал разработчику про автономию, не тревога
  // для человека: агент просто ничем не занят. Остальные тревоги показываем.
  const alarms = (s.alarms || []).filter((a) => a.kind !== "long_silence");
  alarmBox.hidden = !alarms.length;
  alarmBox.innerHTML = alarms.map((a) => `<span>⚠ ${esc(a.text)}</span>`).join(" · ");
}

export async function refreshState() {
  try {
    const s = await api<AgentState>("/api/state");
    S.agentState = s;
    if (s.agent) {
      S.agent = s.agent;
      // Шапка полки остаётся именем продукта: имя агента из состояния сюда не
      // пишем (три места писали по-разному — вот это и возвращало «Мира»).
      q<HTMLElement>("#rail-agent").textContent = PRODUCT_NAME;
      // Комната окна — по имени агента: переименовали в настройках — сменилась
      // и она, в списке и в шапке, если открыта именно она.
      const win = S.rooms.find((r) => r.key === WINDOW_ROOM);
      if (win && win.name !== s.agent) {
        win.name = s.agent;
        if (S.room === WINDOW_ROOM) {
          S.roomName = s.agent;
          const title = document.querySelector<HTMLElement>("#head-title");
          if (title && S.view === "talk") title.textContent = s.agent;
        }
        renderRooms();
      }
    }
    // Связь берём настоящую: api() умеет уйти на HTTP-фолбэк при мёртвом
    // сокете, и жёсткое `true` затирало честное «Нет связи с харнессом».
    renderState(s, S.connected);
  } catch {
    // связь решает пилюля через onConnection
  }
}

// Последняя строка расхода и время, когда её подтвердили. При обрыве связи
// цифры не выдаём за текущие: раньше рядом с «Нет связи» бодро стояли
// вчерашние токены, и владелец верил им.
let pulseHTML = "";
let pulseStamp = "";
function paintPulse(connected: boolean) {
  if (!pulseHTML) {
    pulseBox.textContent = "";
    return;
  }
  pulseBox.classList.toggle("stale", !connected);
  pulseBox.innerHTML = connected ? pulseHTML : `<span class="muted">данные от ${esc(pulseStamp)}</span> · ${pulseHTML}`;
}

async function refreshPulse() {
  try {
    const p = await api("/api/pulse");
    const l = p.last || {};
    if (!l.ts) {
      pulseHTML = "";
      pulseBox.textContent = "";
      return;
    }
    const total = (l.in || 0) + (l.cached || 0);
    const share = total ? Math.round((100 * (l.cached || 0)) / total) : 0;
    pulseHTML =
      `${fmtTs(l.ts)} · <b>${esc(l.model || "")}</b> · кэш <span class="cachebar"><i style="width:${share}%"></i></span>${share}% · ` +
      `${fmtN(total)}→${fmtN(l.out || 0)}${l.err ? ' · <span class="err-msg">ошибка</span>' : ""}`;
    pulseStamp = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (p.calls_day != null) {
      pulseBox.title = `За сутки: вызовов ${fmtN(p.calls_day)}` +
        (p.cache_day != null ? `, доля кэша ${p.cache_day}%` : "") +
        (p.cache_now != null ? `; сейчас ${p.cache_now}%` : "");
    }
    paintPulse(S.connected);
  } catch {
    // Цифра не подтверждена живым ответом — значит она не текущая.
    paintPulse(false);
  }
}

// ---------------------------------------------------------------- композер

// Черновик переживает падение трубы и закрытие окна: набранное не должно
// пропадать никогда.
const draftKey = (room: string) => "frame.draft." + room;
function saveDraft(room: string, text: string) {
  try {
    if (text) localStorage.setItem(draftKey(room), text);
    else localStorage.removeItem(draftKey(room));
  } catch {
    // без хранилища черновик живёт до перезапуска
  }
}
function readDraft(room: string): string {
  try {
    return localStorage.getItem(draftKey(room)) || "";
  } catch {
    return "";
  }
}
function autoGrow() {
  say.style.height = "auto";
  say.style.height = Math.min(say.scrollHeight, 180) + "px";
}

let composerRoom = "";
function syncComposer() {
  composerTarget.textContent = S.room === WINDOW_ROOM ? "" : `в «${S.roomName}»`;
  // Черновик подставляем только при настоящей смене комнаты: событие
  // frame-room летит на каждой перерисовке чата (раз в 1.5 с во время хода),
  // и иначе оно затирало бы то, что владелец печатает прямо сейчас.
  if (composerRoom !== S.room) {
    saveDraft(composerRoom, composerRoom ? say.value : "");
    composerRoom = S.room;
    say.value = readDraft(S.room);
    autoGrow();
  }
}

say.addEventListener("input", () => {
  autoGrow();
  saveDraft(S.room, say.value);
});
say.addEventListener("keydown", (e) => {
  // sending и isComposing: без них два быстрых Enter давали два хода агента по
  // одному тексту (и два счёта), а Enter подтверждения IME-композиции при
  // кириллическом вводе отправлял недописанное.
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !sending) {
    e.preventDefault();
    void doSend();
  }
});
send.addEventListener("click", () => void doSend());

let sending = false;
let pendSeq = 0;

/** Куда делась реплика владельца — словами, по настоящему состоянию агента. */
function sendNote(chat: string, midturn: boolean): string {
  if (chat) return midturn ? `ушло в «${S.roomName}»` : `ждёт хода в «${S.roomName}»`;
  if (midturn) return "агент читает сейчас";
  const st = S.agentState;
  if (st && st.runner && !st.runner.alive) return "ждёт запуска агента";
  return "ждёт следующего хода";
}

async function doSend() {
  if (sending) return;
  const text = say.value.trim();
  if (!text) return;
  const room = S.room;
  const chat = room === WINDOW_ROOM ? "" : room;
  // Труба принимает записку в telegram-комнату и без бота (midturn:true), а
  // руннер потом молча выбрасывает её в log.warning «бота нет — некуда везти».
  // Состояние это знает заранее — спрашиваем его, вместо того чтобы терять текст.
  if (chat && S.agentState?.telegram && !S.agentState.telegram.enabled) {
    toast(`Telegram не подключён: везти сообщение в «${S.roomName}» некуда. Настройки → Telegram.`);
    return;
  }
  // Оптимистичное эхо: реплика ложится в ленту сразу. Раньше поле очищалось, в
  // ленте не появлялось ничего (туда пишет только руннер, между ходами), и
  // экран говорил «Здесь пока тихо» сразу после того, как владелец написал.
  const pending: Pending = {
    id: ++pendSeq,
    room,
    text,
    at: new Date().toISOString(),
    state: "sending",
    note: "отправляется…",
  };
  S.pending.push(pending);
  sending = true;
  send.disabled = true;
  const typed = say.value;
  say.value = "";
  autoGrow();
  saveDraft(room, "");
  talk.paintPending();
  const slow = window.setTimeout(() => {
    if (pending.state === "sending") {
      pending.note = "агент не отвечает уже пять секунд…";
      talk.paintPending();
    }
  }, 5000);
  try {
    const data = await post("/api/say", chat ? { text, chat } : { text });
    pending.state = "queued";
    pending.note = sendNote(chat, !!data?.midturn);
    talk.paintPending();
    talk.afterSend();
  } catch (e) {
    // Текст не теряем: пузырь убираем, набранное возвращаем в поле.
    S.pending = S.pending.filter((p) => p.id !== pending.id);
    talk.paintPending();
    if (S.room === room) {
      say.value = typed;
      autoGrow();
      saveDraft(room, typed);
      say.focus();
    } else {
      saveDraft(room, typed);
    }
    toast("Не ушло: " + humanError(e).text);
  }
  clearTimeout(slow);
  sending = false;
  send.disabled = false;
}

// ---------------------------------------------------------------- события

onConnection((ok) => {
  S.connected = ok;
  if (ok) {
    void refreshState();
    void loadRooms().then(() => {
      if (S.view === "talk") void show("talk");
    });
    void refreshPulse();
  } else {
    renderState(S.agentState, false);
  }
});

let skipsTimer = 0;
let skipsPaintedAt = 0;
function paintJournal() {
  skipsPaintedAt = Date.now();
  if (S.view === "journal") void show("journal", { quiet: true });
}
onEvent((ev) => {
  if (ev.t === "health") void refreshState();
  if (ev.t === "llm") void refreshPulse();
  if (ev.t === "run") {
    void loadRooms();
    talk.onRunEvent(String(ev.run_id ?? ""));
    void refreshState();
  }
  // Дебаунс, как у чата: под штормом откладываний (продукт сам заводит тревогу
  // defer_storm) события шли раз в 1.33 с, и журнал — экран, написанный ровно
  // для этого случая, — мигал «читаю…» и уезжал в начало таблицы.
  // Потолок ожидания: сплошной поток событий не должен бесконечно отодвигать
  // перерисовку («хвостовой» дебаунс без потолка перестаёт обновлять экран
  // ровно под штормом, ради которого журнал и открыли).
  if (ev.t === "skips" && S.view === "journal") {
    clearTimeout(skipsTimer);
    if (Date.now() - skipsPaintedAt > 3000) paintJournal();
    else skipsTimer = window.setTimeout(paintJournal, 1500);
  }
});

// ---------------------------------------------------------------- старт

S.agent = (cfg.agent || "").trim() || "Агент";
q<HTMLElement>("#rail-agent").textContent = PRODUCT_NAME;
syncComposer();
addEventListener("frame-room", syncComposer);
addEventListener("frame-go", (e) => void show((e as CustomEvent<View>).detail));
addEventListener("frame-restart", () => void restartHarness());
connect();
void refreshState();
loadRooms()
  .then(() => show("talk"))
  .catch(() => {
    view.innerHTML = `<div class="empty"><b>${esc(S.agent)} сейчас не на связи</b>Окно продолжит попытки само. Можно оставить его открытым.</div>`;
    renderState(null, false);
  });
void refreshPulse();
setInterval(() => void refreshState(), 8000);
setInterval(() => void refreshPulse(), 20000);
