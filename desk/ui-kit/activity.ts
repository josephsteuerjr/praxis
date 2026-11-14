import { t as uiText } from "../lang/lang";
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
  return ({ running: uiText("ui.ui.kit.activity.d36944f93b"), done: uiText("ui.ui.kit.activity.edc4e7a138"), completed: uiText("ui.ui.kit.activity.edc4e7a138"), failed: uiText("ui.ui.kit.activity.042fb5f9ab"), cancelled: uiText("ui.ui.kit.activity.d33d9337ec"), canceled: uiText("ui.ui.kit.activity.d33d9337ec"), blocked: uiText("ui.ui.kit.activity.57b33e5c74"), paused: uiText("ui.ui.kit.activity.6cf0b8fc13"), in_doubt: uiText("ui.ui.kit.activity.e16eb78176") } as Record<string, string>)[value || ""] || uiText("ui.ui.kit.activity.1699f99af2");
}

export function activityHTML(run: ActivityRun, detail: RunDetail | undefined,
                             duration: string,
                             opts: { pending?: boolean } = {}): string {
  const d = detail || { manifest: { status: run.status } };
  return `<section class="turn-live now-live activity-card" id="turn-live" data-run="${esc(run.id)}" data-status="${esc(d.manifest?.status || run.status)}">
    <div class="turn-live-head"><span class="dot ${d.manifest?.status === "running" ? "live" : ""}"></span><span data-activity-status>${esc(status(d))}</span><span class="t" id="turn-live-t">${esc(duration)}</span></div>
    ${run.chat_title ? `<div class="turn-live-sub">${esc(run.chat_title)}</div>` : ""}
    <div class="ev-steps" id="turn-live-steps">${detail ? stepsHTML(detail, { limit: 12, overview: true }) : opts.pending ? uiText("ui.ui.kit.activity.f1a60a6254") : uiText("ui.ui.kit.activity.669a7c0c5d")}</div>
  </section>`;
}

export function updateActivity(card: HTMLElement, detail: RunDetail, duration: string): void {
  // Пустой манифест — чтение попало в перезапись файла на событии хода: статус
  // прогона при этом НЕ менялся, и карточка «Сейчас» не должна на такт терять
  // заливку и подпись «Действия сейчас» (мигала, слово владельца 02.10).
  const m = detail.manifest;
  if (m && !m.status && !m.terminal?.status && !m.created_at) return;
  card.dataset.status = detail.manifest?.status || "";
  card.querySelector<HTMLElement>("[data-activity-status]")!.textContent = status(detail);
  card.querySelector<HTMLElement>(".turn-live-head .dot")!.classList.toggle("live", detail.manifest?.status === "running");
  card.querySelector<HTMLElement>("#turn-live-t")!.textContent = detail.manifest?.status === "running" ? duration : fmtTime(detail.manifest?.created_at);
  renderSteps(card.querySelector<HTMLElement>("#turn-live-steps")!, detail, { limit: 12, overview: true });
}
