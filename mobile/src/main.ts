// Телефон: разговор с агентом с того же origin, что и труба. Ключ устройства
// приходит по QR (/m/?pair=<токен>) и живёт в localStorage; на iPhone страница
// в Safari и приложение на экране «Домой» — разные хранилища, поэтому токен
// пары годится дважды, а манифест несёт его в start_url.
import "./styles.css";

type Level = "ok" | "live" | "warn" | "error" | "off";

interface AgentState {
  agent: string;
  level: Level;
  phrase: string;
}

interface Msg {
  timestamp?: string;
  outgoing?: boolean;
  text?: string;
  sender_name?: string;
  // Флаг харнесса: строка — служебная плашка ПРОДУКТА, а не чьё-то слово
  // (localharness/transport.py:176-179 `row["system"] = True`). Пишет его
  // только сам харнесс; из Telegram он прийти не может.
  system?: boolean;
}

interface Room {
  key: string;
  name: string;
}

const q = <E extends Element = HTMLElement>(sel: string) => document.querySelector<E>(sel)!;
const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);

const params = new URLSearchParams(location.search);
const standalone =
  matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const isApple = /iPhone|iPad|iPod/.test(navigator.userAgent);

function remembered(name: string): string {
  try {
    return localStorage.getItem(name) || "";
  } catch {
    return "";
  }
}

function remember(name: string, value: string): void {
  try {
    localStorage.setItem(name, value);
  } catch {
    // приватный режим: живём без хранилища, до перезагрузки страницы
  }
}

let key = remembered("frame.device");

let agent = "Агент";
// Комната окна — единственная, куда пишет владелец с этой машины и куда не
// может написать посторонний. От этого зависит и «своё/чужое», и плашки.
const WINDOW_ROOM = "window";

let room = WINDOW_ROOM;
// Комната окна зовётся именем агента (слово владельца 06.09), не «Окно».
let roomName = agent;
let rooms: Room[] = [];

// Манифест с токеном: чтобы установленное приложение открылось с ним же.
const pairToken = params.get("pair") || "";
const manifest = document.createElement("link");
manifest.rel = "manifest";
manifest.href = "/m/manifest.webmanifest" + (pairToken ? "?pair=" + encodeURIComponent(pairToken) : "");
document.head.append(manifest);

// ---------------------------------------------------------------- транспорт

// Ключ устройства раньше уезжал в адресе КАЖДОГО запроса (`?key=dk-…`) — раз в
// шесть секунд, навсегда. Он ложился в access-log трубы и в историю браузера
// телефона. Труба ставит httponly-cookie `desk_key` на первом же запросе с
// ключом (deskapp.auth_middleware) и принимает её вместо ключа (deskapp._role),
// поэтому дальше ходим по cookie, а ключ подставляем снова только если сервер
// ответил 403. Шифрования это не даёт (см. предупреждение про открытый Wi-Fi
// ниже) — но убирает ключ из логов и истории.
let sendKey = true;
let cookieWorks = true;

function withKey(path: string): string {
  if (!key || !sendKey) return path;
  return path + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(key);
}

/** 403: ни ключа, ни cookie не хватило — доступ закрыт. */
class Denied extends Error {}
/** До компьютера не дозвонились: сети нет, труба не отвечает. */
class Offline extends Error {}
/** Компьютер ответил, но ошибкой (5xx, кривой JSON). */
class Broke extends Error {}

