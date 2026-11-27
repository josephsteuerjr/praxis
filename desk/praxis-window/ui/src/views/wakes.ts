// «Пробуждения»: когда она проснётся и что делала в прошлые подъёмы.
import { agentState, el, gate } from "../core";
import { runList, type Run } from "../runs";

const TICK = 15_000;

export function mount(root: HTMLElement): void {
  const g = gate(root);
  const { api } = g;
  root.classList.add("wakes");
  const head = el("div", "wake-next");
  head.setAttribute("role", "status");
  const box = el("div", "wake-list");
  const note = el("p", "muted", "Читаю пробуждения…");
  root.append(head, box, note);
  let signature = "";

  const paintHead = () => {
    const state = agentState();
    const next = state?.next_wake;
    const when = next ? new Date(next).toLocaleString("ru-RU", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "";
    head.replaceChildren(el("div", "top-kicker", "Следующее пробуждение"), el("div", "wake-when", when || "не назначено"),
      el("p", "muted", state?.phrase ? `Сейчас: ${state.phrase}` : ""));
  };
  paintHead();

  // Опрос спит, пока раздел скрыт, и срабатывает сразу при показе.
  const tick = async () => {
    if (!document.hidden) {
      try {
        const runs = await api<Run[]>("/api/runs?kind=wake&limit=120&audience=all");
        const next = runs.map((run) => `${run.id}:${run.status}:${run.updated_at}`).join("|");
        if (next !== signature) { signature = next; runList(box, runs, { empty: "Пробуждений ещё не было." }); }
        note.textContent = "";
      } catch {
        note.textContent = "Не удалось прочитать пробуждения. Повторю сам.";
      }
      paintHead();
    }
    g.every(() => void tick(), TICK);
  };
  void tick();
}
