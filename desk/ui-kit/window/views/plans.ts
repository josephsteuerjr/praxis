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
  agents?: Array<{
    id: string; role?: string; status?: string; request_status?: string;
    custody?: "result" | "unknown" | "unresolved_stop";
    parent_unit_id?: string; depth?: number; ancestry_unknown?: boolean;
    result_sha256?: string; stale_dispositions?: number;
    dispositions?: Array<{ parent_unit_id: string; disposition: string; result_sha256: string }>;
  }>;
}

const STATUS: Record<string, string> = { active: "в работе", running: "выполняется", pending: "ожидает", scheduled: "запланировано", completed: "завершено", done: "завершено", paused: "на паузе", cancelled: "отменено", failed: "ошибка", error: "ошибка", blocked: "нужна помощь", waiting: "ожидает" };
const statusLabel = (s = "") => STATUS[s] || s;
const DISPOSITION: Record<string, string> = { integrated: "принят", irrelevant: "не нужен", deferred: "отложен" };

function agendaRow(t: Agenda["active"][number]): string {
  return `<div class="row">
    <span class="badge">${esc(AGENDA_KIND[t.kind || ""] || t.kind || "?")}</span>
    ${t.when ? ` <b class="mono">${esc(t.when)}</b>` : ""}
    ${t.recur ? ` <span class="muted mono">повтор: ${esc(t.recur)}</span>` : ""}
    ${t.target ? ` <span class="muted">→ ${esc(String(t.target))}</span>` : ""}
    <span class="muted"> · ${esc(statusLabel(t.status))}</span>
    <div class="muted" style="margin-top:4px">${esc((t.goal || "").slice(0, 220))}</div>
  </div>`;
}

function forgeCard(t: ForgeTask): string {
  const agents = (t.agents || [])
    .map(
      (a) => `<div class="row" style="margin-left:${Math.min(Math.max(a.depth || 0, 0), 8) * 16}px">
      <span class="mono">${esc(a.id)}</span> <span class="badge">${esc(a.role || "?")}</span>
      <b class="${a.status === "error" || a.status === "failed" || a.status === "unknown" ? "err-msg" : ""}">${esc(a.status === "unknown" ? "состояние не подтверждено" : statusLabel(a.status))}</b>
      <div class="muted" style="font-size:12.5px">родитель: <span class="mono">${esc(a.parent_unit_id || "корень")}</span>${a.ancestry_unknown ? ' · <span class="err-msg">связь с родителем не подтверждена</span>' : ""}
        ${a.custody === "unresolved_stop" ? ' · <span class="err-msg">остановка не подтверждена</span>' : a.custody === "unknown" ? ` · запрос ${esc(a.request_status || "?")}, результата нет; живость неизвестна` : ""}</div>
      ${a.result_sha256 ? `<div class="muted" style="font-size:12.5px;overflow-wrap:anywhere">SHA-256 результата: <code>${esc(a.result_sha256)}</code> · приёмка: ${a.dispositions?.length ? a.dispositions.map((r) => esc(DISPOSITION[r.disposition] || r.disposition)).join(", ") : "не записана"}</div>` : ""}
      ${a.stale_dispositions ? `<div class="err-msg">${a.stale_dispositions} расписок приёмки не совпало с текущим результатом/родителем</div>` : ""}
    </div>`,
    )
    .join("");
  return `<details class="fold">
    <summary><b>${esc((t.goal || t.id).split("\n")[0].slice(0, 150))}</b> · ${esc(statusLabel(t.status))} · исполнителей: ${(t.agents || []).length}</summary>
    <div class="fold-body"><p class="muted">Номер задачи: <span class="mono">${esc(t.id)}</span>${t.priority ? " · приоритет: " + esc(t.priority) : ""}</p>${t.goal ? `<p>${esc(t.goal)}</p>` : ""}${agents || '<span class="muted">Исполнители ещё не назначены.</span>'}</div>
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
    const uncertain = forge.reduce((n, t) => n + (t.agents || []).filter((a) => a.status === "unknown").length, 0);
    return `<h3 class="section-title">Субагенты <span class="muted">последних задач ${forge.length}${uncertain ? " · с неизвестной живостью " + uncertain : ""}</span></h3>
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
