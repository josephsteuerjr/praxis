import type { AgentState } from "../state";
import { esc } from "../lib";

export function inspectionHTML(value: AgentState["update_inspection"]) {
  if (!value?.id) return "";
  const reported = value.state === "reported";
  const checks = Array.isArray(value.checks) ? value.checks.filter((c) => c && typeof c === "object" && typeof c.id === "string") : [];
  const problems = checks.filter((c) => c.status === "problem").length;
  const unknown = checks.filter((c) => c.status !== "passed" && c.status !== "problem").length;
  const title = reported ? "Отчёт об осмотре сохранён" : value.state === "needs_attention" ? "Осмотр обновления ждёт продолжения" : "Агенту назначен осмотр обновления";
  const names: Record<string, string> = { passed: "проверено", problem: "проблема", unverified: "не проверено" };
  const goals = Array.isArray(value.goals) ? value.goals.filter((g) => g && typeof g === "object" && typeof g.id === "string") : [];
  const goal = new Map(goals.map((g) => [g.id, g.title]));
  return `<section class="inspection-status${problems ? " has-problems" : ""}">
    <span class="eyebrow">Обновление ${esc(value.version || "")}</span><h3>${title}</h3>
    <p>${reported ? esc(value.summary || "") : "Просьба сохраняется до отчёта по целям выпуска и названным багам. Установка уже завершена; осмотр не включает автоматический откат."}</p>
    ${reported ? `<p class="muted">Проверено: ${checks.length - problems - unknown}; проблем: ${problems}; осталось проверить: ${unknown}.</p>` : ''}
    <details class="fold"><summary>${reported ? "Что проверено и каким способом" : "Что агент должен осмотреть"}</summary><div class="fold-body">
      ${reported ? checks.map((c) => `<div class="inspection-check"><strong>${esc(goal.get(c.id) || c.id)}</strong><span class="muted"> · ${esc(names[c.status] || "не проверено")}</span><p>${esc(c.evidence)}</p></div>`).join("") : `<ul>${goals.map((g) => `<li>${esc(g.title)}</li>`).join("")}</ul>`}
    </div></details></section>`;
}
