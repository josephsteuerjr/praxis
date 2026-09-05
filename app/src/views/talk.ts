// Разговор: переписка комнаты в центре, ходы событиями справа, ошибки хода —
// на месте, под сообщением, человеческим словом и с действием.
import { api } from "../api";
import { bindFail, esc, failHTML, fmtAge, fmtDay, fmtN, fmtTime, humanError, md, q } from "../lib";
import { PRODUCT_NAME, S, WINDOW_ROOM, runIsLive, type Run } from "../state";

interface Msg {
  timestamp?: string;
  outgoing?: boolean;
  text?: string;
  sender_name?: string;
  // Флаг харнесса: строка — служебная плашка ПРОДУКТА, а не слово агента
  // (localharness/transport.py, `row["system"] = True`). Ставит его только сам
  // харнесс; из Telegram он прийти не может, потому что входящие строки
  // собирает botapi из полей апдейта, а этого поля там нет.
  system?: boolean;
  sender_id?: string | number;
  topic_title?: string;
  reply_to_message_id?: number;
  media?: string;
  edited_at?: string;
}

interface Turn {
  run_id: string;
  kind?: string;
  in?: string;
  out?: string;
  ts?: string | number;
  delivery?: string;
  held?: string;
  note?: string;
}

const EV_KIND: Record<string, string> = {
  chat_turn: "сообщение",
  wake: "пробуждение",
  task_window: "окно",
  moderation: "модерация",
};

let refreshTimer = 0;
let root: HTMLElement | null = null;
// Последняя удачно прочитанная лента по комнатам (не больше трёх, чтобы не
// копить сотни килобайт разметки на всю жизнь окна).
const lastFeed = new Map<string, string>();

function roomRuns(): Run[] {
  const key = S.room;
  return S.runs.filter((r) => {
    if (r.kind !== "chat_turn" || r.chat_id == null) return false;
    const k = String(r.chat_id);
    return k === key || (key === WINDOW_ROOM && k === "pult");
  });
}

