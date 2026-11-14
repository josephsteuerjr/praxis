import { t as uiText } from "../../../lang/lang";
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
  if (/429|rate.?limit|quota|лимит/i.test(error)) return uiText("ui.ui.kit.window.views.journal.9036d1e6cd");
  if (/401|403|unauthori|forbidden/i.test(error)) return uiText("ui.ui.kit.window.views.journal.c9b750a495");
  if (/timeout|timed out|таймаут/i.test(error)) return uiText("ui.ui.kit.window.views.journal.6336866f4b");
  if (/connection|network|resolve|сет|подключ/i.test(error)) return uiText("ui.ui.kit.window.views.journal.de240eb300");
  return short(error) || uiText("ui.ui.kit.window.views.journal.07aff9d1e6");
}

export function journalEvents(data: Errors): Event[] {
  const llm = Array.isArray(data?.llm) ? data.llm.filter((r) => r && typeof r === "object") : [];
  const skips = Array.isArray(data?.skips) ? data.skips.filter((r) => r && typeof r === "object") : [];
  return [
    ...llm.map((r) => ({ ts: r.ts || 0, kind: "model", title: r.role === "evaluator" ? uiText("ui.ui.kit.window.views.journal.075780dcc5") : uiText("ui.ui.kit.window.views.journal.fb74ec2991"),
      description: modelDescription(r.err || ""), detail: r.err || "", context: [r.model, r.retries ? uiText("ui.ui.kit.window.views.journal.f80da8fd9b", {p0: String(r.retries)}) : ""].filter(Boolean).join(" · ") })),
    ...skips.map((r) => ({ ts: r.ts || 0, kind: "perception", title: r.class === "duplicate" ? uiText("ui.ui.kit.window.views.journal.4bf65818d8") : uiText("ui.ui.kit.window.views.journal.ed1b60c931"), description: short(r.detail || r.class || uiText("ui.ui.kit.window.views.journal.f7b84911ee")),
      detail: r.detail || "", context: [r.stage, r.class, r.chat ? uiText("ui.ui.kit.window.views.journal.68d85157bb", {p0: String(r.chat)}) : ""].filter(Boolean).join(" · ") })),
  ].sort((a, b) => b.ts - a.ts);
}

export async function render(container: HTMLElement): Promise<void> {
  const [eR, sR] = await Promise.allSettled([api<Errors>("/api/errors"), api<AgentState>("/api/state")]);
  if (eR.status !== "fulfilled") throw eR.reason;
  const e = eR.value;
  const events = journalEvents(e);
  container.innerHTML = uiText("ui.ui.kit.window.views.journal.3deaddae82", {p0: String(inspectionHTML(sR.status === "fulfilled" ? sR.value.update_inspection : undefined)), p1: String(events.length), p2: String(events.filter((r) => r.kind === "model").length), p3: String(events.filter((r) => r.kind === "perception").length)});
  const box = container.querySelector<HTMLElement>("[data-journal-events]")!;
  const paint = (kind: string) => {
    const selected = events.filter((r) => kind === "all" || r.kind === kind);
    box.innerHTML = selected.length ? `<ol class="journal-events">${selected.slice(0, TAIL).map((r) => uiText("ui.ui.kit.window.views.journal.a4a122bd15", {p0: String(fmtTs(r.ts)), p1: String(esc(r.title)), p2: String(esc(r.description)), p3: String(esc(r.context)), p4: String(esc(r.detail || r.description))})).join("")}</ol>
      ${selected.length > TAIL ? uiText("ui.ui.kit.window.views.journal.f345f3fe82", {p0: String(selected.length - TAIL)}) : ''}` : uiText("ui.ui.kit.window.views.journal.6b61601ca0");
    container.querySelectorAll<HTMLButtonElement>("[data-journal-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.journalKind === kind)));
  };
  container.querySelectorAll<HTMLButtonElement>("[data-journal-kind]").forEach((b) => b.addEventListener("click", () => paint(b.dataset.journalKind || "all")));
  paint("all");
}
