// Чат: переписка комнаты в центре — слово агента текстом, как документ, слово
// владельца пузырём справа; ошибки хода на месте, человеческим словом и с
// действием. Ход агента — в панели справа (../panel).
//
// 28.09 (поток «окно»): лента — ключевой список (ui-kit/feed/keyed): каждое сообщение
// создаётся один раз и дальше правится на месте, новое вырастает плавно, прокрутку ведёт
// физика окна (../scroll). Раньше страница перерисовывалась `innerHTML` целиком на каждое
// событие хода — отсюда прыжки и «сообщение уходит вниз» (слово Егора 28.09). Плашки хода
// и ошибок стоят ВНИЗУ, у поля ввода: наверху 250 сообщений их никто не видел.
import { KeyedList } from "../../feed/keyed";
import { api, post } from "../api";
import { artifactCaption, mediaDescriptor, paperMediaHTML } from "../../paper-media";
import { deliveryWords, foldWords, type FoldState } from "../../memory-fold";
import { replyPreview } from "../../reply-preview";
import { STARTERS } from "./learn";
import { bindFail, esc, failHTML, fmtDay, fmtTime, humanError, md, q } from "../lib";
import * as panel from "../panel";
import { confirmedByFeed } from "../pending";
import { interruptReceiptMatches, sameRoom } from "../session";
import * as scroll from "../scroll";
import { LEGACY_WINDOW_KEY, PRODUCT_NAME, S, WINDOW_ROOM, foreignHarness, isWindowRoom, type Pending, type Run } from "../state";

interface Msg {
  source_id?: string;
  source_message_id?: string;
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
  reply_to_sender_name?: string;
  reply_to_text?: string;
  reply_to_media?: string;
  media?: string;
  /** Вложение из дерева агента: путь ОТ ДЕРЕВА и вид (transport.archive). Голос
   *  агента приезжает так; строковый `media` остаётся подписью telegram-вложения. */
  media_path?: string;
  media_kind?: string;
  media_name?: string;
  media_size?: number;
  media_mime?: string;
  media_original_path?: string;
  media_error?: string;
  edited_at?: string;
}

/** Строка ленты: день или сообщение. `html` — и содержимое, и подпись «изменилось ли». */
type Row =
  | { kind: "day"; key: string; label: string }
  | { kind: "msg"; key: string; cls: string; at: string; html: string; own: string; messageId: string };

/** Узлы страницы чата — создаются один раз на страницу (комната сменилась — страница новая). */
interface Dom {
  page: HTMLElement;
  peer: string;
  zoom: HTMLElement;
  feed: KeyedList<Row>;
  pending: KeyedList<Pending>;
  empty: HTMLElement;
  notices: HTMLElement;
  painted: boolean;
  noticesHTML: string;
  emptyHTML: string;
}

let refreshTimer = 0;
let root: HTMLElement | null = null;
const doms = new WeakMap<HTMLElement, Dom>();

