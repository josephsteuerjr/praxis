// «Чаты»: её комнаты и темы. Слева список, справа переписка выбранной комнаты,
// её ходы в этой комнате со шагами и поле, чтобы написать ей туда же.
// Лента читается и рисуется так же, как на её телефоне: дни, реплики, вложения.
import { setAudioPlayer } from "../../../ui-kit/paper-audio";
import { browserSave } from "../../../ui-kit/paper-transfer";
import { artifactCaption, mediaDescriptor, mountPaperMedia, paperMediaHTML } from "../../../ui-kit/paper-media";
import { mountActionSteps, type RunDetail } from "../../../ui-kit/window/panel";
import { toast } from "../../../ui-kit/dom";
import { esc, fmtDay, fmtTime, fmtTs, md } from "../../../ui-kit/text";
import { agentState, AGENT, artifactUrl, el, gate, listToggle, why } from "../core";

setAudioPlayer(() => import("wavesurfer.js").then((module) => module.default));

interface Room { peer_id: string; title: string; kind: "window" | "telegram"; messages?: number; mtime_ns?: number }
interface Msg {
  timestamp?: string; outgoing?: boolean; text?: string; sender_name?: string; system?: boolean; kind?: string;
  topic_title?: string; media?: string; media_path?: string; media_error?: string;
}
interface Turn { run_id: string; ts?: number; kind?: string; run_label?: string; out?: string; note?: string }

const WINDOW = "window";
const windowish = (key: string) => key === WINDOW || key === "pult" || /^window-[0-9a-f]{8}$/.test(key);
const TICK = 6000;             // так же часто, как её телефон перечитывает ленту
let wanted = "";               // комната, которую попросили открыть извне

