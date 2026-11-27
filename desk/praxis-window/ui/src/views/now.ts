// «Сейчас»: как она сама, расход и лимиты, ход со шагами, предыдущие ходы и субагенты.
// Ходы и шаги рисует её же блок (phoneActions) — тот, что стоит на её телефоне.
import { mountUsage, usageShell } from "../../../ui-kit/usage";
import { phoneActions } from "../../../ui-kit/phone-actions";
import { setResultFetcher } from "../../../ui-kit/steps";
import { agentState, api, el, gate, go, liveRun, modelAgo, note, onState, statePhrase, whenStateReady, why } from "../core";

type Run = { id: string; kind?: string; goal_head?: string };

const KIND: Record<string, string> = { chat_turn: "Ответ в чате", wake: "Пробуждение", task_window: "Окно работы" };

// Длинный результат руки дочитывается файлом хода — кнопкой в ленте шагов.
setResultFetcher((run, result) => api(`/api/run/${encodeURIComponent(run)}/result/${encodeURIComponent(result)}`));

const when = (iso: string) => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export function mount(root: HTMLElement, tools: HTMLElement): void {
  // Её блоки ходов и расхода ходят в её канал через ворота раздела: скрытый раздел не опрашивает
  // сервер, показанный — перечитывает сразу.
  const g = gate(root);
  const { api, post } = g;
  const head = el("section", "now-head");
  const phrase = el("div", "now-phrase");
  phrase.setAttribute("role", "status");
  const facts = el("div", "now-facts");
  const alarms = el("div", "now-alarms");
  head.append(phrase, facts, alarms);

  const usage = el("section", "now-usage");
  usage.innerHTML = usageShell(true);

  // Её блок ходов называет свой узел своим классом, поэтому стоит внутри обёртки,
  // которая держит раскладку «Сейчас»: ход слева, история и проверки справа.
  const actions = el("div", "now-actions");
  const streamBox = el("div");
  actions.append(streamBox);
  root.append(head, usage, actions);

  // Шапка: написать ей и прервать живой ход.
  const write = el("button", "btn btn-primary", "Написать ей");
  write.type = "button";
  write.addEventListener("click", () => go("chats", { room: "window" })); // комната окна — здесь она читает слово владельца
  const stop = el("button", "btn btn-quiet", "Прервать ход");
  stop.type = "button";
  stop.hidden = true;
  const verdict = note("");
  verdict.hidden = true;
  tools.append(write, stop);
  head.append(verdict);

  stop.addEventListener("click", async () => {
    if (!window.confirm("Прервать её текущий ход? Прочитанное сохранится, ход закончится без ответа.")) return;
    stop.disabled = true;
    try {
      const answer = await post<{ ok?: boolean; note?: string }>("/api/interrupt", { scope: "all", reason: "Прервано из окна владельца" });
      verdict.textContent = answer.note || (answer.ok ? "Прошла просьба остановиться. Ход закончится на текущем шаге." : "Она не приняла просьбу остановиться.");
      verdict.className = "muted";
    } catch (error) {
      verdict.textContent = why(error);
      verdict.className = "err";
    }
    verdict.hidden = false;
    stop.disabled = false;
  });

  const paint = () => {
    const state = agentState();
    const busy = !!liveRun();
    stop.hidden = !busy;
    const bits: string[] = [];
    const ago = modelAgo(state);
    const said = statePhrase(state);      // когда модель отвечала — уже сказано в заголовке
    if (said.startsWith("Ведёт") && ago) bits.push(`модель отвечала ${ago}`);
    if (state?.next_wake) bits.push(`следующее пробуждение · ${when(state.next_wake)}`);
    if (state?.brain?.model) bits.push(state.brain.model);
    phrase.textContent = said;
    facts.replaceChildren(...bits.map((text) => el("span", "pill", text)));
    alarms.replaceChildren(...(state?.alarms ?? []).map((alarm) => el("p", "err", alarm.text)));
    head.dataset.level = !state ? "off" : busy ? "live" : said.startsWith("На связи") ? "ok" : "warn";
  };
  onState(paint);
  paint();

  const stream = phoneActions(streamBox, g.request, () => location.origin + "\n" + (agentState()?.agent ?? ""),
    (run: Run) => run.goal_head || KIND[run.kind || ""] || "Ход", { history: true });
  // Её блок судит о смене агента по первому опросу: запускать его надо, когда имя уже
  // известно, иначе первый опрос окно считает «чужим» — и блок замолкает навсегда.
  void whenStateReady().then(() => stream.start());
  mountUsage(usage, api);
}

/** Сюда пришли извне («к ходу») — просто начать раздел сверху. */
export function focus(): void {
  document.querySelector<HTMLElement>('.view-host[data-view="now"]')?.scrollTo({ top: 0 });
}