async function call(path: string, init?: RequestInit): Promise<Response> {
  let r: Response;
  try {
    r = await fetch(withKey(path), init);
  } catch {
    throw new Offline("нет связи");
  }
  if (r.status === 403 && !sendKey && key) {
    // cookie не сработала (приватный режим, своё хранилище у значка, год истёк)
    // — возвращаем ключ в адрес и больше не пробуем ходить без него.
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
  if (sendKey && key && cookieWorks) sendKey = false; // ключ принят -> cookie стоит
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
  const r = await call(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  try {
    return (await r.json()) as T;
  } catch {
    throw new Broke(path + ": не разобрал ответ");
  }
}

// ---------------------------------------------------------------- спаривание

function showPair(title: string, text: string, action?: { label: string; onClick: () => void }) {
  const box = q("#pair");
  box.hidden = false;
  box.innerHTML = `<h1>${esc(title)}</h1><p>${esc(text)}</p>`;
  if (action) {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = action.label;
    b.addEventListener("click", action.onClick);
    box.append(b);
  }
}

/** Чем кончился обмен токена на ключ. Раньше любой не-2xx звался «код устарел». */
type Redeem = "ok" | "spent" | "closed" | "broke" | "offline";

async function redeem(token: string): Promise<Redeem> {
  let r: Response;
  try {
    r = await fetch("/pair/redeem?token=" + encodeURIComponent(token));
  } catch {
    return "offline";
  }
  if (r.status === 403) {
    // Труба отвечает 403 в ДВУХ разных случаях, и раньше оба звались «Код
    // устарел»: (1) код израсходован или протух — это говорит сама ручка
    // обмена; (2) телефон не пускают ворота — чужой адрес, чужая страница,
    // нет ключа. Диагноз от этого зависит целиком: в первом случае помогает
    // новый QR, во втором — нет. Различаем по телу: только ручка обмена
    // говорит про «устарел / использован», всё прочее — ворота.
    const body = await r.text().catch(() => "");
    return /устарел|использован/i.test(body) ? "spent" : "closed";
  }
  if (r.status === 404 || r.status === 410) return "spent";
  if (!r.ok) return "broke";
  let d: { key?: string; agent?: string };
  try {
    d = (await r.json()) as { key?: string; agent?: string };
  } catch {
    return "broke";
  }
  if (!d.key) return "broke";
  key = d.key;
  sendKey = true;
  agent = d.agent || agent;
  remember("frame.device", key);
  return "ok";
}

/** Экран спаривания с честным диагнозом. `pair` — чем кончился обмен, если он был. */
function pairScreen(pair: Redeem | null): void {
  const retry = { label: "Попробовать снова", onClick: () => location.reload() };
  if (pair === "closed") {
    showPair(
      "Компьютер не пускает",
      "Этот телефон не принимают. На компьютере открой Настройки → Телефон, включи тумблер, перезапусти программу и покажи QR заново.",
      retry,
    );
    return;
  }
  if (pair === "broke") {
    showPair("Компьютер ответил ошибкой", "Обмен кода на ключ не удался на стороне компьютера. Покажи QR заново и попробуй ещё раз.", retry);
    return;
  }
  if (pair === "offline") {
    showPair(
      "Нет связи",
      "Компьютер с агентом не отвечает. Телефон должен быть в той же Wi-Fi, что и компьютер, или подключён к Tailscale.",
      retry,
    );
    return;
  }
  if (pair === "spent") {
    showPair("Код устарел", "Ссылка из QR живёт десять минут и годится дважды. Покажи QR на компьютере заново и открой его снова.", retry);
    return;
  }
  showPair("Нужен ключ", "Открой на компьютере Настройки → Телефон → «Показать QR» и наведи камеру. Ссылка подключит этот телефон.", retry);
}

// ---------------------------------------------------------------- экран

const md = (s: string) =>
  esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .split(/\n{2,}/)
    .map((p) => "<p>" + p.replace(/\n/g, "<br>") + "</p>")
    .join("");

function fmtTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function fmtDay(iso?: string) {
  const d = new Date(iso ?? "");
  if (isNaN(d.getTime())) return "";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const that = new Date(d);
  that.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - that.getTime()) / 86400000);
  if (diff === 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

type Access = "ok" | "denied" | "offline";

async function renderState(): Promise<Access> {
  try {
    const s = await api<AgentState>("/api/state");
    agent = s.agent || agent;
    // Имя пришло — комната окна и её ярлык носят его же.
    const win = rooms.find((r) => r.key === WINDOW_ROOM);
    if (win) win.name = agent;
    if (room === WINDOW_ROOM) roomName = agent;
    q("#top-name").textContent = agent;
    document.title = agent;
    q("#top-state").dataset.level = s.level;
    q("#top-phrase").textContent = s.phrase;
    return "ok";
  } catch (e) {
    q("#top-state").dataset.level = "off";
    if (e instanceof Denied) {
      q("#top-phrase").textContent = "Нет ключа";
      return "denied";
    }
    q("#top-phrase").textContent = e instanceof Broke ? "Компьютер ответил ошибкой" : "Нет связи";
    return "offline";
  }
}

async function loadRooms(): Promise<void> {
  let chats: Array<{ peer_id: string; title?: string }>;
  try {
    chats = await api<Array<{ peer_id: string; title?: string }>>("/api/chats");
  } catch {
    // Не прочиталось — оставляем список комнат таким, каким он был. Раньше
    // `.catch(() => [])` схлопывал его до одной комнаты при любом обрыве.
    return;
  }
  rooms = [{ key: WINDOW_ROOM, name: agent }];
  for (const c of chats) {
    const k = String(c.peer_id);
    if (k === WINDOW_ROOM || k === "pult") continue;
    rooms.push({ key: k, name: c.title || "чат " + k });
  }
  const box = q("#rooms");
  box.replaceChildren();
  if (rooms.length < 2) return;
  for (const r of rooms) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "room";
    b.textContent = r.name;
    b.setAttribute("aria-current", String(r.key === room));
    b.addEventListener("click", () => {
      room = r.key;
      roomName = r.name;
      lastFeed = "";
      void loadRooms();
      bumpFeed();
    });
    box.append(b);
  }
}

let lastFeed = "";

/** Вернуть `true`, если лента изменилась (по этому решается, замедлять ли опрос). */
async function renderFeed(): Promise<boolean> {
  const feed = q("#feed");
  let rows: Msg[];
  try {
    rows = await api<Msg[]>("/api/chat/" + encodeURIComponent(room) + "?n=120");
  } catch (e) {
    // Раньше здесь стоял `.catch(() => [])`, и НЕ ПРОЧИТАЛОСЬ выглядело точно
    // как ПУСТО: «Здесь пока тихо». Молчание прибора — не факт о мире.
    // Уже показанную переписку с экрана не стираем.
    if (!feed.dataset.ready) {
      const why =
        e instanceof Denied
          ? "Компьютер не пускает этот телефон."
          : e instanceof Broke
            ? "Компьютер ответил ошибкой."
            : "Телефон не дозвонился до компьютера.";
      feed.innerHTML = `<div class="empty">Лента не прочиталась. ${esc(why)} Попробую сам.</div>`;
    }
    return false;
  }
  const html: string[] = [];
  let day = "";
  for (const m of rows) {
    const d = fmtDay(m.timestamp);
    if (d && d !== day) {
      html.push(`<div class="day">${esc(d)}</div>`);
      day = d;
    }
    // Плашка продукта — по ФЛАГУ харнесса, а не по имени отправителя.
    // `sender_name` у входящих кладёт Telegram (localharness/botapi.py:255), и
    // выбирает его сам отправитель; строка ложится в архив ДО гейта
    // (botapi.py:585, гейт на :591). Пока здесь нюхалась строка «Hélène»,
    // любой посторонний, назвавшийся так в Telegram, рисовал владельцу на
    // телефоне свой текст в оформлении служебной плашки самой программы.
    // Вторая половина условия — переходная совместимость со СТАРЫМИ архивами:
    // записку рождения руннер подписывал «Hélène» без флага
    // (localharness/runner.py:644), и только в комнате окна, куда чужой не пишет.
    const system = !!m.system || (room === WINDOW_ROOM && !m.outgoing && m.sender_name === "Hélène");
    // Справа — только владелец. Узнать его можно ТОЛЬКО в комнате окна: там
    // входящее пишет он сам. В комнатах Telegram входящее — реплика любого
    // участника, а подписей телефон не рисовал вовсе, и чужие реплики
    // выглядели собственными репликами владельца. В чужой комнате «своё»
    // неприменимо: все слева, зато с именем — как в окне (app/src/views/talk.ts).
    const own = room === WINDOW_ROOM && !m.outgoing && !system;
    const who = system || room === WINDOW_ROOM ? "" : m.outgoing ? agent : m.sender_name || "?";
    html.push(`<div class="msg ${own ? "own" : ""} ${system ? "system" : ""}">
      <div class="msg-time">${who ? `<b>${esc(who)}</b> · ` : ""}${fmtTime(m.timestamp)}</div>
      <div class="msg-body">${md(m.text || "")}</div></div>`);
  }
  const next = html.join("");
  if (next === lastFeed && feed.dataset.ready) return false;
  lastFeed = next;
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 200;
  feed.innerHTML = next || '<div class="empty">Здесь пока тихо</div>';
  if (nearBottom || !feed.dataset.ready) feed.scrollTop = feed.scrollHeight;
  feed.dataset.ready = "1";
  q("#send").setAttribute("title", roomName);
  return true;
}

const say = q<HTMLTextAreaElement>("#say");
const send = q<HTMLButtonElement>("#send");
say.addEventListener("input", () => {
  say.style.height = "auto";
  say.style.height = Math.min(say.scrollHeight, 140) + "px";
});
say.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
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
    setTimeout(() => bumpFeed(), 1200);
  } catch (e) {
    // Раньше сюда уезжало тело ответа сервера как есть (`await r.text()`).
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
  const box = q("#notice");
  box.hidden = false;
  box.innerHTML = esc(text);
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = action.label;
    b.addEventListener("click", () => {
      box.hidden = true;
      action.onClick();
    });
    box.append(document.createElement("br"), b);
  }
}

