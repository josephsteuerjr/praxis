// Plans uses the same durable Forge records as the runner, never a UI store.
import { bindFail, esc, failHTML, md } from "../lib";
import { forgePanel, readForge } from "./forge-panel";
import "./forge-panel.css";
import { agendaWhen } from "../agenda-time";

const AGENDA_KIND: Record<string, string> = { wake: "будильник", window: "окно", message: "доставка", note: "записка", email: "письмо" };
interface Agenda {
  active: Array<{ id?: string; kind?: string; goal?: string; target?: string; when?: string; recur?: string; status?: string }>;
  total: number;
}
const STATUS: Record<string, string> = { active: "в работе", running: "выполняется", pending: "ожидает", scheduled: "запланировано", completed: "завершено", done: "завершено", paused: "на паузе", cancelled: "отменено", failed: "ошибка", error: "ошибка", blocked: "нужна помощь", waiting: "ожидает" };
function agendaRow(t: Agenda["active"][number]): string {
  return `<div class="row"><span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>
    ${t.when ? ` <b class="mono">${esc(agendaWhen(t.when))}</b>` : ""}${t.recur ? ` <span class="muted mono">повтор: ${esc(t.recur)}</span>` : ""}
    ${t.target ? ` <span class="muted">→ ${esc(String(t.target))}</span>` : ""}<span class="muted"> · ${esc(STATUS[t.status || ""] || t.status || "")}</span>
    <div class="muted" style="margin-top:4px">${esc((t.goal || "").slice(0, 220))}</div></div>`;
}
interface Page { shell: HTMLElement; agenda: HTMLElement; board: HTMLElement; panel: ReturnType<typeof forgePanel> }
const pages = new WeakMap<HTMLElement, Page>();
let active: { container: HTMLElement; page: Page; controller: AbortController } | undefined;

export function dispose(): void {
  active?.controller.abort(); active?.page.panel.stop(); active = undefined;
}

export async function render(container: HTMLElement): Promise<void> {
  if (active?.container === container && !active.controller.signal.aborted) { active.page.panel.start(); return; }
  dispose();
  let page = pages.get(container);
  if (!page || page.shell.parentNode !== container) {
    const shell = document.createElement("div"); shell.className = "center forge-plans";
    const forge = document.createElement("section"), agenda = document.createElement("section"), board = document.createElement("section");
    shell.append(forge, agenda, board); container.replaceChildren(shell);
    page = { shell, agenda, board, panel: forgePanel(forge) }; pages.set(container, page);
  }
  const current = { container, page, controller: new AbortController() }; active = current;
  page.panel.start();
  const [agendaR, boardR] = await Promise.allSettled([
    readForge<Agenda>("/api/agenda", current.controller.signal), readForge<{board?: string}>("/api/board", current.controller.signal),
  ]);
  if (active !== current || current.controller.signal.aborted || !container.isConnected) return;
  const card = <T>(r: PromiseSettledResult<T>, draw: (v: T) => string): string => r.status === "fulfilled" ? draw(r.value) : failHTML(r.reason, { retry: false });
  page.agenda.innerHTML = card(agendaR, agenda => `<h3 class="section-title">Намеченное себе <span class="muted">активных ${agenda.active.length} из ${agenda.total}</span></h3>
    <div class="card">${agenda.active.slice(0,40).map(agendaRow).join("") || '<div class="muted">Пока ничего не намечено.</div>'}</div>`);
  page.board.innerHTML = `<h3 class="section-title">Доска</h3>` + card(boardR, board => `<div class="card md">${md(board.board || "Доска пуста.")}</div>`);
  bindFail(container, () => { dispose(); void render(container); });
}
