// Hélène — окно. Полка слева (разделы и чаты), переписка в центре, ход агента
// справа; состояние агента одной фразой в шапке; настройки экраном, а не
// файлом. Большой надписи с именем продукта нет — только подпись внизу полки
// (слово владельца 07.09).
import "./styles/app.css";
import { api, cfg, connect, inTauri, onConnection, onEvent, post, shell } from "./api";
import { applyTheme } from "../../ui-kit/dom";
import { watchShellVersion } from "../../ui-kit/version";
import { setResultFetcher } from "../../ui-kit/steps";
import { bindFail, esc, failHTML, fmtAge, fmtK, fmtTs, humanError, q, toast } from "./lib";
import { PRODUCT_NAME, S, WINDOW_ROOM, foreignHarness, isWindowRoom, runIsRecent, type AgentState, type Pending, type Room, type View } from "./state";
import { buildRooms, createRoom, deleteRoom, fetchRooms, renameRoom } from "./rooms";
import * as panel from "./panel";
import * as now from "./views/now";
import * as talk from "./views/talk";
import * as wakes from "./views/wakes";
import * as plans from "./views/plans";
import * as frame from "./views/frame";
import * as files from "./views/files";
import * as journal from "./views/journal";
import * as anatomy from "./views/anatomy";
import * as settings from "./views/settings";

// Длинный результат руки или её слово дочитываются файлом прогона по кнопке в ленте шагов.
setResultFetcher((run, rid) => api(`/api/run/${encodeURIComponent(run)}/result/${encodeURIComponent(rid)}`));

const view = q<HTMLElement>("#view");
const app = q<HTMLElement>("#app");
const railNav = q<HTMLElement>("#rail-nav");
const railBottom = q<HTMLElement>("#rail-bottom");
const railSign = q<HTMLElement>("#rail-sign");
const roomsBox = q<HTMLElement>("#rooms");
const roomAdd = q<HTMLButtonElement>("#room-add");
const headKicker = q<HTMLElement>("#head-kicker");
const headTitle = q<HTMLElement>("#head-title");
const statePill = q<HTMLElement>("#state");
const stateText = q<HTMLElement>("#state-text");
const stateAction = q<HTMLButtonElement>("#state-action");
const pulseBox = q<HTMLElement>("#pulse");
const alarmBox = q<HTMLElement>("#alarm");
const composer = q<HTMLElement>("#composer");
const composerTarget = q<HTMLElement>("#composer-target");
const composerNote = q<HTMLElement>("#composer-note");
const say = q<HTMLTextAreaElement>("#say");
const send = q<HTMLButtonElement>("#send");
const panelBox = q<HTMLElement>("#panel");
const menu = q<HTMLElement>("#menu");

// ---------------------------------------------------------------- тема

// Тема — как в системе (слово владельца 07.09): светлая днём, тёмная ночью,
// без переключателя в окне.
applyTheme("system");

// Веб-версия окна (Пульт за каналом) обновляется сама, когда на сервере новая
// сборка; в оболочке helene:// сверка тихо не срабатывает — там статика с диска.
if (!inTauri) watchShellVersion();

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
  now: '<path d="M3.5 10h3l2-5 3 10 2-5h3"/>',
  wakes: '<circle cx="10" cy="10.5" r="5.5"/><path d="M10 7.5v3l2 1.5M6 3.5 3.5 5.5M14 3.5l2.5 2"/>',
  talk: '<path d="M4 5.5h12v8H8l-4 3z"/>',
  plans: '<rect x="3.5" y="4" width="13" height="12" rx="2"/><path d="M6.5 2.8v2.5M13.5 2.8v2.5M6.5 8h7M6.5 11h4"/>',
  frame: '<path d="M3.5 6.5V4.8c0-.7.6-1.3 1.3-1.3h1.7M13.5 3.5h1.7c.7 0 1.3.6 1.3 1.3v1.7M16.5 13.5v1.7c0 .7-.6 1.3-1.3 1.3h-1.7M6.5 16.5H4.8c-.7 0-1.3-.6-1.3-1.3v-1.7"/><circle cx="10" cy="10" r="2.6"/>',
  files: '<path d="M5 3.5h7l3 3V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M11.8 3.8v3h3M6.7 10h6.6M6.7 13h4.5"/>',
  journal: '<path d="M10 3.2 17 16H3L10 3.2Z"/><path d="M10 7.5v4M10 14.1v.1"/>',
  anatomy: '<circle cx="10" cy="10" r="6.7"/><path d="M10 9v4M10 6.7v.1"/>',
  settings: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2M10 15.2v2M2.8 10h2M15.2 10h2M4.9 4.9l1.4 1.4M13.7 13.7l1.4 1.4M4.9 15.1l1.4-1.4M13.7 6.3l1.4-1.4"/>',
};