/** Ходы, которые не дошли до конца — показываем на месте, под перепиской. */
function failedNotices(): string {
  // Окно времени: без него один сбой апстрима месячной давности висел красной
  // плашкой над лентой при каждом открытии Чата, пока прогон не вывалится из
  // двухсот, а счётчик рос всю жизнь установки.
  const since = Date.now() - 24 * 3600 * 1000;
  const failed = roomRuns().filter((r) => {
    if (r.terminal_status !== "failed" && r.status !== "failed") return false;
    const at = new Date(r.created_at ?? "").getTime();
    return isNaN(at) || at >= since;
  });
  if (!failed.length) return "";
  const last = failed[0];
  // Дата, когда ход не сегодняшний: «Ход 14:32» читалось как сегодняшний.
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
 * модель. Раньше last_error_raw подставлялся к ЛЮБОЙ фразе уровня error/warn, и
 * владелец читал «Не запущен · BadRequestError: 400 …» как одну беду: руннера
 * нет ИЗ-ЗА ошибки модели. Это две несвязанные вещи, склеенные точкой.
 * Признак: фраза про модель строится из той же короткой строки last_error.
 */
function brainErrorIsCurrent(): boolean {
  const s = S.agentState;
  return !!(s && s.brain && s.brain.last_error && s.phrase.includes(s.brain.last_error));
}

function brainNotice(): string {
  const s = S.agentState;
  if (!s) return "";
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
 * Идущий ход и единственный честный способ его прекратить.
 *
 * Чистой «отмены» у хода нет и не будет дешёвой ценой: `voice_turn_envelope`
 * ядра не принимает потолок итераций, а сторож, обрывающий чужой синхронный
 * вызов из другого потока, — лечение хуже болезни (рука уже могла отправить
 * письмо, записать файл, потратить деньги). Поэтому окно не притворяется, что
 * умеет «отменить», а даёт то, что действительно есть: снять руннера. Это
 * делает перезапуск программы — оболочка гасит своих детей (shell/src/main.rs,
 * `restart_self` → `kill_children`), то есть руннер умирает вместе с ходом.
 *
 * Молчания здесь быть не должно: до этой плашки у владельца, у которого ход
 * ушёл в разнос на его деньги, не было НИ ОДНОГО способа его прекратить, кроме
 * убийства процесса из диспетчера задач.
 */
function turnNotice(): string {
  const r = S.agentState?.runner;
  if (!r || !r.alive || !r.busy) return "";
  const since = r.since ? ` (идёт ${fmtAge(r.since)})` : "";
  return `<div class="notice" data-turn-stop-box>
    <span class="dot live"></span>
    <span>Агент сейчас ведёт ход${since}.</span>
    <button class="notice-action" data-stop-turn="ask" type="button">Остановить ход</button>
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
  // Сорванное чтение и пустая комната давали ОДИН результат [] — и обрыв связи
  // стирал всю переписку словами «Здесь пока тихо · Напиши первое сообщение
  // внизу». Теперь это разные вещи: пусто — только после успешного ответа.
  let rows: Msg[];
  try {
    rows = await api<Msg[]>("/api/chat/" + encodeURIComponent(peer) + "?n=250");
  } catch (e) {
    // Последняя удачно прочитанная лента комнаты. Держим её отдельно, потому что
    // #view к этому моменту уже затёрт переключателем разделов, а владельцу
    // нельзя показывать пустоту вместо его переписки.
    const kept = lastFeed.get(peer);
    const bar = `<div class="notice err read-fail"><span class="dot failed"></span>
      <span>${esc(humanError(e).text)} Показано последнее прочитанное.</span>
      <button class="notice-action" data-fail-retry type="button">Повторить</button></div>`;
    container.innerHTML = kept
      ? `<div class="center">${bar}<div class="feed">${kept}</div></div>`
      : `<div class="center">${failHTML(e)}</div>`;
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
    // Плашка продукта — по ФЛАГУ харнесса, а не по имени отправителя.
    // `sender_name` у входящих кладёт Telegram, и выбирает его сам отправитель;
    // строка ложится в архив ДО гейта (localharness/botapi.py: запись раньше
    // проверки прав). Пока здесь нюхалась строка «Hélène», любой посторонний,
    // назвавшийся так в Telegram, гарантированно клал владельцу в ленту свой
    // текст в оформлении служебной плашки самой программы — «программа просит
    // переввести ключ вот здесь» от настоящей плашки неотличимо.
    // Вторая половина условия — совместимость со СТАРЫМИ архивами, где флага
    // ещё нет (записка рождения, localharness/runner.py), и только в комнате
    // окна: туда чужой не пишет по построению.
    const system = !!m.system || (peer === WINDOW_ROOM && !m.outgoing && m.sender_name === PRODUCT_NAME);
    const name = m.outgoing ? S.agent : system ? PRODUCT_NAME : m.sender_name || String(m.sender_id ?? "");
    const topic = m.topic_title ? ` <span class="badge">${esc(m.topic_title)}</span>` : "";
    const media = m.media ? ` <span class="muted">[${esc(m.media)}]</span>` : "";
    const edited = m.edited_at ? " · ред." : "";
    // Имя в подписи — только у чужих людей в общих комнатах: в личке сторона
    // пузыря говорит сама за себя, а имя агента и так стоит на полке.
    const showName = !m.outgoing && !system && peer !== WINDOW_ROOM && name !== S.agentState?.owner;
    // Справа — только владелец. Входящее в комнате Telegram написал кто угодно
    // из участников, и «своя» сторона пузыря выдавала чужую реплику за реплику
    // владельца. Если имя владельца известно и не совпало — рисуем слева, с
    // подписью. Имя неизвестно (анатомии ещё нет) — оставляем как было.
    const foreign = showName && !!S.agentState?.owner;
    const own = !m.outgoing && !system && !foreign; // владелец — справа, агент — слева
    feed.push(`<div class="msg ${own ? "own" : ""} ${system ? "system" : ""}">
      <div class="msg-head">${showName ? `<b>${esc(name)}</b>` : ""}${topic}<span>${fmtTime(m.timestamp)}${edited}</span></div>
      <div class="msg-body">${md(m.text || "")}${media}</div>
    </div>`);
  }
  const feedHTML = feed.join("");
  lastFeed.set(peer, feedHTML);
  while (lastFeed.size > 3) lastFeed.delete(lastFeed.keys().next().value as string);
  const notices = brainNotice() + turnNotice() + failedNotices();
  const pend = pendingHTML();
  container.innerHTML = `<div class="center">${notices}<div class="feed">${
    feedHTML ||
    (pend ? "" : `<div class="empty"><b>Здесь пока тихо</b>${peer === WINDOW_ROOM ? "Напиши первое сообщение внизу." : "Архива этой комнаты ещё нет."}</div>`)
  }<div class="pending-box">${pend}</div></div></div>`;
  for (const b of container.querySelectorAll<HTMLButtonElement>("[data-go]")) {
    b.addEventListener("click", () => dispatchEvent(new CustomEvent("frame-go", { detail: b.dataset.go })));
  }
  bindStopTurn(container);
  container.scrollTop = container.scrollHeight;
  await renderPanel();
}

/**
 * Две ступени, потому что действие необратимо: первое нажатие говорит цену
 * словами, второе — снимает руннера. Кнопка «Остановить» без предупреждения
 * была бы такой же ложью, как её отсутствие.
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
    // Тот же путь, что у «Перезапустить»: под службой Windows окно честно
    // говорит, что перезапуск окна её не тронет (app/src/main.ts).
    dispatchEvent(new Event("frame-restart"));
  });
}

// ---------------------------------------------------------------- правая панель

async function renderPanel() {
  const panel = q<HTMLElement>("#panel");
  let turns: Turn[] = [];
  try {
    turns = await api<Turn[]>("/api/chat-turns/" + encodeURIComponent(S.room));
  } catch (e) {
    // «Действий ещё нет» — только когда их правда нет, а не когда не прочиталось.
    panel.innerHTML = `<div class="panel-head">Действия · ${esc(S.roomName)}</div>` + failHTML(e);
    bindFail(panel, () => void renderPanel());
    return;
  }
  const byRun = new Map(S.runs.map((r) => [r.id, r]));
  const rows = turns.slice().reverse();
  panel.innerHTML =
    `<div class="panel-head">Действия · ${esc(S.roomName)}</div>` +
    (rows
      .map((t) => {
        const run = byRun.get(t.run_id) ?? ({} as Run);
        const label =
          `${EV_KIND[run.kind || t.kind || ""] || "сообщение"} · ` +
          ((t.in || "").replace(/^[^:]{1,40}:\s*/, "").slice(0, 60) || fmtTime(run.created_at));
        const status = run.status || (t.delivery === "failed" ? "failed" : "done");
        const at = run.created_at
          ? fmtTime(run.created_at)
          : t.ts
            ? fmtTime(new Date(parseFloat(String(t.ts)) * 1000).toISOString())
            : "";
        const unspoken = t.held === "unspoken" ? ' <span class="badge">без слова</span>' : "";
        return `<div class="ev ${S.evOpen.has(t.run_id) ? "open" : ""}" data-ev="${esc(t.run_id)}">
        <div class="ev-head">
          <span class="ev-title" title="${esc(t.in || "")}">${esc(label)}${unspoken}</span>
          <span class="ev-time">${at}</span>
          <span class="dot ${runIsLive(status) ? "live" : status === "failed" ? "failed" : ""}"></span>
        </div>
        <div class="ev-steps" ${S.evOpen.has(t.run_id) ? "" : "hidden"}></div>
      </div>`;
      })
      .join("") || '<div class="empty">Действий ещё нет</div>');
  for (const el of panel.querySelectorAll<HTMLElement>(".ev")) {
    const id = el.dataset.ev!;
    el.querySelector(".ev-head")!.addEventListener("click", () => {
      if (S.evOpen.has(id)) S.evOpen.delete(id);
      else S.evOpen.add(id);
      void renderPanel();
    });
    if (S.evOpen.has(id)) void fillSteps(panel, id);
  }
}

interface RunDetail {
  manifest?: { status?: string; terminal?: { status?: string; reason?: string } };
  iterations?: Array<{
    at?: string;
    model?: string;
    ms?: number;
    text?: string;
    usage?: { in?: number; cache_read?: number; out?: number };
    tools?: Array<{ tool?: string; args?: unknown; result?: { head?: string; tail?: string } }>;
  }>;
}

async function fillSteps(panel: HTMLElement, runId: string) {
  const box = panel.querySelector<HTMLElement>(`.ev[data-ev="${CSS.escape(runId)}"] .ev-steps`);
  if (!box) return;
  let d = S.evCache.get(runId) as RunDetail | undefined;
  if (!d) {
    box.innerHTML = '<div class="muted">читаю шаги…</div>';
    try {
      d = await api<RunDetail>("/api/run/" + encodeURIComponent(runId));
    } catch (e) {
      if (!box.isConnected) return;
      box.innerHTML = `<div class="muted">шаги не прочитались: ${esc(humanError(e).text)}</div>`;
      return;
    }
    // Кэш живого хода раньше выбрасывался сразу, и каждая перерисовка панели
    // (раз в ~1.5 с во время хода) начинала с нуля. Держим как неокончательный:
    // шаги полторы секунды назад лучше вечного спиннера. Свежесть обеспечивает
    // onRunEvent — он этот кэш чистит на каждое событие хода.
    S.evCache.set(runId, d);
  }
  // Пока ждали ответ, панель могла перерисоваться целиком — узел, в который мы
  // собирались писать, уже оторван от документа, и владелец видел бы «читаю
  // шаги…» вечно, хотя данные приходили каждый цикл.
  if (!box.isConnected) return;
  const steps: string[] = [];
  for (const it of d.iterations || []) {
    const u = it.usage || {};
    steps.push(
      `<div class="ev-step"><span class="muted mono">${fmtTime(it.at)}</span> думает · ${esc(it.model || "")}` +
        `${it.ms != null ? ` · ${(it.ms / 1000).toFixed(1)} с` : ""}` +
        `${u.in != null ? ` · вход ${fmtN(u.in)}${u.cache_read ? ` (+кэш ${fmtN(u.cache_read)})` : ""} → ${fmtN(u.out || 0)}` : ""}</div>`,
    );
    for (const t of it.tools || []) {
      const args = t.args ? JSON.stringify(t.args) : "";
      const head = (t.result && (t.result.head || t.result.tail)) || "";
      steps.push(
        `<div class="ev-step"><b class="hand mono">${esc(t.tool || "?")}</b> ` +
          `${args ? `<span class="muted">${esc(args.slice(0, 70))}${args.length > 70 ? "…" : ""}</span>` : ""}` +
          `${head ? `<div class="muted">→ ${esc(String(head).slice(0, 110))}</div>` : ""}</div>`,
      );
    }
    if (it.text) {
      steps.push(`<div class="ev-step"><span class="muted">слово:</span> ${esc(it.text.slice(0, 140))}${it.text.length > 140 ? "…" : ""}</div>`);
    }
  }
  const term = d.manifest?.terminal || {};
  if (term.status) {
    steps.push(`<div class="ev-step"><b>${esc(term.status)}</b>${term.reason ? ` <span class="muted">· ${esc(String(term.reason).slice(0, 90))}</span>` : ""}</div>`);
  }
  box.innerHTML = steps.join("") || '<div class="muted">шагов нет</div>';
}

// ---------------------------------------------------------------- живое

export function onRunEvent(runId: string) {
  if (S.view !== "talk" || !root) return;
  S.evCache.delete(runId);
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    // Проверка раздела нужна и здесь, не только при постановке таймера: за эти
    // 700 мс владелец успевает уйти на «Файлы», и лента чата затирала открытый
    // экран — заголовок и полка говорят «Файлы», а на экране переписка.
    if (root && S.view === "talk") void render(root);
  }, 700);
}

export function afterSend() {
  clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    if (root && S.view === "talk") void render(root);
  }, 1500);
}
