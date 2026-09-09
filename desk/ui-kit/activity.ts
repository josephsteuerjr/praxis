import { esc, fmtTime } from "./text";
import { renderSteps, stepsHTML, type RunDetail } from "./steps";

export interface ActivityRun { id: string; status: string; kind: string; chat_id?: string | number | null; chat_title?: string; created_at?: string }

/** Keep the observed run when it finishes or temporarily leaves the listing. */
export function selectActivity<T extends ActivityRun>(previous: T | undefined, live: T | undefined, list: T[]): T | undefined {
  if (live) return live;
  if (!previous) return list[0];
  const latest = list[0];
  // A short run can start and finish entirely between two refreshes.
  if (latest && Date.parse(latest.created_at || "") > Date.parse(previous.created_at || "")) return latest;
  return list.find(r => r.id === previous.id) || previous;
}

function status(d: RunDetail): string {
  const value = d.manifest?.terminal?.status || d.manifest?.status;
  return ({ running: "Действия сейчас", done: "Работа завершена", completed: "Работа завершена", failed: "Работа остановилась с ошибкой", cancelled: "Работа отменена", canceled: "Работа отменена", blocked: "Нужно внимание", paused: "Работа приостановлена", in_doubt: "Нужно проверить результат" } as Record<string, string>)[value || ""] || "Последняя работа";
}

export function activityHTML(run: ActivityRun, detail: RunDetail | undefined, duration: string): string {
  const d = detail || { manifest: { status: run.status } };
  const key = String(run.chat_id ?? "").replace(/^pult$/, "window");
  return `<section class="turn-live now-live activity-card" id="turn-live" data-run="${esc(run.id)}" data-status="${esc(d.manifest?.status || run.status)}">
    <div class="turn-live-head"><span class="dot ${d.manifest?.status === "running" ? "live" : ""}"></span><span data-activity-status>${esc(status(d))}</span><span class="t" id="turn-live-t">${esc(duration)}</span></div>
    ${run.chat_title ? `<div class="turn-live-sub"><a href="#" data-room="${esc(key)}" data-room-name="${esc(run.chat_title)}">${esc(run.chat_title)}</a></div>` : ""}
    <div class="ev-steps" id="turn-live-steps">${detail ? stepsHTML(detail, { limit: 12, overview: true }) : '<div class="muted">Не удалось прочитать действия. Повторяю подключение…</div>'}</div>
  </section>`;
}

export function updateActivity(card: HTMLElement, detail: RunDetail, duration: string): void {
  card.dataset.status = detail.manifest?.status || "";
  card.querySelector<HTMLElement>("[data-activity-status]")!.textContent = status(detail);
  card.querySelector<HTMLElement>(".turn-live-head .dot")!.classList.toggle("live", detail.manifest?.status === "running");
  card.querySelector<HTMLElement>("#turn-live-t")!.textContent = detail.manifest?.status === "running" ? duration : fmtTime(detail.manifest?.created_at);
  renderSteps(card.querySelector<HTMLElement>("#turn-live-steps")!, detail, { limit: 12, overview: true });
}
