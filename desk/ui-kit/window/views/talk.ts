// Чат: переписка комнаты в центре — слово агента текстом, как документ, слово
// владельца пузырём справа; ошибки хода на месте, человеческим словом и с
// действием. Ход агента — в панели справа (../panel).
import { api, mediaURL } from "../api";
import { bindFail, esc, failHTML, fmtAge, fmtDay, fmtTime, humanError, md, q } from "../lib";
import * as panel from "../panel";
import { PRODUCT_NAME, S, WINDOW_ROOM, foreignHarness, isWindowRoom, type Run } from "../state";

interface Msg {
  timestamp?: string;
  outgoing?: boolean;
  text?: string;
  sender_name?: string;
  // Флаг харнесса: строка — служебная плашка ПРОДУКТА, а не слово агента
  // (localharness/transport.py, `row["system"] = True`). Из Telegram он прийти
  // не может: входящие строки собирает botapi из полей апдейта.
  system?: boolean;
  /** У служебной плашки: "silence" — молчание по решению / ход без реплики. */
  kind?: string;
  sender_id?: string | number;
  topic_title?: string;
  reply_to_message_id?: number;
  media?: string;
  /** Вложение из дерева агента: путь ОТ ДЕРЕВА и вид (transport.archive). Голос
   *  агента приезжает так; строковый `media` остаётся подписью telegram-вложения. */
  media_path?: string;
  media_kind?: string;
  edited_at?: string;
}

let refreshTimer = 0;
let root: HTMLElement | null = null;
// Последняя удачно прочитанная лента по комнатам (не больше трёх).
const lastFeed = new Map<string, string>();

function roomRuns(): Run[] {
  const key = S.room;
  return S.runs.filter((r) => {
    if (r.kind !== "chat_turn" || r.chat_id == null) return false;
    const k = String(r.chat_id);
    return k === key || (key === WINDOW_ROOM && k === "pult");
  });
}

/**
 * Архив комнаты. Тема форума (`<чат>__topic__<id>`) своего архива не имеет —
 * её строки лежат в архиве родителя с полем `topic_id`; читаем родителя и
 * оставляем только строки темы. Раньше тема открывала пустоту («архива нет»),
 * хотя переписка была.
 */
async function readArchive(peer: string): Promise<Msg[]> {
  const m = peer.match(/^(.+)__topic__(\d+)$/);
  if (!m) return api<Msg[]>("/api/chat/" + encodeURIComponent(peer) + "?n=250");
  const rows = await api<Array<Msg & { topic_id?: number | string }>>("/api/chat/" + encodeURIComponent(m[1]) + "?n=600");
  const id = m[2];
  const mine = rows.filter((r) => String(r.topic_id ?? "") === id);
  return mine.slice(-250);
}

/** Ходы, которые не дошли до конца — на месте, под перепиской. */
function failedNotices(): string {
  // Окно суток: иначе один сбой месячной давности висел красной плашкой при
  // каждом открытии Чата.
  const since = Date.now() - 24 * 3600 * 1000;
  const failed = roomRuns().filter((r) => {
    if (r.terminal_status !== "failed" && r.status !== "failed") return false;
    const at = new Date(r.created_at ?? "").getTime();
    return isNaN(at) || at >= since;
  });
  if (!failed.length) return "";
  const last = failed[0];
  const day = fmtDay(last.created_at);
  const when = (day === "Сегодня" ? "" : day + ", ") + fmtTime(last.created_at);
  const why = brainErrorIsCurrent() ? `: ${esc(S.agentState!.brain.last_error!)}` : "";
  return `<div class="notice err">
    <span class="dot failed"></span>
    <span>Ход ${esc(when)} не дошёл до конца${why}. ${failed.length > 1 ? `Таких ходов за сутки: ${failed.length}.` : ""}</span>
    <button class="notice-action" data-go="journal" type="button">Открыть журнал</button>
  </div>`;
}

