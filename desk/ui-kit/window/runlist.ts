// Список прогонов с раскрывающимися шагами — общий для «Сейчас» и «Вейков».
// Подпись хода — слово агента из chat-turns комнаты (без скобок кадра),
// без него — первая строка цели прогона.
import { api } from "./api";
import { esc, fmtDay, fmtTime, humanError } from "./lib";
import { stepsHTML, type RunDetail } from "../steps";
import { LEGACY_WINDOW_KEY, S, WINDOW_ROOM, runIsLive, type Run } from "./state";

export const RUN_KIND: Record<string, string> = {
  chat_turn: "сообщение",
  wake: "пробуждение",
  task_window: "окно задачи",
  moderation: "модерация",
};

/** run_id → слово агента, заметка границы и собеседник (из chat-turns комнат). */
const words = new Map<string, { out: string; note: string; who: string }>();
const wordsLoaded = new Map<string, number>();

export const cleanLabel = (s: string) =>
  s
    .replace(/^\s*…?\s*\[[^\]]{0,200}\]\s*/, "")
    .replace(/^[^:\n]{1,40}:\s*/, "")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();

/** Цель запуска годится в подпись, только если это не обрезок кадра. */
function goalLabel(goal: string): string {
  const g = String(goal || "");
  if (/ЛЕНТА ОБРЕЗАНА|^\s*…|^\s*\[/.test(g)) return "";
  const t = cleanLabel(g);
  return /[\]»]$/.test(t) && t.length > 60 ? "" : t;
}

/** Заметка границы хода (`done: …`, `wait: …`) — словами. */
function noteLabel(note: string): string {
  const n = String(note || "").trim();
  const m = n.match(/^(done|wait|blocked|silence)\s*:\s*(.*)$/i);
  const body = m ? m[2] : n;
  const head = m ? (m[1].toLowerCase() === "wait" ? "Жду: " : m[1].toLowerCase() === "blocked" ? "Препятствие: " : "") : "";
  return body.trim() ? head + cleanLabel(body) : "";
}

export function roomKeyOf(r: Run): string {
  const k = String(r.chat_id ?? "");
  return k === LEGACY_WINDOW_KEY ? WINDOW_ROOM : k;
}

/** Слова агента для подписей: chat-turns комнат последних запусков (не чаще раза в 20 с на комнату). */
export async function loadWords(list: Run[], maxRooms = 6): Promise<void> {
  const keys = [...new Set(list.filter((r) => r.chat_id != null).map(roomKeyOf))].slice(0, maxRooms);
  const now = Date.now();
  await Promise.all(
    keys
      .filter((k) => now - (wordsLoaded.get(k) || 0) > 20_000)
      .map(async (k) => {
        wordsLoaded.set(k, now);
        try {
          const turns = await api<Array<{ run_id: string; out?: string; note?: string; who?: string }>>("/api/chat-turns/" + encodeURIComponent(k) + "?n=60");
          for (const t of turns || []) if (t.run_id) words.set(t.run_id, { out: t.out || "", note: t.note || "", who: t.who || "" });
        } catch {
          // подписи останутся по цели запуска
        }
      }),
  );
}

export function wordOf(runId: string): { out: string; note: string; who: string } | undefined {
  return words.get(runId);
}

/** Подпись: слово агента → заметка границы → цель (если не обрезок кадра) → вид и время. */
export function runLabel(r: Run): string {
  const w = words.get(r.id);
  const text = cleanLabel(w?.out || "") || noteLabel(w?.note || "") || goalLabel(r.goal_head || "");
  return text || `${RUN_KIND[r.kind] || r.kind} · ${fmtTime(r.created_at)}`;
}

export function runRowHTML(r: Run, opts: { showRoom?: boolean } = {}): string {
  const status = r.terminal_status || r.status || "";
  const dot = runIsLive(r.status, r) ? "live" : status === "failed" ? "failed" : "";
  const who = (words.get(r.id)?.who || "").trim();
  const day = fmtDay(r.created_at);
  const roomTitle = opts.showRoom && r.chat_title ? r.chat_title : "";
  // В личке собеседник и комната — одно имя: не повторять.
  const sub = [RUN_KIND[r.kind] || r.kind, roomTitle, who && who !== roomTitle ? who : ""].filter(Boolean).join(" · ");
  return `<div class="ev ${S.evOpen.has(r.id) ? "open" : ""}" data-ev="${esc(r.id)}">
    <button type="button" class="ev-head" aria-expanded="${S.evOpen.has(r.id)}" aria-controls="run-${esc(r.id)}">
      <span class="ev-title" title="${esc(r.goal_head || "")}">${esc(runLabel(r))}</span>
      <span class="ev-time">${day === "Сегодня" ? "" : esc(day) + " "}${fmtTime(r.created_at)}</span>
      <span class="dot ${dot}"></span>
      <span class="ev-chevron" aria-hidden="true">›</span>
    </button>
    ${sub ? `<div class="ev-sub">${esc(sub)}${opts.showRoom && r.chat_id != null && r.chat_title ? ` · <a href="#" data-room="${esc(roomKeyOf(r))}" data-room-name="${esc(r.chat_title)}">открыть чат</a>` : ""}</div>` : ""}
    <div class="ev-steps" id="run-${esc(r.id)}" ${S.evOpen.has(r.id) ? "" : "hidden"}></div>
  </div>`;
}

export async function runDetail(runId: string, fresh = false): Promise<RunDetail | undefined> {
  if (!fresh) {
    const cached = S.evCache.get(runId) as RunDetail | undefined;
    if (cached) return cached;
  }
  const d = await api<RunDetail>("/api/run/" + encodeURIComponent(runId));
  S.evCache.set(runId, d);
  return d;
}

/** Оживить раскрывающиеся ходы и ссылки «открыть чат» внутри узла. */
export function bindRuns(box: HTMLElement, _redraw: () => void, openRoom: (key: string, name: string) => void) {
  for (const node of box.querySelectorAll<HTMLElement>(".ev[data-ev]")) {
    const id = node.dataset.ev!;
    const head = node.querySelector<HTMLButtonElement>(".ev-head")!;
    const steps = node.querySelector<HTMLElement>(".ev-steps")!;
    const load = () => {
      steps.innerHTML = '<div class="muted">читаю шаги…</div>';
      void runDetail(id).then(
        (d) => {
          if (steps.isConnected) steps.innerHTML = d ? stepsHTML(d) : '<div class="muted">шагов нет</div>';
        },
        (e) => {
          if (steps.isConnected) steps.innerHTML = `<div class="muted">шаги не прочитались: ${esc(humanError(e).text)}</div>`;
        },
      );
    };
    head.addEventListener("click", () => {
      const open = !S.evOpen.has(id);
      if (open) S.evOpen.add(id); else S.evOpen.delete(id);
      node.classList.toggle("open", open);
      head.setAttribute("aria-expanded", String(open));
      steps.hidden = !open;
      if (open) load();
    });
    if (S.evOpen.has(id)) load();
  }
  for (const a of box.querySelectorAll<HTMLElement>("[data-room]")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openRoom(a.dataset.room!, a.dataset.roomName || a.dataset.room!);
    });
  }
}
