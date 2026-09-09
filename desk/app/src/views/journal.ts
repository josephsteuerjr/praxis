// Журнал: ошибки вызовов модели и пропуски восприятия. Для разработчика;
// человеку ошибка показывается на месте, в разговоре и в шапке.
import { api } from "../api";
import { esc, fmtTs } from "../lib";

interface Errors {
  llm?: Array<{ ts?: number; model?: string; role?: string; err?: string; retries?: number }>;
  skips?: Array<{ ts?: number; stage?: string; class?: string; detail?: string; chat?: string }>;
}

export async function render(container: HTMLElement): Promise<void> {
  const e = await api<Errors>("/api/errors");
  const llm = (e.llm || [])
    .slice()
    .reverse()
    .map(
      (r) => `<tr>
      <td class="mono">${fmtTs(r.ts)}</td><td>${esc(r.model || "")}</td>
      <td>${esc(r.role || "")}</td><td class="err-msg">${esc(r.err || "")}</td>
      <td class="mono">${r.retries || 0}</td></tr>`,
    )
    .join("");
  const skips = (e.skips || [])
    .slice()
    .reverse()
    .map(
      (r) => `<tr>
      <td class="mono">${fmtTs(r.ts)}</td><td>${esc(r.stage || "")}</td>
      <td>${esc(r.class || "")}</td><td class="muted">${esc(r.detail || "")}</td>
      <td class="mono">${esc(r.chat || "")}</td></tr>`,
    )
    .join("");
  container.innerHTML = `<div class="center">
    <h3 class="section-title">Ошибки вызовов модели <span class="muted">${(e.llm || []).length}</span></h3>
    ${llm ? `<table class="grid"><tr><th>когда</th><th>модель</th><th>роль</th><th>ошибка</th><th>ретраи</th></tr>${llm}</table>` : '<div class="card muted">Ошибок модели не было.</div>'}
    <h3 class="section-title">Пропуски восприятия <span class="muted">${(e.skips || []).length}</span></h3>
    ${skips ? `<table class="grid"><tr><th>когда</th><th>стадия</th><th>класс</th><th>деталь</th><th>чат</th></tr>${skips}</table>` : '<div class="card muted">Пропусков не было.</div>'}
  </div>`;
}
