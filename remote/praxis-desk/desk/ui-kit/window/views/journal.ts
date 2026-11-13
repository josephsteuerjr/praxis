// Readable events with exact diagnostics available on demand.
import { api } from "../api";
import { esc, fmtTs } from "../lib";
import type { AgentState } from "../state";
import { inspectionHTML } from "./inspection-status";

interface Errors {
  llm?: Array<{ ts?: number; model?: string; role?: string; err?: string; retries?: number }>;
  skips?: Array<{ ts?: number; stage?: string; class?: string; detail?: string; chat?: string }>;
}
interface Event { ts: number; kind: string; title: string; description: string; detail: string; context: string }
const TAIL = 50;
const short = (s: unknown) => { const text = String(s ?? ""); return text.length > 180 ? text.slice(0, 177) + "…" : text; };

function modelDescription(error: string) {
  if (/429|rate.?limit|quota|лимит/i.test(error)) return "Провайдер ограничил запросы. Подробный ответ покажет, требуется ли ожидание или смена модели.";
  if (/401|403|unauthori|forbidden/i.test(error)) return "Провайдер отказал в доступе. Проверь подключение и ключ выбранной модели.";
  if (/timeout|timed out|таймаут/i.test(error)) return "Ответ не получен в отведённое время. Это не доказывает, что модель или агент остановлены.";
  if (/connection|network|resolve|сет|подключ/i.test(error)) return "Связь с провайдером не состоялась. Проверь сеть и адрес подключения.";
  return short(error) || "Причина в журнале вызова не записана.";
}

export function journalEvents(data: Errors): Event[] {
  const llm = Array.isArray(data?.llm) ? data.llm.filter((r) => r && typeof r === "object") : [];
  const skips = Array.isArray(data?.skips) ? data.skips.filter((r) => r && typeof r === "object") : [];
  return [
    ...llm.map((r) => ({ ts: r.ts || 0, kind: "model", title: r.role === "evaluator" ? "Не прошёл вызов проверки или свёртки" : "Не прошёл вызов модели",
      description: modelDescription(r.err || ""), detail: r.err || "", context: [r.model, r.retries ? `повторов: ${r.retries}` : ""].filter(Boolean).join(" · ") })),
    ...skips.map((r) => ({ ts: r.ts || 0, kind: "perception", title: r.class === "duplicate" ? "Повторное событие уже учтено" : "Событие восприятия пропущено", description: short(r.detail || r.class || "Причина не записана"),
      detail: r.detail || "", context: [r.stage, r.class, r.chat ? `чат ${r.chat}` : ""].filter(Boolean).join(" · ") })),
  ].sort((a, b) => b.ts - a.ts);
}

export async function render(container: HTMLElement): Promise<void> {
  const [eR, sR] = await Promise.allSettled([api<Errors>("/api/errors"), api<AgentState>("/api/state")]);
  if (eR.status !== "fulfilled") throw eR.reason;
  const e = eR.value;
  const events = journalEvents(e);
  container.innerHTML = `<div class="center journal">
    <p class="page-intro">Здесь видно, что помешало работе агента: сбои модели и пропущенные события. Запись об ошибке остаётся в истории после восстановления связи.</p>
    ${inspectionHTML(sR.status === "fulfilled" ? sR.value.update_inspection : undefined)}
    <div class="journal-controls" role="group" aria-label="Фильтр событий">
      <button class="context-link" type="button" data-journal-kind="all" aria-pressed="true">Все · ${events.length}</button>
      <button class="context-link" type="button" data-journal-kind="model" aria-pressed="false">Модель · ${events.filter((r) => r.kind === "model").length}</button>
      <button class="context-link" type="button" data-journal-kind="perception" aria-pressed="false">Восприятие · ${events.filter((r) => r.kind === "perception").length}</button>
    </div><div data-journal-events></div></div>`;
  const box = container.querySelector<HTMLElement>("[data-journal-events]")!;
  const paint = (kind: string) => {
    const selected = events.filter((r) => kind === "all" || r.kind === kind);
    box.innerHTML = selected.length ? `<ol class="journal-events">${selected.slice(0, TAIL).map((r) => `<li class="journal-event">
      <time>${fmtTs(r.ts)}</time><div><h3>${esc(r.title)}</h3><p>${esc(r.description)}</p><small class="muted">${esc(r.context)}</small>
      <details class="fold"><summary>Полная запись</summary><pre class="mono">${esc(r.detail || r.description)}</pre></details></div></li>`).join("")}</ol>
      ${selected.length > TAIL ? `<p class="muted">Ещё ${selected.length - TAIL} старых событий остаются в архиве.</p>` : ''}` : '<div class="journal-empty">Здесь пока нет событий.<p class="muted">Текущую связь и доступ к компьютеру можно посмотреть в «Системе».</p></div>';
    container.querySelectorAll<HTMLButtonElement>("[data-journal-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.journalKind === kind)));
  };
  container.querySelectorAll<HTMLButtonElement>("[data-journal-kind]").forEach((b) => b.addEventListener("click", () => paint(b.dataset.journalKind || "all")));
  paint("all");
}