// ------------------------------------------------------------------ опрос
//
// Было: два `setInterval` навсегда — состояние раз в 8 с, лента раз в 6 с, и
// лента тянется ЦЕЛИКОМ (`/api/chat/<room>?n=120` — на комнате с архивом это
// 165 КБ, то есть ~99 МБ в час и радиомодуль без сна). Ни паузы на скрытой
// странице, ни замедления, ни живых событий.
// Стало: живые события трубы (`/events`, тот же канал, по которому обновляется
// окно — app/src/main.ts, onEvent), пауза на скрытой странице и опрос-страховка,
// который замедляется, пока ничего не меняется, и мгновенно ускоряется от
// события, от отправки, от смены комнаты и от возврата на экран.

const FEED_MIN = 6000;
const STATE_MIN = 8000;
const STATE_MAX = 60000;

/** Насколько редко можно опрашивать ленту. Пока живы события — реже (они
 *  разбудят); без событий страховочный опрос обязан оставаться частым, иначе
 *  реплика агента появлялась бы на телефоне через минуты. */
const feedCeiling = () => (live ? 60000 : 30000);

let feedDelay = FEED_MIN;
let stateDelay = STATE_MIN;
let feedTimer = 0;
let stateTimer = 0;
let running = false;

function scheduleFeed(): void {
  clearTimeout(feedTimer);
  if (!running || document.hidden) return;
  feedTimer = window.setTimeout(() => void tickFeed(), feedDelay);
}

