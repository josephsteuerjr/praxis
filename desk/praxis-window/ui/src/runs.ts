// Ходы: как назвать ход и как показать его строкой со шагами. Разметка — её
// (`.ev`, `.ev-head`, `.ev-steps`): те же строки стоят на её телефоне.
import { stepsHTML, type RunDetail } from "../../ui-kit/steps";
import { esc, fmtDay, fmtTime } from "../../ui-kit/text";
import { api, el, go, why } from "./core";

export interface Run {
  id: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
  terminal_status?: string;
  kind?: string;
  chat_id?: string | null;
  chat_title?: string;
  run_label?: string;
  goal_head?: string;
  operator?: boolean;
}

export const KIND: Record<string, string> = { chat_turn: "Ответ в чате", wake: "Пробуждение", task_window: "Окно работы", subagent: "Субагент" };

export const runLabel = (run: Run) => run.goal_head || run.run_label || KIND[run.kind || ""] || run.kind || "Ход";

export const runLive = (run: Run, now = Date.now()) => {
  if (run.status !== "running") return false;
  const age = now - Date.parse(run.updated_at || "");
  return Number.isFinite(age) && age >= -5000 && age < 30 * 60_000;
};

const STATUS: Record<string, string> = { done: "завершён", failed: "не удался", cancelled: "прерван", running: "идёт" };

/** Строка хода: название, время, комната; клик раскрывает шаги, «Разбор» ведёт в «Приборы». */
export function runRow(run: Run, options: { selected?: string; inspect?: boolean; room?: boolean } = {}): HTMLElement {
  const status = run.terminal_status || run.status || "";
  const node = el("div", "ev" + (runLive(run) ? " live" : status === "failed" ? " failed" : "") + (options.selected === run.id ? " chosen" : ""));
  node.dataset.ev = run.id;
  const head = document.createElement("button");
  head.type = "button";
  head.className = "ev-head";
  head.setAttribute("aria-expanded", "false");
  const day = fmtDay(run.created_at);
  const when = `${day === "Сегодня" ? "" : day + ", "}${fmtTime(run.created_at)}`;
  const sub = [run.kind === "wake" ? "" : KIND[run.kind || ""] || run.kind, options.room === false ? "" : run.chat_title, status && status !== "done" && status !== "running" ? STATUS[status] || status : ""]
    .filter(Boolean).join(" · ");
  head.innerHTML = `<span class="ev-time">${esc(when)}</span><span class="ev-title">${esc(runLabel(run))}</span>${sub ? `<div class="ev-sub">${esc(sub)}</div>` : ""}`;
  const steps = el("div", "ev-steps");
  steps.hidden = true;
  node.append(head, steps);
  if (options.inspect !== false) {
    const inspect = el("button", "chip ev-inspect", "Разбор хода");
    inspect.type = "button";
    inspect.title = "Что читала модель и что записал харнесс в этом ходу";
    inspect.addEventListener("click", (event) => { event.stopPropagation(); go("instruments", { run: run.id }); });
    node.append(inspect);
  }
  let painted = false;
  head.addEventListener("click", async () => {
    const open = steps.hidden === true;
    steps.hidden = !open;
    node.classList.toggle("open", open);
    head.setAttribute("aria-expanded", String(open));
    if (!open || painted) return;
    steps.innerHTML = '<div class="muted">читаю шаги…</div>';
    try {
      steps.innerHTML = stepsHTML(await api<RunDetail>(`/api/run/${encodeURIComponent(run.id)}`));
      painted = true;
    } catch (error) {
      steps.textContent = why(error);
    }
  });
  return node;
}

/** Список ходов в узле; пустой список — честной строкой. */
export function runList(box: HTMLElement, runs: Run[], options: { selected?: string; empty?: string } = {}): void {
  box.replaceChildren(...(runs.length ? runs.map((run) => runRow(run, options)) : [el("div", "empty", options.empty ?? "Ходов пока нет.")]));
}
