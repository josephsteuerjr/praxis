// Планы: что агент себе наметил (будильники, окна, доставки), форж-субагенты
// и его доска. Это ответ на «что она будет делать, пока меня нет».
import { api } from "../api";
import { bindFail, esc, failHTML, md } from "../lib";

const AGENDA_KIND: Record<string, string> = {
  wake: "будильник",
  window: "окно",
  message: "доставка",
  note: "записка",
  email: "письмо",
};

interface Agenda {
  active: Array<{ id?: string; kind?: string; goal?: string; target?: string; when?: string; recur?: string; status?: string }>;
  total: number;
}

interface ForgeTask {
  id: string;
  status?: string;
  priority?: string;
  goal?: string;
  agents?: Array<{ id: string; role?: string; status?: string; error?: string; result_head?: string }>;
}

function agendaRow(t: Agenda["active"][number]): string {
  return `<div class="row">
    <span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>
    ${t.when ? ` <b class="mono">${esc(t.when)}</b>` : ""}
    ${t.recur ? ` <span class="muted mono">повтор: ${esc(t.recur)}</span>` : ""}
    ${t.target ? ` <span class="muted">→ ${esc(String(t.target))}</span>` : ""}
    <span class="muted mono"> · ${esc(t.status || "")}</span>
    <div class="muted" style="margin-top:4px">${esc((t.goal || "").slice(0, 220))}</div>
  </div>`;
}

function forgeCard(t: ForgeTask): string {
  const agents = (t.agents || [])
    .map(
      (a) => `<div class="row">
      <span class="mono">${esc(a.id)}</span> <span class="badge">${esc(a.role || "?")}</span>
      <b class="${a.status === "error" || a.status === "failed" ? "err-msg" : ""}">${esc(a.status || "")}</b>
      ${a.error ? `<span class="err-msg"> ${esc(a.error.slice(0, 120))}</span>` : ""}
      ${a.result_head ? `<div class="muted" style="font-size:12.5px">${esc(a.result_head.slice(0, 160))}</div>` : ""}
    </div>`,
    )
    .join("");
  return `<details class="fold">
    <summary><b>${esc(t.id)}</b> · ${esc(t.status || "")}${t.priority && t.priority !== "normal" ? " · " + esc(t.priority) : ""} · ${(t.agents || []).length} юнитов — ${esc((t.goal || "").slice(0, 90))}</summary>
    <div class="fold-body">${agents || '<span class="muted">юнитов нет</span>'}</div>
  </details>`;
}

export async function render(container: HTMLElement): Promise<void> {
  // Раньше здесь стоял Promise.all: падение любого из трёх чтений стирало и
  // доску, и агенду, и субагентов разом. Теперь деградация по карточкам —
  // одна не прочиталась, две остальные владелец всё равно видит.
  const [bR, agendaR, forgeR] = await Promise.allSettled([
    api<{ board?: string }>("/api/board"),
    api<Agenda>("/api/agenda"),
    api<ForgeTask[]>("/api/forge"),
  ]);
  if (bR.status === "rejected" && agendaR.status === "rejected" && forgeR.status === "rejected") {
    throw agendaR.reason; // всё разом — это отказ связи, экран отказа общий
  }
  const card = <T>(r: PromiseSettledResult<T>, draw: (v: T) => string): string =>
    r.status === "fulfilled" ? draw(r.value) : failHTML(r.reason, { retry: false });
  const agendaHTML = card(agendaR, (agenda) =>
    `<h3 class="section-title">Намеченное себе <span class="muted">активных ${agenda.active.length} из ${agenda.total}</span></h3>
    <div class="card">${agenda.active.slice(0, 40).map(agendaRow).join("") || '<div class="muted">Пока ничего не намечено.</div>'}</div>`);
  const forgeHTML = card(forgeR, (forge) => {
    const activeForge = forge.filter((t) => t.status === "active" || (t.agents || []).some((a) => a.status === "running"));
    return `<h3 class="section-title">Субагенты <span class="muted">задач ${forge.length}${activeForge.length ? " · живых " + activeForge.length : ""}</span></h3>
    ${forge.map(forgeCard).join("") || '<div class="card muted">Субагентов сейчас нет.</div>'}`;
  });
  const boardHTML = card(bR, (b) => `<div class="card md">${md(b.board || "Доска пуста.")}</div>`);
  container.innerHTML = `<div class="center">
    ${agendaHTML}
    ${forgeHTML}
    <h3 class="section-title">Доска</h3>
    ${boardHTML}
  </div>`;
  bindFail(container, () => void render(container));
}
