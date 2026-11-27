// «Задачи»: намеченное (будильники, окна, отложенные доставки), её доска и субагенты Forge.
// Субагенты — её же панель (forgePanel): команды остановки и восстановления доступны,
// потому что это окно владельца, а не телефон.
import { forgePanel } from "../../../ui-kit/window/views/forge-panel";
import { esc, fmtDay, md, fmtTime } from "../../../ui-kit/text";
import { agentState, el, failNode, gate } from "../core";

interface Agenda { active: Array<{ id?: string; kind?: string; goal?: string; target?: string; when?: string; recur?: string; status?: string }>; total: number }
const KIND: Record<string, string> = { wake: "будильник", window: "окно", message: "доставка", note: "записка", email: "письмо" };
const TICK = 20_000;

export function mount(root: HTMLElement): void {
  // Её канал через ворота раздела: скрытый раздел не опрашивает сервер, показанный — перечитывает сразу.
  const g = gate(root);
  const { api, request } = g;
  root.classList.add("tasks");
  const left = el("section", "task-pane");
  const agendaBox = el("div");
  const right = el("section", "task-pane");
  const boardBox = el("div");
  left.append(el("div", "top-kicker", "Намеченное"), el("h2", "section-title", "Что она себе назначила"), agendaBox);
  right.append(el("div", "top-kicker", "Доска"), el("h2", "section-title", "Её рабочая доска"), boardBox);
  const top = el("div", "grid2 tasks-top");
  top.append(left, right);
  const forge = el("section", "tasks-forge");
  forge.append(el("div", "top-kicker", "Субагенты"), el("h2", "section-title", "Задачи Forge и их исполнители"));
  root.append(top, forge);

  const agendaNote = el("p", "muted");
  const boardPaper = el("article", "card md");
  left.insertBefore(agendaNote, agendaBox);
  right.insertBefore(boardPaper, boardBox);
  boardBox.remove();
  let last = "";

  async function load() {
    const [agenda, board] = await Promise.allSettled([api<Agenda>("/api/agenda"), api<{ board?: string }>("/api/board")]);
    const fresh = agenda.status === "fulfilled" ? agenda.value : null;
    const text = board.status === "fulfilled" ? board.value.board || "" : null;
    const signature = JSON.stringify([fresh?.active.map((item) => `${item.id ?? ""}:${item.status ?? ""}`), text?.length]);
    if (signature === last) return;
    last = signature;
    // ---- намеченное
    agendaBox.replaceChildren();
    if (fresh) {
      agendaNote.textContent = `активных ${fresh.active.length} из ${fresh.total} записей`;
      if (!fresh.active.length) agendaBox.append(el("div", "empty", "Пока ничего не намечено."));
      for (const item of fresh.active.slice(0, 80)) {
        const row = el("div", "task-row");
        const when = item.when ? new Date(item.when) : null;
        const stamp = item.when ? (Number.isNaN(when?.getTime()) ? item.when : `${fmtDay(item.when)}, ${fmtTime(item.when)}`) : "";
        row.innerHTML = `<div class="task-head"><span class="badge">${esc(KIND[item.kind || ""] || item.kind || "?")}</span>${stamp ? ` <b class="mono">${esc(stamp)}</b>` : ""}${item.recur ? ` <span class="muted mono">повтор: ${esc(item.recur)}</span>` : ""}${item.status ? ` <span class="muted"> · ${esc(item.status)}</span>` : ""}</div>${item.target ? `<div class="muted">→ ${esc(String(item.target))}</div>` : ""}<div class="task-goal">${esc((item.goal || "").slice(0, 400))}</div>`;
        agendaBox.append(row);
      }
    } else {
      agendaNote.textContent = "Намеченное не прочиталось.";
      agendaBox.replaceChildren(failNode(agenda.status === "rejected" ? agenda.reason : new Error("Канал не ответил."), () => { last = ""; void load(); }));
    }
    // ---- доска
    if (text !== null) {
      boardBox.replaceChildren();
      boardPaper.innerHTML = md(text || "Доска пуста.");
      boardPaper.hidden = false;
    } else {
      boardPaper.hidden = true;
      boardBox.replaceChildren(failNode(board.status === "rejected" ? board.reason : new Error("Канал не ответил."), () => { last = ""; void load(); }));
    }
  }

  // Её панель Forge ходит по тем же записям, что и телефон: окно лишь снимает «только читать».
  // Читает она через ворота раздела: скрытая панель ждёт, показанная — опрашивает сразу.
  const panel = forgePanel(forge, async <T>(path: string, signal: AbortSignal, body?: unknown) =>
    request<T>(path.replace("/api/forge/", "/api/phone/forge/"), body, signal), 3000,
    { readOnly: false, recovery: true, scope: () => location.origin + "\n" + (agentState()?.agent ?? "") });
  panel.start();

  const tick = async () => {
    if (!document.hidden) await load();
    g.every(() => void tick(), TICK);
  };
  void tick();
}
