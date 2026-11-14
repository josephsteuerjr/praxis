import { t as uiText } from "../../../lang/lang";
// Scheduled tasks and the board are distinct from subagent execution (Now).
import { bindFail, esc, failHTML, md } from "../lib";
import { readForge } from "./forge-panel";
import { agendaWhen } from "../agenda-time";

const AGENDA_KIND: Record<string, string> = { get wake() { return uiText("ui.ui.kit.window.views.plans.601efa51bd"); }, get window() { return uiText("ui.ui.kit.window.views.plans.5943c4045e"); }, get message() { return uiText("ui.ui.kit.window.views.plans.682556de90"); }, get note() { return uiText("ui.ui.kit.window.views.plans.efc562bc9a"); }, get email() { return uiText("ui.ui.kit.window.views.plans.8f9e4e204d"); } };
interface Agenda {
  active: Array<{ id?: string; kind?: string; goal?: string; target?: string; when?: string; recur?: string; status?: string }>;
  total: number;
}
const STATUS: Record<string, string> = { get active() { return uiText("ui.ui.kit.window.views.plans.cdd22f9fd0"); }, get running() { return uiText("ui.ui.kit.window.views.plans.bd280ebe5b"); }, get pending() { return uiText("ui.ui.kit.window.views.plans.1c0ab75146"); }, get scheduled() { return uiText("ui.ui.kit.window.views.plans.94096c9f49"); }, get completed() { return uiText("ui.ui.kit.window.views.plans.5f9b6607de"); }, get done() { return uiText("ui.ui.kit.window.views.plans.5f9b6607de"); }, get paused() { return uiText("ui.ui.kit.window.views.plans.0bb2232e2e"); }, get cancelled() { return uiText("ui.ui.kit.window.views.plans.8a09723017"); }, get failed() { return uiText("ui.ui.kit.window.views.plans.44d4090ae9"); }, get error() { return uiText("ui.ui.kit.window.views.plans.44d4090ae9"); }, get blocked() { return uiText("ui.ui.kit.window.views.plans.0d48eccfa9"); }, get waiting() { return uiText("ui.ui.kit.window.views.plans.1c0ab75146"); } };
function agendaRow(t: Agenda["active"][number]): string {
  return `<div class="row"><span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>
    ${t.when ? ` <b class="mono">${esc(agendaWhen(t.when))}</b>` : ""}${t.recur ? uiText("ui.ui.kit.window.views.plans.2e7cadc42b", {p0: String(esc(t.recur))}) : ""}
    ${t.target ? ` <span class="muted">→ ${esc(String(t.target))}</span>` : ""}<span class="muted"> · ${esc(STATUS[t.status || ""] || t.status || "")}</span>
    <div class="muted" style="margin-top:4px">${esc((t.goal || "").slice(0, 220))}</div></div>`;
}
interface Page { shell: HTMLElement; agenda: HTMLElement; board: HTMLElement }
const pages = new WeakMap<HTMLElement, Page>();
let active: { container: HTMLElement; page: Page; controller: AbortController } | undefined;

export function dispose(): void {
  active?.controller.abort(); active = undefined;
}

export async function render(container: HTMLElement): Promise<void> {
  if (active?.container === container && !active.controller.signal.aborted) return;
  dispose();
  let page = pages.get(container);
  if (!page || page.shell.parentNode !== container) {
    const shell = document.createElement("div"); shell.className = "center forge-plans";
    const agenda = document.createElement("section"), board = document.createElement("section");
    shell.append(agenda, board); container.replaceChildren(shell);
    page = { shell, agenda, board }; pages.set(container, page);
  }
  const current = { container, page, controller: new AbortController() }; active = current;
  const [agendaR, boardR] = await Promise.allSettled([
    readForge<Agenda>("/api/agenda", current.controller.signal), readForge<{board?: string}>("/api/board", current.controller.signal),
  ]);
  if (active !== current || current.controller.signal.aborted || !container.isConnected) return;
  const card = <T>(r: PromiseSettledResult<T>, draw: (v: T) => string): string => r.status === "fulfilled" ? draw(r.value) : failHTML(r.reason, { retry: false });
  page.agenda.innerHTML = card(agendaR, agenda => uiText("ui.ui.kit.window.views.plans.ed884c5ca0", {p0: String(agenda.active.length), p1: String(agenda.total), p2: String(agenda.active.slice(0,40).map(agendaRow).join("") || uiText("ui.ui.kit.window.views.plans.c12e165696"))}));
  page.board.innerHTML = uiText("ui.ui.kit.window.views.plans.45ca9e7839") + card(boardR, board => `<div class="card md">${md(board.board || uiText("ui.ui.kit.window.views.plans.9df3b9ee07"))}</div>`);
  bindFail(container, () => { dispose(); void render(container); });
}