export function mount(root: HTMLElement): void {
  // Её канал через ворота раздела: скрытый раздел не опрашивает сервер, показанный — перечитывает сразу.
  const g = gate(root);
  const { api, post } = g;
  root.classList.add("chats");
  const list = el("nav", "chat-list");
  list.setAttribute("aria-label", "Комнаты");
  const search = el("input", "chat-search");
  search.type = "search";
  search.placeholder = "Найти комнату";
  search.autocomplete = "off";
  search.setAttribute("aria-label", "Найти комнату");
  const columns = el("div", "chat-columns");
  // Список комнат сворачивается кнопкой; открытая комната остаётся открытой.
  const listButton = listToggle("praxis.window.chatList", "список комнат", columns);
  const heading = el("header", "chat-head");
  const headMain = el("div", "chat-head-main");
  const title = el("h2", "chat-title");
  const meta = el("p", "muted chat-meta");
  headMain.append(title, meta);
  heading.append(listButton, headMain);
  const feed = el("div", "chat-feed feed-in");
  feed.setAttribute("role", "log");
  feed.setAttribute("aria-label", "Переписка");
  // Её ходы в этой комнате — свёрнутой строкой со счётчиком: ленте достаётся место,
  // а шаги любого хода — один щелчок. Положение запоминается.
  const turnsBox = el("details", "chat-turns");
  const turnsTitle = el("summary", "chat-turns-title");
  turnsBox.append(turnsTitle);
  try { turnsBox.open = localStorage.getItem("praxis.window.chatTurns") === "1"; } catch { /* свёрнуто */ }
  turnsBox.addEventListener("toggle", () => { try { localStorage.setItem("praxis.window.chatTurns", turnsBox.open ? "1" : "0"); } catch { /* не запомнится */ } });
  const composer = el("footer", "composer chat-composer");
  composer.innerHTML = `<div class="composer-target"></div><div class="composer-row">
    <textarea rows="1" placeholder="Написать…" enterkeyhint="send" aria-label="Сообщение"></textarea>
    <button class="send" type="button" aria-label="Отправить"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 15V5M5.5 9.5 10 5l4.5 4.5"/></svg></button></div>`;
  const say = composer.querySelector<HTMLTextAreaElement>("textarea")!;
  const send = composer.querySelector<HTMLButtonElement>("button")!;
  const target = composer.querySelector<HTMLElement>(".composer-target")!;
  const roomBox = el("section", "chat-room");
  roomBox.append(heading, feed, turnsBox, composer);
  const side = el("aside", "chat-side");
  side.append(search, list);
  columns.append(side, roomBox);
  root.append(columns);

  mountPaperMedia(roomBox, {
    key: (item) => artifactUrl(item.path), bytes: (item) => apiBytes(artifactUrl(item.path)), save: browserSave,
  });

  let rooms: Room[] = [];
  let room = wanted || WINDOW;
  let roomName = room === WINDOW ? AGENT : room;
  let controller: AbortController | undefined;
  let lastFeed = "";
  let ready = false;
  let alive = true;
  wanted = "";

  function paintList() {
    const filter = search.value.trim().toLocaleLowerCase("ru");
    list.replaceChildren(...rooms
      .filter((item) => !filter || item.title.toLocaleLowerCase("ru").includes(filter))
      .map((item) => {
        const button = el("button", "room" + (item.kind === "window" ? " window" : ""));
        button.type = "button";
        button.dataset.key = item.peer_id;
        button.setAttribute("aria-current", String(item.peer_id === room));
        const when = item.mtime_ns ? fmtTs(item.mtime_ns / 1e6) : "";
        button.innerHTML = `<span class="dot"></span><span class="room-name${item.kind === "window" ? " hand" : ""}">${esc(item.title)}</span><span class="room-count">${esc(when)}</span>`;
        button.addEventListener("click", () => void open(item.peer_id));
        return button;
      }));
  }

  async function loadRooms() {
    let loaded: Room[];
    try {
      loaded = [{ peer_id: WINDOW, title: AGENT, kind: "window" }, ...(await api<Room[]>("/api/chats")).map((item) => ({ ...item, title: item.title || "Без названия" }))];
    } catch {
      return;                       // список останется прежним; следующий тик повторит
    }
    if (!alive) return;
    rooms = loaded;
    paintList();
  }

  async function loadFeed() {
    if (!room) return;
    const peer = room;
    const mine = new AbortController();
    controller?.abort();
    controller = mine;
    let rows: Msg[];
    try {
      rows = await api<Msg[]>(`/api/chat/${encodeURIComponent(peer)}?n=240`, mine.signal);
    } catch (error) {
      if ((error as Error).name === "AbortError" || peer !== room) return;
      if (!ready) feed.innerHTML = `<div class="empty">Лента не прочиталась. ${esc(why(error))}</div>`;
      return;
    }
    if (!alive || peer !== room) return;
    const state = agentState();
    const here = windowish(peer);
    const html: string[] = [];
    let day = "";
    for (const m of rows) {
      const name = fmtDay(m.timestamp);
      if (name && name !== day) { html.push(`<div class="day">${esc(name)}</div>`); day = name; }
      // Реплика владельца в общей комнате — его собственная; слово агента — справа.
      const system = !!m.system || (here && !m.outgoing && m.sender_name === AGENT);
      const sender = m.outgoing ? AGENT : system ? AGENT : m.sender_name || "?";
      const showName = !m.outgoing && !system && !here && sender !== state?.owner;
      const own = !m.outgoing && !system && showName;
      const cls = own ? "own" : system ? "system" + (m.kind === "silence" ? " silence" : "") : m.outgoing ? "agent" : "";
      const topic = m.topic_title && !/__topic__/.test(peer) ? ` <span class="badge">${esc(m.topic_title)}</span>` : "";
      const head = m.outgoing
        ? `<span class="who-hand">${esc(AGENT)}</span><span>${fmtTime(m.timestamp)}</span>`
        : `${showName ? `<b>${esc(sender)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}</span>`;
      const descriptor = mediaDescriptor(m);
      const attachment = descriptor ? paperMediaHTML(descriptor) : m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "";
      const failed = m.media_error ? `<p class="media-error">${esc(m.media_error)}</p>` : "";
      html.push(`<div class="msg ${cls}" data-at="${esc(m.timestamp || "")}"><div class="msg-head">${head}</div><div class="msg-body">${md(artifactCaption(m.text || "", m.media_path))}${attachment}${failed}</div></div>`);
    }
    const next = html.join("");
    if (next === lastFeed && ready) return;
    const nearEnd = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 200;
    lastFeed = next;
    feed.innerHTML = next || `<div class="empty"><b>Здесь пока тихо</b>${here ? "Напишите первое сообщение внизу." : "Записей этой комнаты ещё нет."}</div>`;
    if (nearEnd || !ready) feed.scrollTop = feed.scrollHeight;
    ready = true;
  }

  async function loadTurns() {
    if (!room) return;
    const peer = room;
    let turns: Turn[];
    try {
      turns = await api<Turn[]>(`/api/chat-turns/${encodeURIComponent(peer)}?n=60`);
    } catch {
      return;
    }
    if (!alive || peer !== room) return;
    const signature = turns.map((turn) => `${turn.run_id}:${turn.out?.length ?? 0}:${turn.note ?? ""}`).join("|");
    if (turnsBox.dataset.signature === signature) return;
    turnsBox.dataset.signature = signature;
    turnsBox.replaceChildren(turnsTitle);
    turnsTitle.textContent = turns.length ? `Её ходы в этой комнате · ${turns.length}` : "Её ходы в этой комнате · ходов с записью пока нет";
    if (!turns.length) return;
    for (const turn of turns.slice().reverse()) {
      const details = el("details", "fold chat-turn");
      details.append(el("summary", "", `${fmtTs(turn.ts)} · ${turn.run_label || (turn.kind === "wake" ? "Пробуждение" : "Ход")}`));
      const body = el("div", "fold-body");
      if (turn.out) body.append(el("p", "chat-turn-out", turn.out.length > 420 ? turn.out.slice(0, 420) + "…" : turn.out));
      if (turn.note) body.append(el("p", "muted", turn.note));
      const steps = el("div", "st-list");
      body.append(steps);
      details.append(body);
      const paint = mountActionSteps(steps);
      let painted = false;
      details.addEventListener("toggle", async () => {
        if (!details.open || painted) return;
        painted = true;
        try {
          paint(await api<RunDetail>(`/api/run/${encodeURIComponent(turn.run_id)}`));
        } catch (error) {
          steps.textContent = why(error);
          painted = false;
        }
      });
      turnsBox.append(details);
    }
  }

  function syncComposer() {
    const here = windowish(room);
    target.textContent = here ? "" : "Ответ придёт в Telegram этой комнаты";
    say.placeholder = here ? `Написать ${AGENT}…` : "Поручить ей в этой комнате…";
    say.setAttribute("aria-label", here ? `Сообщение ${AGENT}` : `Поручение в комнате «${roomName}»`);
  }

  async function open(key: string) {
    if (key === room && ready) return;
    controller?.abort();
    room = key;
    roomName = rooms.find((item) => item.peer_id === key)?.title || (key === WINDOW ? AGENT : key);
    lastFeed = "";
    ready = false;
    feed.replaceChildren();
    delete turnsBox.dataset.signature;
    turnsBox.replaceChildren();
    title.textContent = roomName;
    meta.textContent = key === WINDOW ? "Комната окна: здесь она читает то, что написали вы." : "";
    syncComposer();
    paintList();
    feed.scrollTop = 0;
    await Promise.all([loadFeed(), loadTurns()]);
  }

  send.addEventListener("click", async () => {
    const text = say.value.trim();
    if (!text || !room) return;
    send.disabled = true;
    try {
      await post("/api/say", { text, chat: room });
      say.value = "";
      say.style.height = "auto";
      ready = false;
      await loadFeed();
    } catch (error) {
      toast(why(error));
    } finally {
      send.disabled = false;
      say.focus();
    }
  });
  say.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send.click(); }
  });
  say.addEventListener("input", () => { say.style.height = "auto"; say.style.height = Math.min(say.scrollHeight, 160) + "px"; });
  search.addEventListener("input", paintList);
  // Список открыли кнопкой — искать по нему можно сразу.
  listButton.addEventListener("click", () => { if (!columns.classList.contains("no-list")) search.focus({ preventScroll: true }); });

  title.textContent = roomName;
  syncComposer();
  const tick = async () => {
    if (!alive) return;
    if (!document.hidden) {
      await loadRooms();
      await loadFeed();
      await loadTurns();
    }
    g.every(() => void tick(), TICK);
  };
  void tick();
}

async function apiBytes(url: string): Promise<Blob> {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) throw new Error("вложение не прочиталось");
  return response.blob();
}

/** Открыть комнату (из «Сейчас» или другого раздела). */
export function focus(intent: unknown): void {
  const key = intent && typeof intent === "object" ? String((intent as { room?: unknown }).room || "") : "";
  if (!key) return;
  const button = document.querySelector<HTMLElement>(`#view .chat-list .room[data-key="${CSS.escape(key)}"]`);
  if (button) button.click();
}