function roomRuns(): Run[] {
  const key = S.room;
  return S.runs.filter((r) => {
    if (r.kind !== "chat_turn" || r.chat_id == null) return false;
    const k = String(r.chat_id);
    return k === key || (key === WINDOW_ROOM && k === LEGACY_WINDOW_KEY);
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
const readFailures = new Set<string>();
try { for (const key of JSON.parse(localStorage.getItem("helene-read-failures") || "[]")) readFailures.add(String(key)); } catch { /* storage unavailable */ }
function failedNotices(): string {
  // Окно суток: иначе один сбой месячной давности висел красной плашкой при
  // каждом открытии Чата.
  const since = Date.now() - 24 * 3600 * 1000;
  const failed = roomRuns().filter((r) => {
    if (r.terminal_status !== "failed" && r.status !== "failed") return false;
    const at = new Date(r.created_at ?? "").getTime();
    return Number.isFinite(at) && at >= since && !readFailures.has(`${S.room}:${r.id}`);
  });
  if (!failed.length) return "";
  const last = failed[0];
  const day = fmtDay(last.created_at);
  const when = (day === "Сегодня" ? "" : day + ", ") + fmtTime(last.created_at);
  const why = brainErrorIsCurrent() ? `: ${esc(S.agentState!.brain.last_error!)}` : "";
  return `<div class="notice err">
    <span class="dot failed"></span>
    <span>Ход ${esc(when)} не дошёл до конца${why}. ${failed.length > 1 ? `Таких ходов за сутки: ${failed.length}.` : ""}</span>
    <button class="notice-action" data-go="journal" data-read-failures="${esc(failed.map(r => `${S.room}:${r.id}`).join('|'))}" type="button">Открыть журнал</button>
    <button class="notice-action" data-read-failures="${esc(failed.map(r => `${S.room}:${r.id}`).join('|'))}" type="button">Прочитано</button>
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
  // Чужой харнесс (Praxis): «Не запущен» — про сердцебиение Hélène,
  // которого там нет по построению; состояние — в шапке, по вызовам модели.
  if (!s || foreignHarness()) return "";
  if (s.level === "error" || s.level === "warn") {
    const raw = brainErrorIsCurrent() ? s.brain.last_error_raw : null;
    return `<div class="notice ${s.level === "error" ? "err" : ""}">
      <span class="dot ${s.level === "error" ? "failed" : ""}"></span>
      <span>${esc(s.phrase)}</span>
      ${s.action ? `<button class="notice-action" data-act="${esc(s.action.target)}" type="button">${esc(s.action.label)}</button>` : ""}
      ${raw ? `<details class="fail-detail notice-detail"><summary>Подробности</summary><pre class="mono">${esc(raw)}</pre></details>` : ""}
    </div>`;
  }
  return "";
}

/**
 * Cooperative stop requests do not restart the engine or undo completed effects.
 */
/** Последняя квитанция остановки хода — чтобы перерисовка не возвращала «Остановить ход»
 *  поверх записанной просьбы (ревью 25.09, A7 F8). */
let stopReceipt: { key: string; text: string; pending: boolean } | null = null;
let turnControls: HTMLElement | null = null;
let controlsBlocked = false;

export function currentTurn(): { run_id: string; key: string } | null {
  const s = S.agentState;
  const r = s?.runner;
  if (!r?.alive || !r.busy || !r.run || r.run === "sleep") return null;
  if (s && "activity" in s) {
    const a = s.activity;
    if (!a || a.run_id !== r.run || a.kind !== "chat_turn"
        || !sameRoom(a.chat_id, S.room, WINDOW_ROOM, LEGACY_WINDOW_KEY)) return null;
  } else if (!roomRuns().some(run => run.id === r.run && run.status === "running")) return null;
  return { run_id: r.run, key: `${S.room}:${r.run}:${r.since}` };
}

export function interruptReceiptWords(receipt: Record<string, unknown> | null | undefined): string {
  if (!receipt || typeof receipt !== "object") return "";
  const n = (k: string) => Array.isArray(receipt[k]) ? (receipt[k] as unknown[]).length : 0;
  if (n("failed")) return "Не удалось подтвердить остановку — открой подробности хода";
  if (n("pending_tool_outcomes")) return "Останавливается — ждёт результата текущего действия";
  if (n("cancelled")) return "Ход остановлен";
  if (!n("requested")) return "Ход уже завершён";
  return "Остановка запрошена";
}

export function mountTurnControls(container: HTMLElement) {
  turnControls = container;
  container.innerHTML = '<span class="turn-receipt" aria-live="polite"></span><button class="notice-action" data-stop-turn type="button" hidden>Остановить ход</button>';
  container.querySelector("button")!.addEventListener("click", () => void stopCurrentTurn());
}

export function onStateChange(blocked = false) {
  controlsBlocked = blocked;
  const target = currentTurn();
  if (stopReceipt && stopReceipt.key !== target?.key) stopReceipt = null;
  if (turnControls) {
    const button = turnControls.querySelector<HTMLButtonElement>("button")!;
    button.hidden = blocked || !target;
    button.disabled = !!stopReceipt?.pending;
    button.textContent = stopReceipt?.pending ? "Останавливается…" : "Остановить ход";
    button.title = "Остановить этот ход; уже выполненные действия сохранятся";
    const receipt = turnControls.querySelector<HTMLElement>(".turn-receipt")!;
    receipt.textContent = !blocked && target ? stopReceipt?.text || "" : "";
  }
  if (root) {
    const dom = doms.get(root);
    if (dom) paintNotices(dom, stubNotice() + brainNotice() + failedNotices());
  }
}

async function stopCurrentTurn() {
  const target = currentTurn();
  if (!target || controlsBlocked || stopReceipt?.pending) return;
  const record = { key: target.key, text: "Остановка запрошена…", pending: true };
  stopReceipt = record;
  onStateChange(controlsBlocked);
  try {
    const answer = await post<{ ok: boolean; note?: string; request?: { id: string } }>("/api/interrupt", { scope: target.run_id });
    if (!answer.ok) throw new Error(answer.note || "Просьба не записана");
    if (stopReceipt !== record) return;
    const requestId = answer.request?.id;
    if (!requestId) throw new Error("Запрос принят, но подтверждение остановки пока недоступно");
    for (let i = 0; i < 15; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (stopReceipt !== record) return;
      try {
        const sup = await api<{ interrupt_receipt?: Record<string, unknown> }>("/api/supervisor");
        const receipt = sup?.interrupt_receipt;
        if (!interruptReceiptMatches(requestId, receipt)) continue;
        if (stopReceipt !== record || currentTurn()?.key !== target.key) return;
        record.text = interruptReceiptWords(receipt);
        record.pending = Array.isArray(receipt?.pending_tool_outcomes) && receipt.pending_tool_outcomes.length > 0;
        onStateChange(controlsBlocked);
        return;
      } catch { /* Connection can disappear while owned processes stop. */ }
    }
    if (stopReceipt === record) {
      record.text = "Остановка ещё не подтверждена";
      record.pending = false;
      onStateChange(controlsBlocked);
    }
  } catch (error) {
    if (stopReceipt !== record) return;
    record.text = humanError(error).text;
    record.pending = false;
    onStateChange(controlsBlocked);
  }
}

function stubNotice(): string {
  const room = S.rooms.find((r) => r.key === S.room);
  if (!room?.stub) return "";
  return `<div class="notice">
    <span class="dot"></span>
    <span>Этот чат — заглушка: код агента ещё не умеет несколько чатов с агентом (появится в 0.3.3). Здесь можно проверить, как это будет выглядеть; писать — в основной чат.</span>
  </div>`;
}

/**
 * Вложение строки ленты — проигрывателем или картинкой.
 *
 * Bytes use the authenticated artifact client; loading and playback are limited
 * to figures near the viewport. Saving keeps the original Telegram file.
 */
function mediaBlock(m: Msg): string {
  const rel = String(m.media_path || "").trim();
  if (!rel) return "";
  return paperMediaHTML(mediaDescriptor(m)!);
}

// ---------------------------------------------------------------- строки ленты

/** Строки ленты: дни и сообщения с устойчивыми ключами (время + кто + номер дубля). */
function buildRows(rows: Msg[], peer: string): Row[] {
  const originals = new Map(rows.filter(m => m.source_message_id).map(m => [String(m.source_message_id), m]));
  const windowish = isWindowRoom(peer);
  const out: Row[] = [];
  const seen = new Map<string, number>();
  let day = "";
  for (const m of rows) {
    const d = fmtDay(m.timestamp);
    if (d !== day) {
      out.push({ kind: "day", key: "day:" + (m.timestamp || "").slice(0, 10) + ":" + d, label: d });
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
    const media = mediaBlock(m) || (m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "") || (m.media_error ? `<p class="muted">${esc(m.media_error)}</p>` : '');
    const edited = m.edited_at ? " · ред." : "";
    // Имя в подписи — только у чужих людей в общих комнатах.
    const showName = !m.outgoing && !system && !windowish && name !== S.agentState?.owner;
    // Справа — только владелец: чужая реплика из общей комнаты — слева, с именем.
    const foreign = showName && !!S.agentState?.owner;
    const own = !m.outgoing && !system && !foreign;
    // `kind: "silence"` — серая плашка (КОНТРАКТ A→B §3): молчание по
    // решению или ход без реплики; не слово агента и не тревога.
    const silence = system && m.kind === "silence";
    // Записка первого запуска (1.2.3): агенту она нужна целиком, человеку — нет. Серой
    // плашкой одной строкой, полный текст — по щелчку. Старые установки клали её без
    // `kind`: узнаём по началу текста.
    const birth = system && (m.kind === "birth" || (m.text || "").startsWith("Это твой первый запуск"));
    const cls = own ? "own" : system ? "system" + (silence || birth ? " silence" : "") : m.outgoing ? "agent" : "";
    const preview = replyPreview(m, originals);
    const reply = preview ? `<button type="button" class="msg-reply-preview" data-reply-id="${esc(preview.id)}"
      title="Перейти к исходному сообщению"><b>↳ ${esc(preview.author)}</b><span>${esc(preview.text)}</span></button>` : '';
    const head = m.outgoing
      ? `<span class="who-hand">${esc(S.agent)}</span><span>${fmtTime(m.timestamp)}${edited}</span>`
      : `${showName ? `<b>${esc(name)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}${edited}</span>`;
    const visibleText = artifactCaption(m.text || "", m.media_path);
    const body = birth
      ? `<details><summary>Первый запуск: ${esc(PRODUCT_NAME)} рассказала агенту, кто он, где его дом и кто владелец</summary>${md(m.text || "")}</details>`
      : md(visibleText);
    const base = `${m.timestamp || ""}|${m.outgoing ? "a" : system ? "s" : "o"}|${m.sender_id ?? ""}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    out.push({
      kind: "msg",
      key: n ? `${base}#${n}` : base,
      cls,
      at: m.timestamp || "",
      messageId: String(m.source_message_id || ""),
      html: `<div class="msg-head">${head}</div>${reply}<div class="msg-body">${body}${media}</div>`,
      own: own ? (m.text || "").trim() : "",
    });
  }
  return out;
}

function makeRow(r: Row): HTMLElement {
  const el = document.createElement("div");
  if (r.kind === "day") {
    el.className = "day";
    el.textContent = r.label;
  } else {
    el.className = `msg ${r.cls}`;
    el.dataset.at = r.at;
    el.dataset.messageId = r.messageId;
    el.innerHTML = r.html;
  }
  return el;
}

function pendingEl(p: Pending): HTMLElement {
  const el = document.createElement("div");
  el.className = "msg own pending";
  el.dataset.pending = String(p.id);
  el.innerHTML = `<div class="msg-head"><span>${fmtTime(p.at)}</span></div>
      <div class="msg-body">${md(p.text)}</div>
      <div class="msg-note">${esc(p.note)}</div>`;
  return el;
}

function skeleton(page: HTMLElement, peer: string): Dom {
  page.addEventListener("click", event => {
    const link = (event.target as Element).closest<HTMLElement>("[data-reply-id]");
    if (!link) return;
    const target = Array.from(page.querySelectorAll<HTMLElement>(".msg[data-message-id]"))
      .find(row => row.dataset.messageId === link.dataset.replyId);
    if (target) { showMessage(target); return; }
    link.title = "Исходное сообщение ещё не загружено. Открой более раннюю часть переписки.";
    let feedback = link.parentElement?.querySelector<HTMLElement>(".reply-feedback");
    if (!feedback) { feedback = document.createElement("p"); feedback.className = "receipt reply-feedback"; link.after(feedback); }
    feedback.textContent = link.title;
  });
  page.innerHTML = `<div class="center talk">
    <div class="talk-zoom">
      <div class="feed" role="log" aria-label="Переписка"></div>
      <div class="pending-box"></div>
      <div class="talk-empty" hidden></div>
      <div class="talk-foot"><div data-fold-box></div><div class="talk-notices"></div></div>
    </div>
  </div>`;
  const zoom = page.querySelector<HTMLElement>(".talk-zoom")!;
  const dom: Dom = {
    page,
    peer,
    zoom,
    feed: new KeyedList<Row>(page.querySelector<HTMLElement>(".feed")!, {
      key: (r) => r.key,
      create: makeRow,
      same: (a, b) => (a.kind === "day" ? b.kind === "day" && a.label === b.label : b.kind === "msg" && a.html === b.html && a.cls === b.cls && a.messageId === b.messageId),
      update(el, r) {
        if (r.kind === "day") { el.textContent = r.label; return; }
        el.className = `msg ${r.cls}`;
        el.innerHTML = r.html;
        el.dataset.messageId = r.messageId;
        el.dataset.at = r.at;
      },
    }),
    pending: new KeyedList<Pending>(page.querySelector<HTMLElement>(".pending-box")!, {
      key: (p) => "p" + p.id,
      create: pendingEl,
      same: (a, b) => a.note === b.note && a.state === b.state,
      update(el, p) {
        const note = el.querySelector(".msg-note");
        if (note) note.textContent = p.note;
      },
    }),
    empty: page.querySelector<HTMLElement>(".talk-empty")!,
    notices: page.querySelector<HTMLElement>(".talk-notices")!,
    painted: false,
    noticesHTML: "",
    emptyHTML: "",
  };
  doms.set(page, dom);
  return dom;
}

// ---------------------------------------------------------------- отрисовка

/** Перерисовать только пузыри отправляемого, не трогая ленту. */
export function paintPending() {
  dispatchEvent(new Event("frame-pending"));
  if (!root || S.view !== "talk") return;
  const dom = doms.get(root);
  if (!dom) return;
  scroll.preserve(() => dom.pending.set(S.pending.filter((p) => p.room === S.room), { animate: true }));
  paintEmpty(dom, dom.feed.size > 0);
}

function paintEmpty(dom: Dom, hasRows: boolean) {
  const pend = S.pending.some((p) => p.room === S.room);
  const show = !hasRows && !pend;
  dom.empty.hidden = !show;
  if (!show) return;
  const windowish = isWindowRoom(dom.peer);
  const emptyText = windowish ? "Напиши первое сообщение внизу." : "Архива этой комнаты ещё нет.";
  // Четыре начала — только в пустой переписке с агентом этого окна, и только
  // пока она пуста: с первым же сообщением полоска уходит навсегда. Тому, кто
  // уже написал, подсказки «с чего начать» не нужны, а место они занимают ровно
  // там, где идёт разговор.
  const starters = windowish
    ? `<div class="starters">${STARTERS.map((st, i) =>
        `<button class="starter" type="button" data-starter="${i}">${esc(st.label)}</button>`).join("")}</div>`
    : "";
  const html = `<div class="empty"><b>Здесь пока тихо</b>${emptyText}${starters}</div>`;
  if (html === dom.emptyHTML) return;
  dom.emptyHTML = html;
  dom.empty.innerHTML = html;
  for (const b of dom.empty.querySelectorAll<HTMLButtonElement>("[data-starter]")) {
    b.addEventListener("click", () => {
      const st = STARTERS[Number(b.dataset.starter)];
      if (st) dispatchEvent(new CustomEvent("frame-template", { detail: st.template }));
    });
  }
}

/** Плашки внизу ленты: переписываются, только если слова изменились, и не посреди «прервать?». */
function paintNotices(dom: Dom, html: string) {
  if (html === dom.noticesHTML) return;
  dom.noticesHTML = html;
  scroll.preserve(() => { dom.notices.innerHTML = html; });
  for (const b of dom.notices.querySelectorAll<HTMLButtonElement>("[data-go]")) {
    b.addEventListener("click", () => dispatchEvent(new CustomEvent("frame-go", { detail: b.dataset.go })));
  }
  // ⚠ ЖИВОЙ СЛУЧАЙ 17.09. Кнопка плашки состояния несёт ЦЕЛЬ ДЕЙСТВИЯ, а не имя раздела:
  // харнесс присылает `settings` или `restart`. Она стояла среди `[data-go]`, и «Перезапустить»
  // в переписке открывало раздел «restart», которого нет: вместо перезапуска агента —
  // пустой экран с «Не получилось» и `reading 'render'` в подробностях. Кнопка состояния
  // в шапке разбирала те же цели правильно — расходились ровно здесь.
  for (const b of dom.notices.querySelectorAll<HTMLButtonElement>("[data-act]")) {
    b.addEventListener("click", () => {
      if (b.dataset.act === "restart") dispatchEvent(new Event("frame-restart"));
      else dispatchEvent(new CustomEvent("frame-go", { detail: b.dataset.act }));
    });
  }
  for (const b of dom.notices.querySelectorAll<HTMLButtonElement>("[data-read-failures]")) {
    b.addEventListener("click", () => {
      for (const key of (b.dataset.readFailures || "").split('|')) if (key) readFailures.add(key);
      while (readFailures.size > 500) readFailures.delete(readFailures.values().next().value!);
      try { localStorage.setItem("helene-read-failures", JSON.stringify([...readFailures])); } catch { /* in-memory acknowledgement stays */ }
      if (root) void render(root);
    });
  }
  for (const b of dom.notices.querySelectorAll("[data-live-runs]")) b.addEventListener("click", () => dispatchEvent(new Event("frame-live-runs")));
  bindFail(dom.notices, () => { if (root) void render(root); });

}

export async function render(container: HTMLElement): Promise<void> {
  root = container;
  const title = q<HTMLElement>("#head-title");
  title.textContent = S.roomName;
  // Над своей комнатой агент подписан своим почерком; чужие комнаты — нет.
  title.classList.toggle("hand", S.room === WINDOW_ROOM);
  dispatchEvent(new Event("frame-room"));
  const peer = S.room;
  let dom = doms.get(container);
  if (!dom || dom.peer !== peer) dom = skeleton(container, peer);
  if (S.view === "talk") scroll.sectionShown(true, dom.zoom);
  // Сорванное чтение и пустая комната давали ОДИН результат [] — и обрыв связи
  // стирал всю переписку. Теперь пусто — только после успешного ответа, а при обрыве
  // лента остаётся той, что была (её узлы никуда не деваются).
  let rows: Msg[];
  try {
    rows = await readArchive(peer);
  } catch (e) {
    if (!dom.painted) {
      dom.empty.hidden = false;
      dom.emptyHTML = "";
      dom.empty.innerHTML = failHTML(e);
      bindFail(dom.empty, () => void render(container));
      return;
    }
    paintNotices(dom, `<div class="notice err read-fail"><span class="dot failed"></span>
      <span>${esc(humanError(e).text)} Показано последнее прочитанное.</span>
      <button class="notice-action" data-fail-retry type="button">Повторить</button></div>`);
    return;
  }
  if (doms.get(container) !== dom || S.room !== peer) return; // комната сменилась, пока читали
  // Подтверждённые лентой пузыри «отправляется…» снимаем — и их строки ленты встают
  // на место пузыря без роста: подмена одного на другое не должна мигать.
  const own = rows.filter((m) => !m.outgoing);
  const confirmed = new Set<string>();
  if (S.pending.length) {
    // Голосовое и картинка лентой несут не заглушку пузыря, а «[голосовое]: расшифровка»
    // — правило сверки в ui-kit/window/pending.ts (26.09, пузыри висели до перезапуска).
    S.pending = S.pending.filter((p) => {
      if (p.room !== peer || !confirmedByFeed(p, own)) return true;
      confirmed.add(p.text.trim());
      return false;
    });
  }
  dispatchEvent(new Event("frame-pending"));
  const list = buildRows(rows, peer);
  const d = dom;
  const firstPaint = !d.painted;
  scroll.preserve(() => {
    d.feed.set(list, {
      animate: d.painted,
      still: (el) => confirmed.size > 0 && el.classList.contains("own") && confirmed.has((el.querySelector(".msg-body")?.textContent || "").trim()),
    });
    d.pending.set(S.pending.filter((p) => p.room === peer), { animate: d.painted });
  });
  d.painted = true;
  paintEmpty(d, list.length > 0);
  paintNotices(d, stubNotice() + brainNotice() + failedNotices());
  // История пришла после начального homeScroll на пустой странице. Ставим
  // низ до первого кадра с сообщениями; последующие обновления следуют плавно.
  if (firstPaint && S.view === "talk" && container.isConnected) scroll.view()?.toBottom(false);
  void paintFold(container, peer);
  // Прокрутку ведёт каркас (`homeScroll`) и физика окна: прилипшая лента сама едет к новому.
  await panel.render();
}

// ---------------------------------------------------------------- свёртка по кнопке

let foldTimer = 0;

/**
 * Строка «память чата» у низа ленты (Егор 27.09: «компактирование… неплохо бы по нажатию»).
 * Число горячих сообщений видно и во время свёртки, и после неё.
 * Объяснение следует политике загруженного ядра, архив — отдельное число.
 */
async function paintFold(container: HTMLElement, room: string): Promise<void> {
  const box = container.querySelector<HTMLElement>("[data-fold-box]");
  if (!box) return;
  let st: FoldState;
  try {
    st = await api<FoldState>(`/api/memory-fold/${encodeURIComponent(room)}`);
  } catch {
    if (box.isConnected && S.room === room) {
      box.textContent = "Счётчик горячей памяти пока недоступен.";
      delete box.dataset.html;
      clearTimeout(foldTimer);
      foldTimer = window.setTimeout(() => {
        if (S.view === "talk" && S.room === room && box.isConnected) void paintFold(container, room);
      }, 3000);
    }
    return;
  }
  if (!box.isConnected || S.room !== room) return;
  const words = foldWords(st);
  clearTimeout(foldTimer);
  foldTimer = window.setTimeout(() => {
    if (S.view === "talk" && S.room === room && box.isConnected) void paintFold(container, room);
  }, 3000);
  const delivery = deliveryWords(st);
  const html = `${delivery ? `<div class="notice"><span>${esc(delivery)}</span></div>` : ''}<div class="memory-fold"><div class="memory-fold-head"><strong>${esc(words.counter)}</strong>
    ${words.canFold ? '<button class="notice-action" data-fold type="button">Свернуть сейчас</button>' : ''}</div>
    <details><summary>Как работает память</summary><p>${esc(words.explanation)}</p></details>
    ${words.note ? `<p class="receipt${words.state === "failed" ? " err" : ""}" data-fold-note>${esc(words.note)}</p>` : ''}</div>`;
  if (box.dataset.html === html) return;
  box.dataset.html = html;
  const expanded = box.querySelector("details")?.open;
  scroll.preserve(() => {
    box.innerHTML = html;
    if (expanded) box.querySelector("details")!.open = true;
  });
  const btn = box.querySelector<HTMLButtonElement>("[data-fold]");
  btn?.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "прошу…";
    try {
      const said = await post<{ ok: boolean; note?: string }>("/api/memory-fold", { room });
      if (!said.ok) throw new Error(said.note || "просьба не записана");
      void paintFold(container, room);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Свернуть сейчас";
      let text = box.querySelector<HTMLElement>("[data-fold-note]");
      if (!text) { text = document.createElement("p"); text.className = "receipt err"; box.querySelector(".memory-fold")?.append(text); }
      text.textContent = humanError(e).text;
    }
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
  // Своё отправленное — всегда к низу: ответ придёт туда.
  scroll.view()?.toBottom(true);
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
  showMessage(best);
  return true;
}

function showMessage(best: HTMLElement): void {
  const sc = scroll.view();
  if (sc) {
    const r = best.getBoundingClientRect();
    const v = sc.el.getBoundingClientRect();
    sc.scrollTo(sc.el.scrollTop + r.top - v.top - (v.height - r.height) / 2);
  } else best.scrollIntoView({ block: "center" });
  best.classList.add("flash");
  window.setTimeout(() => best?.classList.remove("flash"), 2400);
}