const SECTIONS: Array<{ id: View; label: string; kicker: string; key: string }> = [
  { id: "now", label: "Сейчас", kicker: "Что агент делает", key: "1" },
  { id: "talk", label: "Чат", kicker: "", key: "2" },
  { id: "plans", label: "Задачи", kicker: "Агенда, доска, субагенты", key: "3" },
  { id: "wakes", label: "Вейки", kicker: "Пробуждения по расписанию", key: "4" },
  { id: "frame", label: "Контекст", kicker: "Что видит модель", key: "5" },
  { id: "files", label: "Файлы", kicker: "Память агента в файлах", key: "6" },
  { id: "journal", label: "Журнал", kicker: "Ошибки и пропуски", key: "7" },
  { id: "anatomy", label: "Система", kicker: "Как это устроено", key: "8" },
];

function railButton(id: View, label: string, key: string): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "rail-item";
  b.dataset.view = id;
  b.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${ICONS[id]}</svg><span>${esc(label)}</span><kbd>Ctrl+${esc(key)}</kbd>`;
  b.addEventListener("click", () => void show(id));
  return b;
}

for (const s of SECTIONS) railNav.append(railButton(s.id, s.label, s.key));
railBottom.append(railButton("settings", "Настройки", ","));

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
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
  if (e.shiftKey) return;
  if (e.code === "KeyB") {
    e.preventDefault();
    railBtn.click();
  } else if (e.code === "KeyJ") {
    e.preventDefault();
    panelBtn.click();
  } else if (e.code === "KeyN") {
    e.preventDefault();
    void newRoom();
  } else if (e.code === "Comma") {
    e.preventDefault();
    void show("settings");
  } else if (/^Digit[1-8]$/.test(e.code)) {
    const s = SECTIONS[Number(e.code.slice(5)) - 1];
    if (s) {
      e.preventDefault();
      void show(s.id);
    }
  }
});

function syncRail() {
  for (const b of document.querySelectorAll<HTMLButtonElement>(".rail-item")) {
    b.setAttribute("aria-current", b.dataset.view === S.view ? "page" : "false");
  }
}

const views: Record<View, { render: (root: HTMLElement) => Promise<void> }> = {
  now,
  talk,
  plans,
  wakes,
  frame,
  files,
  journal,
  anatomy,
  settings,
};

// Диктору говорим отдельной строкой: aria-live на #view зачитывал бы весь чат
// заново каждые полторы секунды.
const liveRegion = q<HTMLElement>("#live");
function announce(text: string) {
  liveRegion.textContent = text;
}

// Два конкурирующих show() (клик по полке во время незавершённого чтения)
// писали в один #view; поколение отсекает опоздавшего.
let showSeq = 0;

/**
 * @param quiet — «обнови содержимое», а не «покажи другой раздел»: без
 *   промежуточного «читаю…» и с сохранением прокрутки.
 */
export async function show(id: View, opts: { quiet?: boolean } = {}) {
  const gen = ++showSeq;
  S.view = id;
  syncRail();
  const section = SECTIONS.find((s) => s.id === id);
  headKicker.textContent = section ? section.kicker : "";
  if (id !== "talk") {
    headTitle.textContent = section?.label ?? "Настройки";
    headTitle.classList.remove("hand");
  }
  const talking = id === "talk";
  composer.hidden = !talking;
  panelBox.hidden = !talking;
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
    view.innerHTML = failHTML(e);
    bindFail(view, () => void show(id));
    announce(humanError(e).text);
  }
}

// ---------------------------------------------------------------- комнаты

let roomPicked = false;
function selectRoom(room: Room) {
  roomPicked = true;
  S.room = room.key;
  S.roomName = room.name;
  renderRooms();
  void show("talk");
}

function roomButton(room: Room): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "room" + (room.stub ? " stub" : "");
  b.dataset.key = room.key;
  b.setAttribute("aria-current", String(room.key === S.room));
  // Имя агента над его комнатой — его почерком; остальные — гротеском.
  const hand = room.key === WINDOW_ROOM;
  b.innerHTML =
    `<span class="dot ${room.live ? "live" : ""}"></span>` +
    `<span class="room-name${hand ? " hand" : ""}" title="${esc(room.name)}">${esc(room.name)}</span>` +
    (room.count && !hand ? `<span class="room-count">${room.count}</span>` : "");
  if (room.kind === "window" && room.key !== WINDOW_ROOM) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "icon-btn room-more";
    more.setAttribute("aria-label", "Действия с чатом");
    more.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1.3"/><circle cx="10" cy="10" r="1.3"/><circle cx="15" cy="10" r="1.3"/></svg>';
    more.addEventListener("click", (e) => {
      e.stopPropagation();
      openMenu(room, more.getBoundingClientRect());
    });
    b.append(more);
    b.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openMenu(room, new DOMRect(e.clientX, e.clientY, 0, 0));
    });
  }
  b.addEventListener("click", () => selectRoom(room));
  return b;
}

function renderRooms() {
  const nodes: HTMLElement[] = [];
  let group = "";
  for (const room of S.rooms) {
    if (room.kind === "telegram" && group !== "telegram") {
      group = "telegram";
      const label = document.createElement("div");
      label.className = "rooms-group";
      label.textContent = "Telegram";
      nodes.push(label);
    }
    nodes.push(roomButton(room));
  }
  roomsBox.replaceChildren(...nodes);
  roomAdd.hidden = false;
}

export async function loadRooms() {
  const { runs, chats } = await fetchRooms();
  S.runs = runs;
  S.rooms = buildRooms(runs, chats);
  // Чужой харнесс (Пульт Праксис): комната окна у неё пуста — чат открывается
  // на самой свежей комнате, пока владелец не выбрал сам.
  if (foreignHarness() && !roomPicked && S.room === WINDOW_ROOM) {
    const fresh = S.rooms.find((r) => r.kind === "telegram" && r.count > 0) ?? S.rooms.find((r) => r.kind === "telegram");
    if (fresh) {
      S.room = fresh.key;
      S.roomName = fresh.name;
    }
  }
  const current = S.rooms.find((r) => r.key === S.room);
  if (current) S.roomName = current.name;
  else if (S.room !== WINDOW_ROOM) {
    // Комнату убрали (другое окно, харнесс) — возвращаемся в основную.
    S.room = WINDOW_ROOM;
    S.roomName = S.agent;
  }
  renderRooms();
}

async function newRoom() {
  try {
    const room = await createRoom("Новый чат");
    S.rooms.splice(S.rooms.findIndex((r) => r.kind === "telegram") < 0 ? S.rooms.length : S.rooms.findIndex((r) => r.kind === "telegram"), 0, room);
    // Новая комната сразу первой среди чатов окна, после основной.
    const i = S.rooms.indexOf(room);
    S.rooms.splice(i, 1);
    S.rooms.splice(1, 0, room);
    selectRoom(room);
    if (room.stub) {
      toast("Этот харнесс ещё не умеет несколько чатов: чат создан как заглушка, писать в него нельзя. Появится в 0.3.3.");
    }
    startRename(room);
  } catch (e) {
    toast("Чат не создался: " + humanError(e).text);
  }
}
roomAdd.addEventListener("click", () => void newRoom());

/** Переименование на месте: поле вместо имени, Enter — сохранить, Esc — отмена. */
function startRename(room: Room) {
  const b = roomsBox.querySelector<HTMLElement>(`.room[data-key="${CSS.escape(room.key)}"]`);
  const name = b?.querySelector<HTMLElement>(".room-name");
  if (!b || !name) return;
  const input = document.createElement("input");
  input.className = "field-input";
  input.value = room.name;
  input.style.height = "26px";
  input.style.flex = "1";
  input.style.minWidth = "0";
  input.style.padding = "0 8px";
  input.style.fontSize = "13px";
  name.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save: boolean) => {
    if (done) return;
    done = true;
    const value = input.value.trim();
    if (save && value && value !== room.name) {
      try {
        await renameRoom(room, value);
        if (S.room === room.key) S.roomName = room.name;
        // Заголовок и панель хода носят имя комнаты — перерисовать тихо.
        if (S.view === "talk" && S.room === room.key) void show("talk", { quiet: true });
      } catch (e) {
        toast(humanError(e).text);
      }
    }
    renderRooms();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      void finish(false);
    }
    e.stopPropagation();
  });
  input.addEventListener("blur", () => void finish(true));
  input.addEventListener("click", (e) => e.stopPropagation());
}

function openMenu(room: Room, at: DOMRect) {
  menu.replaceChildren();
  const item = (text: string, cls: string, fn: () => void) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = text;
    b.addEventListener("click", () => {
      closeMenu();
      fn();
    });
    menu.append(b);
  };
  item("Переименовать", "", () => startRename(room));
  item("Убрать чат", "danger", () => void removeRoom(room));
  menu.hidden = false;
  const x = Math.min(at.left, innerWidth - menu.offsetWidth - 8);
  const y = Math.min(at.bottom + 4, innerHeight - menu.offsetHeight - 8);
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  setTimeout(() => document.addEventListener("pointerdown", closeMenu, { once: true }), 0);
  document.addEventListener("keydown", closeMenuOnEsc);
}

function closeMenu() {
  menu.hidden = true;
  document.removeEventListener("keydown", closeMenuOnEsc);
}
function closeMenuOnEsc(e: KeyboardEvent) {
  if (e.key === "Escape") closeMenu();
}

async function removeRoom(room: Room) {
  try {
    await deleteRoom(room);
    S.rooms = S.rooms.filter((r) => r.key !== room.key);
    if (S.room === room.key) {
      S.room = WINDOW_ROOM;
      S.roomName = S.agent;
      void show("talk");
    }
    renderRooms();
    toast(room.stub ? "Чат убран." : "Чат убран: переписка отложена в архив агента, не удалена.");
  } catch (e) {
    toast(humanError(e).text);
  }
}

// ---------------------------------------------------------------- состояние

/**
 * Перезапуск честно: если агента держит служба Windows, оболочка своих детей не
 * поднимала, и перезапуск окна — нулевое действие.
 */
export async function restartHarness() {
  let svc = "";
  try {
    svc = await shell<string>("service_state");
  } catch {
    // вне приложения (веб) — служба не при делах
  }
  if (svc === "running") {
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

/**
 * Чужой харнесс (Пульт Праксис): сердцебиения Hélène нет, и канал честно
 * отвечает «Не запущен». Для окна это не тревога, а другой способ судить о
 * жизни: свежий вызов модели и идущие прогоны.
 */
function foreignState(s: AgentState): { level: string; phrase: string } {
  const running = S.runs.find((r) => r.status === "running" && runIsRecent(r));
  if (running) return { level: "live", phrase: "Ведёт ход" + (running.chat_title ? ` · ${running.chat_title}` : "") };
  const at = s.brain?.last_call_at;
  if (!at) return { level: "warn", phrase: "Вызовов модели ещё не было" };
  const ageMin = (Date.now() / 1000 - at) / 60;
  const age = fmtAge(at);
  const when = age === "только что" ? age : `${age} назад`;
  if (ageMin < 15) return { level: "ok", phrase: `На связи · модель отвечала ${when}` };
  return { level: "warn", phrase: `Модель молчит ${age}` };
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
  if (foreignHarness()) {
    const f = foreignState(s);
    statePill.dataset.level = f.level;
    stateText.textContent = f.phrase;
    statePill.title = "Дерево ведёт чужой харнесс: снимка Hélène нет, состояние — по вызовам модели и прогонам.";
    stateAction.hidden = true;
    alarmBox.hidden = true;
    return;
  }
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
  // «Долгое молчание» без долга — сигнал разработчику, не тревога для человека.
  const alarms = (s.alarms || []).filter((a) => a.kind !== "long_silence");
  alarmBox.hidden = !alarms.length;
  alarmBox.innerHTML = alarms.map((a) => `<span>⚠ ${esc(a.text)}</span>`).join(" · ");
}

export async function refreshState() {
  try {
    const s = await api<AgentState>("/api/state");
    const wasBusy = !!S.agentState?.runner?.busy;
    S.agentState = s;
    // Имя: снимок харнесса знает его лучше всех; без снимка (чужой харнесс)
    // имя даёт config.js страницы — иначе Праксис звалась бы «Агент».
    const named = s.anatomy === false && (cfg.agent || "").trim() ? (cfg.agent || "").trim() : s.agent;
    if (named) {
      s.agent = named;
      S.agent = named;
      // Комната окна — по имени агента: переименовали в настройках — сменилась
      // и она, в списке и в шапке, если открыта именно она.
      const win = S.rooms.find((r) => r.key === WINDOW_ROOM);
      if (win && win.name !== s.agent) {
        win.name = s.agent;
        if (S.room === WINDOW_ROOM) {
          S.roomName = s.agent;
          if (S.view === "talk") headTitle.textContent = s.agent;
        }
        renderRooms();
      }
    }
    // Связь берём настоящую: api() умеет уйти на HTTP-фолбэк при мёртвом сокете.
    renderState(s, S.connected);
    const busy = foreignHarness()
      ? S.runs.some((r) => r.status === "running" && runIsRecent(r))
      : !!s.runner?.busy && !!s.runner?.alive;
    if (busy || wasBusy) {
      panel.tick(busy);
    }
    now.tick();
  } catch {
    // связь решает пилюля через onConnection
  }
}

// Последняя строка расхода и время, когда её подтвердили. При обрыве связи
// цифры не выдаём за текущие.
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
      `${fmtK(total)} → ${fmtK(l.out || 0)}${l.err ? ' · <span class="err-msg">ошибка</span>' : ""}`;
    pulseStamp = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (p.calls_day != null) {
      pulseBox.title = `За сутки: вызовов ${p.calls_day}` +
        (p.cache_day != null ? `, доля кэша ${p.cache_day}%` : "") +
        (p.cache_now != null ? `; сейчас ${p.cache_now}%` : "");
    }
    paintPulse(S.connected);
  } catch {
    paintPulse(false);
  }
}

// ---------------------------------------------------------------- композер

// Черновик переживает падение канала и закрытие окна.
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
  const room = S.rooms.find((r) => r.key === S.room);
  composerTarget.textContent = isWindowRoom(S.room) ? "" : `в «${S.roomName}»`;
  say.placeholder = room?.stub ? "Чат-заглушка: писать сюда пока нельзя" : isWindowRoom(S.room) ? "Написать агенту…" : `Написать в «${S.roomName}»…`;
  say.disabled = !!room?.stub;
  send.disabled = !!room?.stub || sending;
  // Черновик подставляем только при настоящей смене комнаты: событие
  // frame-room летит на каждой перерисовке чата.
  if (composerRoom !== S.room) {
    saveDraft(composerRoom, composerRoom ? say.value : "");
    composerRoom = S.room;
    say.value = readDraft(S.room);
    composerNote.textContent = "";
    autoGrow();
  }
}

say.addEventListener("input", () => {
  autoGrow();
  saveDraft(S.room, say.value);
});
// Первый замер мог пройти до стилей (dev-сервер подключает CSS асинхронно) —
// поле раздувалось до потолка. Перемеряем, когда страница собралась.
addEventListener("load", autoGrow);
say.addEventListener("keydown", (e) => {
  // sending и isComposing: без них два быстрых Enter давали два хода агента по
  // одному тексту, а Enter подтверждения IME при кириллице отправлял недописанное.
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
  if (chat && !isWindowRoom(chat)) return midturn ? `ушло в «${S.roomName}»` : `ждёт хода в «${S.roomName}»`;
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
  const current = S.rooms.find((r) => r.key === room);
  if (current?.stub) {
    toast("Этот чат — заглушка: харнесс ещё не умеет несколько чатов. Пиши в основной.");
    return;
  }
  const chat = room === WINDOW_ROOM ? "" : room;
  // Канал принимает записку в telegram-комнату и без бота, а руннер потом молча
  // выбрасывает её. Состояние это знает заранее — спрашиваем его.
  if (chat && !isWindowRoom(chat) && S.agentState?.telegram && !S.agentState.telegram.enabled) {
    toast(`Telegram не подключён: везти сообщение в «${S.roomName}» некуда. Настройки → Telegram.`);
    return;
  }
  // Оптимистичное эхо: реплика ложится в ленту сразу.
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
    composerNote.textContent = pending.note;
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
  if (ev.t === "llm") {
    void refreshPulse();
    panel.onLlm();
    now.onEvent("llm");
  }
  if (ev.t === "run") {
    if (ev.run_id) S.evCache.delete(String(ev.run_id));
    void loadRooms().then(() => now.onEvent("run"));
    talk.onRunEvent(String(ev.run_id ?? ""));
    void refreshState();
  }
  // Дебаунс под штормом откладываний: журнал — экран ровно для этого случая.
  if (ev.t === "skips" && S.view === "journal") {
    clearTimeout(skipsTimer);
    if (Date.now() - skipsPaintedAt > 3000) paintJournal();
    else skipsTimer = window.setTimeout(paintJournal, 1500);
  }
});

// ---------------------------------------------------------------- старт

S.agent = (cfg.agent || "").trim() || "Агент";
// Подпись внизу полки и заголовок вкладки — имя продукта хостинга: у Пульта
// Праксис это «Praxis» (config.js), у Hélène — Hélène (слово владельца 07.09).
const product = (cfg.product || "").trim() || PRODUCT_NAME;
railSign.textContent = product;
document.title = product;
shell<{ version: string }>("app_info")
  .then((i) => (railSign.textContent = `${product} ${i.version}`))
  .catch(() => {});
syncComposer();
addEventListener("frame-room", syncComposer);
addEventListener("frame-go", (e) => void show((e as CustomEvent<View>).detail));
addEventListener("frame-restart", () => void restartHarness());
connect();
void refreshState();
addEventListener("frame-open-room", (e) => {
  const d = (e as CustomEvent<{ key: string; name: string }>).detail;
  const room = S.rooms.find((r) => r.key === d.key) ?? { key: d.key, name: d.name, kind: isWindowRoom(d.key) ? "window" : "telegram", live: false, count: 0, mtime: 0 } as Room;
  selectRoom(room);
});
// Ссылки с карточки хода (ui-kit/steps): открыть место в чате; надиктовать агенту просьбу в композер.
function roomFor(key: string): Room {
  const k = key === "pult" || key === "window" ? WINDOW_ROOM : key;
  return S.rooms.find((r) => r.key === k) ?? ({ key: k, name: isWindowRoom(k) ? S.agent : k, kind: isWindowRoom(k) ? "window" : "telegram", live: false, count: 0, mtime: 0 } as Room);
}
addEventListener("steps-open", (e) => {
  const d = (e as CustomEvent<{ room: string; at: string }>).detail;
  selectRoom(roomFor(d.room));
  // Лента рисуется асинхронно: прыгаем, когда сообщение уже в DOM.
  let tries = 0;
  const hop = () => { if (talk.jumpTo(d.at) || ++tries > 20) return; window.setTimeout(hop, 150); };
  window.setTimeout(hop, 150);
});
addEventListener("steps-compose", (e) => {
  const d = (e as CustomEvent<{ room: string; text: string }>).detail;
  selectRoom(roomFor(d.room));
  window.setTimeout(() => { say.value = d.text; say.dispatchEvent(new Event("input")); say.focus(); }, 250);
});
loadRooms()
  .then(() => show("now"))
  .catch(() => {
    view.innerHTML = `<div class="empty"><b>${esc(S.agent)} сейчас не на связи</b>Окно продолжит попытки само. Можно оставить его открытым.</div>`;
    renderState(null, false);
  });
void refreshPulse();
setInterval(() => void refreshState(), 8000);
setInterval(() => void refreshPulse(), 20000);