/**
 * Ошибка модели относится к тому, что сейчас в шапке, только если шапка про
 * модель: фраза про модель строится из той же короткой строки last_error.
 */
function brainErrorIsCurrent(): boolean {
  const s = S.agentState;
  return !!(s && s.brain && s.brain.last_error && s.phrase.includes(s.brain.last_error));
}

function brainNotice(): string {
  const s = S.agentState;
  // Чужой харнесс (Пульт Праксис): «Не запущен» — про сердцебиение Hélène,
  // которого там нет по построению; состояние — в шапке, по вызовам модели.
  if (!s || foreignHarness()) return "";
  if (s.level === "error" || s.level === "warn") {
    const raw = brainErrorIsCurrent() ? s.brain.last_error_raw : null;
    return `<div class="notice ${s.level === "error" ? "err" : ""}">
      <span class="dot ${s.level === "error" ? "failed" : ""}"></span>
      <span>${esc(s.phrase)}</span>
      ${s.action ? `<button class="notice-action" data-go="${esc(s.action.target)}" type="button">${esc(s.action.label)}</button>` : ""}
      ${raw ? `<details class="fail-detail notice-detail"><summary>Подробности</summary><pre class="mono">${esc(raw)}</pre></details>` : ""}
    </div>`;
  }
  return "";
}

/**
 * Идущий ход и единственный честный способ его прекратить — снять руннера
 * перезапуском программы. Чистой «отмены» у хода нет: рука уже могла
 * отправить письмо, записать файл, потратить деньги.
 */
function turnNotice(): string {
  const r = S.agentState?.runner;
  if (!r || !r.alive || !r.busy) return "";
  const since = r.since ? ` (идёт ${fmtAge(r.since)})` : "";
  return `<div class="notice" data-turn-stop-box>
    <span class="dot live"></span>
    <span>Агент сейчас работает${since} — действия справа.</span>
    <button class="notice-action" data-stop-turn="ask" type="button">Остановить ход</button>
  </div>`;
}

function stubNotice(): string {
  const room = S.rooms.find((r) => r.key === S.room);
  if (!room?.stub) return "";
  return `<div class="notice">
    <span class="dot"></span>
    <span>Этот чат — заглушка: харнесс ещё не умеет несколько чатов с агентом (появится в 0.3.3). Здесь можно проверить, как это будет выглядеть; писать — в основной чат.</span>
  </div>`;
}

/** Пузыри отправленного, которое лента ещё не подтвердила. */
function pendingHTML(): string {
  return S.pending
    .filter((p) => p.room === S.room)
    .map(
      (p) => `<div class="msg own pending" data-pending="${p.id}">
      <div class="msg-head"><span>${fmtTime(p.at)}</span></div>
      <div class="msg-body">${md(p.text)}</div>
      <div class="msg-note">${esc(p.note)}</div>
    </div>`,
    )
    .join("");
}

/** Перерисовать только пузыри отправляемого, не трогая ленту. */
/**
 * Вложение строки ленты — проигрывателем или картинкой.
 *
 * ⚠ Адрес собирается ТЕМ ЖЕ способом, что и остальные запросы окна (`mediaURL`
 * из api.ts): ключ канала уезжает в запрос, иначе браузер получит 403 и покажет
 * сломанный проигрыватель вместо звука.
 *
 * `preload="none"` — намеренно: в ленте бывают десятки голосовых, и грузить их
 * все ради прокрутки незачем.
 */
function mediaBlock(m: { media_path?: string; media_kind?: string }): string {
  const rel = String(m.media_path || "").trim();
  if (!rel) return "";
  const src = mediaURL(rel);
  const name = rel.split("/").pop() || rel;
  if (String(m.media_kind || "") === "audio") {
    return `<div class="msg-media"><audio controls preload="none" src="${esc(src)}"></audio></div>`;
  }
  if (String(m.media_kind || "") === "image") {
    return `<div class="msg-media"><img loading="lazy" alt="${esc(name)}" src="${esc(src)}"></div>`;
  }
  return `<div class="msg-media"><a href="${esc(src)}" target="_blank" rel="noreferrer">${esc(name)}</a></div>`;
}

