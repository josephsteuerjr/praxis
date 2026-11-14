import { t as uiText } from "../../../lang/lang";
import type { AgentState } from "../state";
import { esc } from "../lib";

export function inspectionHTML(value: AgentState["update_inspection"]) {
  if (!value?.id) return "";
  const reported = value.state === "reported";
  const checks = Array.isArray(value.checks) ? value.checks.filter((c) => c && typeof c === "object" && typeof c.id === "string") : [];
  const problems = checks.filter((c) => c.status === "problem").length;
  const unknown = checks.filter((c) => c.status !== "passed" && c.status !== "problem").length;
  const title = reported ? uiText("ui.ui.kit.window.views.inspection.status.bf7edc97ca") : value.state === "needs_attention" ? uiText("ui.ui.kit.window.views.inspection.status.1bf62116ec") : uiText("ui.ui.kit.window.views.inspection.status.b2a53d1851");
  const names: Record<string, string> = { passed: uiText("ui.ui.kit.window.views.inspection.status.be0d36ce03"), problem: uiText("ui.ui.kit.window.views.inspection.status.b6703e28e2"), unverified: uiText("ui.ui.kit.window.views.inspection.status.948b8f97e3") };
  const goals = Array.isArray(value.goals) ? value.goals.filter((g) => g && typeof g === "object" && typeof g.id === "string") : [];
  const goal = new Map(goals.map((g) => [g.id, g.title]));
  return uiText("ui.ui.kit.window.views.inspection.status.4604365fbe", {p0: String(problems ? " has-problems" : ""), p1: String(esc(value.version || "")), p2: String(title), p3: String(reported ? esc(value.summary || "") : uiText("ui.ui.kit.window.views.inspection.status.5a5e15afc0")), p4: String(reported ? uiText("ui.ui.kit.window.views.inspection.status.891bebe475", {p0: String(checks.length - problems - unknown), p1: String(problems), p2: String(unknown)}) : ''), p5: String(reported ? uiText("ui.ui.kit.window.views.inspection.status.734b7c81f9") : uiText("ui.ui.kit.window.views.inspection.status.7284d3965f")), p6: String(reported ? checks.map((c) => `<div class="inspection-check"><strong>${esc(goal.get(c.id) || c.id)}</strong><span class="muted"> · ${esc(names[c.status] || uiText("ui.ui.kit.window.views.inspection.status.948b8f97e3"))}</span><p>${esc(c.evidence)}</p></div>`).join("") : `<ul>${goals.map((g) => `<li>${esc(g.title)}</li>`).join("")}</ul>`)});
}