function scheduleState(): void {
  clearTimeout(stateTimer);
  if (!running || document.hidden) return;
  stateTimer = window.setTimeout(() => void tickState(), stateDelay);
}

// Такты не наслаиваются: событие может прийти посреди уже начатого запроса,
// и два одновременных чтения ленты — лишний трафик на телефоне.
let feedBusy = false;
let feedAgain = false;
let stateBusy = false;

async function tickFeed(): Promise<void> {
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

async function tickState(): Promise<void> {
  if (stateBusy) return;
  openLive(); // канал мог закрыться — поднимаем его на том же такте
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
  // Отзыв ключа раньше проверялся ОДИН раз при старте: отозвали при открытом
  // приложении — телефон показывал пустую ленту, а шапка просто краснела.
  stateDelay = st === "ok" ? STATE_MIN : Math.min(STATE_MAX, Math.round(stateDelay * 1.6));
  scheduleState();
}

function bumpFeed(): void {
  feedDelay = FEED_MIN;
  clearTimeout(feedTimer);
  if (running && !document.hidden) void tickFeed();
}

function bumpState(): void {
  stateDelay = STATE_MIN;
  clearTimeout(stateTimer);
  if (running && !document.hidden) void tickState();
}

// --------------------------------------------------------------- события

let live: EventSource | null = null;
let liveTries = 0;

function openLive(): void {
  // Одна попытка на сеанс: сегодня труба НЕ отдаёт /events телефону (область
  // ключа устройства, deskapp.py `_DEVICE_PATHS`), и биться в неё каждый такт
  // значило бы сорить 403-ми. Установленное соединение обнуляет счётчик само,
  // а обрыв EventSource поднимает внутри себя, не трогая счётчик.
  if (live || !running || document.hidden || liveTries >= 1) return;
  liveTries += 1;
  try {
    live = new EventSource(withKey("/events"));
  } catch {
    live = null;
    return;
  }
  live.addEventListener("open", () => {
    liveTries = 0;
  });
  live.addEventListener("message", (e) => {
    let kind = "";
    try {
      kind = String((JSON.parse(String(e.data)) as { t?: unknown })?.t ?? "");
    } catch {
      return;
    }
    if (kind === "run") {
      bumpFeed();
      bumpState();
    } else if (kind === "health" || kind === "llm") {
      bumpState();
    }
  });
  live.addEventListener("error", () => {
    // Оборванное соединение EventSource поднимает сам; закрытое (403, не тот
    // тип содержимого) — уже не поднимет, и тогда живём одним опросом.
    if (live && live.readyState === EventSource.CLOSED) live = null;
  });
}

function closeLive(): void {
  if (live) live.close();
  live = null;
}

// ------------------------------------------------------------ подсказки

/** Страница отдана по http и не через Tailscale — связь не шифруется. */
function insecureLink(): boolean {
  if (location.protocol !== "http:") return false;
  const host = location.hostname;
  if (/^100\./.test(host)) return false; // Tailscale: WireGuard шифрует сам
  return host !== "localhost" && host !== "::1" && !/^127\./.test(host);
}

function hints(): void {
  const queue: string[] = [];
  if (insecureLink() && !remembered("frame.netwarn")) {
    remember("frame.netwarn", "1");
    queue.push(
      "Связь с компьютером не шифруется — это обычный Wi-Fi. В чужой сети (кафе, отель, коворкинг) сосед может прочитать переписку и перехватить ключ этого телефона. Надёжно — только через Tailscale.",
    );
  }
  if (isApple && !standalone) {
    queue.push("Чтобы открывать как приложение: «Поделиться» → «На экран „Домой“». Первое открытие из значка допишет ключ само.");
  }
  const next = () => {
    const text = queue.shift();
    if (!text) {
      q("#notice").hidden = true;
      return;
    }
    notice(text, { label: "Понятно", onClick: next });
  };
  next();
}

// ---------------------------------------------------------------- старт

function accessLost(): void {
  running = false;
  clearTimeout(feedTimer);
  clearTimeout(stateTimer);
  closeLive();
  // Ключ из хранилища НЕ стираем: 403 бывает и от временной беды на стороне
  // компьютера (devices.json не прочитался — deskapp._devices глотает OSError и
  // отдаёт пустой список). Стёртый ключ вернуть было бы нечем.
  showPair("Компьютер не пускает", "Похоже, этот телефон отвязали в Настройках → Телефон. Покажи QR на компьютере заново и наведи камеру.", {
    label: "Проверить снова",
    onClick: () => void recheck(),
  });
}

async function recheck(): Promise<void> {
  if ((await renderState()) !== "ok") return;
  enter();
}

function enter(): void {
  q("#pair").hidden = true;
  running = true;
  liveTries = 0;
  openLive();
  stateDelay = STATE_MIN;
  scheduleState(); // состояние только что прочитано в start()/recheck() — не дёргаем
  bumpFeed();
  void loadRooms();
}

async function start() {
  let pair: Redeem | null = null;
  if (pairToken) {
    pair = await redeem(pairToken);
    // Раньше здесь стояло `if (!ok) return` — и приложение, поставленное на
    // экран «Домой», умирало НАВСЕГДА: значок открывается по start_url из
    // манифеста, а туда вшит одноразовый токен. Второй запуск получал 403
    // (uses=0, или TTL=600 c истёк, или труба перезапустилась и забыла пару) —
    // и мы выходили, не дойдя до совершенно исправного ключа в localStorage.
    // Теперь протухший токен ничего не решает: решает ключ.
    if (pair === "ok" && standalone) {
      // Токен из адреса убираем ТОЛЬКО в установленном приложении — как и
      // обещал комментарий, которого код не исполнял. В Safari он должен
      // остаться: «Поделиться → На экран „Домой“» берёт ТЕКУЩИЙ адрес, и без
      // токена значок рождается без ключа (на iOS младше 16.4 манифеста нет
      // вовсе, и другого пути передать ключ значку не существует).
      history.replaceState(null, "", "/m/");
    }
  }

  // Пробуем достучаться ДО того, как судить по наличию ключа в localStorage:
  // ключа может не быть, а cookie desk_key работать (её ставит и сам обмен,
  // deskapp.api_pair_redeem). Раньше телефон в этом случае показывал «Нужен ключ»,
  // хотя доступ у него был.
  const access = await renderState();
  if (access === "ok") {
    enter();
    hints();
    return;
  }
  if (access === "offline") {
    pairScreen("offline");
    return;
  }
  // Доступа нет. Говорим то, что знаем точно: сначала диагноз обмена кода,
  // потом — «ключ есть, но не пускают», и только в конце «нужен ключ».
  if (pair && pair !== "ok") {
    pairScreen(pair);
    return;
  }
  if (key) {
    accessLost();
    return;
  }
  pairScreen(null);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    // Скрытая страница не тянет ничего: раньше опрос жил и в кармане.
    clearTimeout(feedTimer);
    clearTimeout(stateTimer);
    closeLive();
    return;
  }
  if (!running) return;
  // liveTries тут НЕ обнуляем: живое соединение обнуляет счётчик само (open),
  // а если труба события телефону не отдаёт (403), незачем биться в неё
  // заново при каждом возврате на экран.
  openLive();
  bumpState();
  bumpFeed();
});

void start();