export function paintPending() {
  if (!root || S.view !== "talk") return;
  const box = root.querySelector<HTMLElement>(".pending-box");
  if (box) box.innerHTML = pendingHTML();
}

export async function render(container: HTMLElement): Promise<void> {
  root = container;
  const title = q<HTMLElement>("#head-title");
  title.textContent = S.roomName;
  // Над своей комнатой агент подписан своим почерком; чужие комнаты — нет.
  title.classList.toggle("hand", S.room === WINDOW_ROOM);
  dispatchEvent(new Event("frame-room"));
  const peer = S.room;
  const windowish = isWindowRoom(peer);
  // Сорванное чтение и пустая комната давали ОДИН результат [] — и обрыв связи
  // стирал всю переписку. Теперь пусто — только после успешного ответа.
  let rows: Msg[];
  try {
    rows = await readArchive(peer);
  } catch (e) {
    const kept = lastFeed.get(peer);
    const bar = `<div class="notice err read-fail"><span class="dot failed"></span>
      <span>${esc(humanError(e).text)} Показано последнее прочитанное.</span>
      <button class="notice-action" data-fail-retry type="button">Повторить</button></div>`;
    container.innerHTML = kept ? `<div class="center">${bar}<div class="feed">${kept}</div></div>` : `<div class="center">${failHTML(e)}</div>`;
    bindFail(container, () => void render(container));
    return;
  }
  // Подтверждённые лентой пузыри «отправляется…» снимаем.
  if (S.pending.length) {
    const seen = new Set(rows.filter((m) => !m.outgoing).map((m) => (m.text || "").trim()));
    S.pending = S.pending.filter((p) => !(p.room === peer && seen.has(p.text)));
  }
  const feed: string[] = [];
  let day = "";
  for (const m of rows) {
    const d = fmtDay(m.timestamp);
    if (d !== day) {
      feed.push(`<div class="day">${esc(d)}</div>`);
      day = d;
    }
    // Плашка продукта — по ФЛАГУ харнесса, а не по имени отправителя: имя
    // входящего из Telegram выбирает сам отправитель. Вторая половина условия —
    // совместимость со старыми архивами без флага, и только в комнате окна.
    const system = !!m.system || (windowish && !m.outgoing && m.sender_name === PRODUCT_NAME);
    const name = m.outgoing ? S.agent : system ? PRODUCT_NAME : m.sender_name || String(m.sender_id ?? "");
    // Ярлык темы — только в общем архиве чата; внутри самой темы он лишний.
    const topic = m.topic_title && !/__topic__/.test(peer) ? ` <span class="badge quiet">${esc(m.topic_title)}</span>` : "";
    // Вложение из дерева агента: канал отдаёт его байтами (`/api/media`), и в
    // ленте оно перестаёт быть строкой с путём. Голос агента приезжает так.
    // Строковый `m.media` остаётся тем, чем был: подписью telegram-вложения.
    const media = mediaBlock(m) || (m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "");
    const edited = m.edited_at ? " · ред." : "";
    // Имя в подписи — только у чужих людей в общих комнатах.
    const showName = !m.outgoing && !system && !windowish && name !== S.agentState?.owner;
    // Справа — только владелец: чужая реплика из общей комнаты — слева, с именем.
    const foreign = showName && !!S.agentState?.owner;
    const own = !m.outgoing && !system && !foreign;
    // `kind: "silence"` — серая плашка (КОНТРАКТ A→B §3): молчание по
    // решению или ход без реплики; не слово агента и не тревога.
    const silence = system && m.kind === "silence";
    const cls = own ? "own" : system ? "system" + (silence ? " silence" : "") : m.outgoing ? "agent" : "";
    const head = m.outgoing
      ? `<span class="who-hand">${esc(S.agent)}</span><span>${fmtTime(m.timestamp)}${edited}</span>`
      : `${showName ? `<b>${esc(name)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}${edited}</span>`;
    feed.push(`<div class="msg ${cls}" data-at="${esc(m.timestamp || "")}">
      <div class="msg-head">${head}</div>
      <div class="msg-body">${md(m.text || "")}${media}</div>
    </div>`);
  }
  const feedHTML = feed.join("");
  lastFeed.set(peer, feedHTML);
  while (lastFeed.size > 3) lastFeed.delete(lastFeed.keys().next().value as string);
  const notices = stubNotice() + brainNotice() + turnNotice() + failedNotices();
  const pend = pendingHTML();
  const emptyText = windowish ? "Напиши первое сообщение внизу." : "Архива этой комнаты ещё нет.";
  container.innerHTML = `<div class="center">${notices}<div class="feed">${
    feedHTML || (pend ? "" : `<div class="empty"><b>Здесь пока тихо</b>${emptyText}</div>`)
  }<div class="pending-box">${pend}</div></div></div>`;
  for (const b of container.querySelectorAll<HTMLButtonElement>("[data-go]")) {
    b.addEventListener("click", () => dispatchEvent(new CustomEvent("frame-go", { detail: b.dataset.go })));
  }
  bindStopTurn(container);
  container.scrollTop = container.scrollHeight;
  await panel.render();
}

/**
 * Две ступени, потому что действие необратимо: первое нажатие говорит цену
 * словами, второе — снимает руннера.
 */
function bindStopTurn(container: HTMLElement) {
  const box = container.querySelector<HTMLElement>("[data-turn-stop-box]");
  if (!box) return;
  const btn = box.querySelector<HTMLButtonElement>("[data-stop-turn]");
  if (!btn) return;
  btn.addEventListener("click", () => {
    if (btn.dataset.stopTurn === "ask") {
      const text = box.querySelector("span:not(.dot)");
      if (text) {
        text.textContent =
          "Прервать ход можно только перезапуском программы: агент оборвётся посреди работы. " +
          "То, что он уже успел сделать — отправленные сообщения, записанные файлы, потраченные деньги, — останется сделанным; " +
          "недоделанное он подхватит следующим ходом.";
      }
      btn.dataset.stopTurn = "do";
      btn.textContent = "Всё равно прервать";
      return;
    }
    btn.disabled = true;
    btn.textContent = "прерываю…";
    dispatchEvent(new Event("frame-restart"));
  });
}

// ---------------------------------------------------------------- живое

export function onRunEvent(runId: string) {
  panel.onRunEvent(runId);
  if (S.view !== "talk" || !root) return;
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    // За эти 700 мс владелец успевает уйти на «Файлы» — проверяем раздел снова.
    if (root && S.view === "talk") void render(root);
  }, 700);
}

export function afterSend() {
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    if (root && S.view === "talk") void render(root);
  }, 1500);
}

/** Показать в ленте сообщение, ближайшее к моменту `at` (ISO): ссылка «Открыть в чате» с карточки хода. */
export function jumpTo(at: string): boolean {
  if (!root || !at) return false;
  const want = Date.parse(at);
  if (isNaN(want)) return false;
  let best: HTMLElement | null = null;
  let gap = Infinity;
  for (const el of root.querySelectorAll<HTMLElement>(".msg[data-at]")) {
    const t = Date.parse(el.dataset.at || "");
    if (isNaN(t)) continue;
    const d = Math.abs(t - want);
    if (d < gap) { gap = d; best = el; }
  }
  if (!best || gap > 5 * 60_000) return false;
  best.scrollIntoView({ block: "center" });
  best.classList.add("flash");
  window.setTimeout(() => best?.classList.remove("flash"), 2400);
  return true;
}
