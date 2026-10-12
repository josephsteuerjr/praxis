import type { AgentState } from "../state";
import { esc } from "../lib";

export function inspectionHTML(value: AgentState["update_inspection"]) {
  if (!value?.id) return "";
  const reported = value.state === "reported";
  const checks = value.checks || [];
  const problems = checks.filter((c) => c.status === "problem").length;
  const unknown = checks.filter((c) => c.status === "unverified").length;
  const title = reported ? "Отчёт об осмотре сохранён" : value.state === "needs_attention" ? "Осмотр обновления ждёт продолжения" : "Агенту назначен осмотр обновления";
  const names: Record<string, string> = { passed: "проверено", problem: "проблема", unverified: "не проверено" };
  const goal = new Map((value.goals || []).map((g) => [g.id, g.title]));
  return `<section class="inspection-status${problems ? " has-problems" : ""}">
    <span class="eyebrow">Обновление ${esc(value.version || "")}</span><h3>${title}</h3>
    <p>${reported ? esc(value.summary || "") : "Просьба сохраняется до отчёта по целям выпуска и названным багам. Установка уже завершена; осмотр не включает автоматический откат."}</p>
    ${reported ? `<p class="muted">Проверено: ${checks.length - problems - unknown}; проблем: ${problems}; осталось проверить: ${unknown}.</p>` : ''}
    <details class="fold"><summary>${reported ? "Что проверено и каким способом" : "Что агент должен осмотреть"}</summary><div class="fold-body">
      ${reported ? checks.map((c) => `<div class="inspection-check"><strong>${esc(goal.get(c.id) || c.id)}</strong><span class="muted"> · ${esc(names[c.status] || c.status)}</span><p>${esc(c.evidence)}</p></div>`).join("") : `<ul>${(value.goals || []).map((g) => `<li>${esc(g.title)}</li>`).join("")}</ul>`}
    </div></details></section>`;
}
