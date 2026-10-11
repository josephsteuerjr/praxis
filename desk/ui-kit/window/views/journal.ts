// Журнал: ошибки вызовов модели и пропуски восприятия — последние события,
// длинные ошибки свёрнуты; полный хвост — в «подробнее». Пустые таблицы — норма.
import { api } from "../api";
import { esc, fmtTs } from "../lib";

interface Errors {
  llm?: Array<{ ts?: number; model?: string; role?: string; err?: string; retries?: number }>;
  skips?: Array<{ ts?: number; stage?: string; class?: string; detail?: string; chat?: string }>;
}

const TAIL = 50;
const shortErr = (s: string) => (s.length > 120 ? s.slice(0, 117) + "…" : s);
const detailHTML = (s: string) => s.length > 120
  ? `${esc(shortErr(s))} <details class="fold"><summary>подробнее</summary><pre class="mono">${esc(s)}</pre></details>`
  : esc(s);

export async function render(container: HTMLElement): Promise<void> {
  const e = await api<Errors>("/api/errors");
  const llmAll = (e.llm || []).slice().reverse();
  const llm = llmAll.slice(0, TAIL).map(
      (r) => `<tr>
      <td class="mono">${fmtTs(r.ts)}</td><td>${esc(r.model || "")}</td>
      <td title="${esc(r.role || "")}">${esc(({voice:"Ответ агента", evaluator:"Проверка и свёртка"} as Record<string,string>)[r.role || ""] || r.role || "")}</td><td class="err-msg">${detailHTML(r.err || "")}</td>
      <td class="mono">${r.retries || 0}</td></tr>`,
    )
    .join("");
  const skipsAll = (e.skips || []).slice().reverse();
  const skips = skipsAll.slice(0, TAIL).map(
      (r) => `<tr>
      <td class="mono">${fmtTs(r.ts)}</td><td>${esc(r.stage || "")}</td>
      <td>${esc(r.class || "")}</td><td class="muted">${detailHTML(r.detail || "")}</td>
      <td class="mono">${esc(r.chat || "")}</td></tr>`,
    )
    .join("");
  container.innerHTML = `<div class="center journal">
    <p class="muted">Здесь сбои вызовов модели и пропуски восприятия. Пустые таблицы — норма; показаны последние ${TAIL}.</p>
    <h3 class="section-title">Ошибки вызовов модели <span class="muted">${llmAll.length}</span></h3>
    ${llm ? `<table class="grid"><tr><th>когда</th><th>модель</th><th>назначение</th><th>ошибка</th><th>повторы</th></tr>${llm}</table>${llmAll.length > TAIL ? `<p class="muted">+${llmAll.length - TAIL} старых — не показано</p>` : ""}` : '<div class="card muted">Ошибок модели не было.</div>'}
    <h3 class="section-title">Пропуски восприятия <span class="muted">${skipsAll.length}</span></h3>
    ${skips ? `<table class="grid"><tr><th>когда</th><th>стадия</th><th>класс</th><th>деталь</th><th>чат</th></tr>${skips}</table>${skipsAll.length > TAIL ? `<p class="muted">+${skipsAll.length - TAIL} старых — не показано</p>` : ""}` : '<div class="card muted">Пропусков не было.</div>'}
  </div>`;
}
