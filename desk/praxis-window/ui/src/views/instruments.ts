// «Приборы»: что читает модель и что записал харнесс. Четыре экрана:
//   ход      — разбор одного хода: кадры, подготовка вызовов, генерация, исходящие;
//   журналы  — её собственные журналы: отказы рук, пропуски восприятия, вердикты, модерация;
//   кэш      — доля входа из кэша, время и обрывы по группам действий;
//   тень     — теневые кадры комнат и то, где между снимками рвётся общий префикс.
import { api, el, listToggle, why, type Dispose } from "../core";
import { KIND, runLabel, runLive, type Run } from "../runs";
import { mountCache } from "./instruments-cache";
import { mountJournals } from "./instruments-journals";
import { mountRun, type RunPick } from "./instruments-run";
import { mountShadow } from "./instruments-shadow";

type Tab = "run" | "journals" | "cache" | "shadow";
const TABS: Array<[Tab, string]> = [["run", "Ход"], ["journals", "Журналы"], ["cache", "Кэш и расход"], ["shadow", "Тень"]];
const FILTERS: Array<[string, string]> = [["", "Все ходы"], ["chat_turn", "Ответы в чатах"], ["wake", "Пробуждения"], ["task_window", "Окна работы"], ["operator", "Проверки оператора"]];
const PAGE = 60;
let openRun: ((pick: RunPick) => void) | null = null;   // экран «ход» текущего раздела

const LIST_NAME: Partial<Record<Tab, string>> = { run: "список ходов", journals: "список журналов", shadow: "список комнат" };

export function mount(root: HTMLElement): Dispose | undefined {
  root.classList.add("instruments");
  const bar = el("div", "instr-bar");
  const tabs = el("div", "chips instr-tabs");
  tabs.setAttribute("role", "tablist");
  const body = el("div", "instr-body");
  bar.append(tabs);
  root.append(bar, body);
  const nodes = {} as Record<Tab, HTMLElement>;
  let alive = true;

  const screens: Record<Tab, () => void> = {
    run: buildRunScreen,
    journals: () => { mountJournals(nodes.journals); },
    cache: () => { mountCache(nodes.cache); },
    shadow: () => { mountShadow(nodes.shadow); },
  };

  /** Экран с двумя колонками (список слева, разбор справа) занимает его от шапки до низа,
   *  а список сворачивается кнопкой в строке вкладок — открытый разбор остаётся на месте. */
  function armList(tab: Tab): void {
    const node = nodes[tab];
    const columns = node.querySelector<HTMLElement>(".instr-columns-run");
    if (!columns) return;
    node.classList.add("instr-view-columns");
    const button = listToggle("praxis.window.list." + tab, LIST_NAME[tab] || "список", columns);
    button.dataset.for = tab;
    bar.prepend(button);
  }

  function show(next: Tab): void {
    for (const button of tabs.querySelectorAll<HTMLElement>("[data-tab]")) {
      button.setAttribute("aria-selected", String(button.dataset.tab === next));
      button.classList.toggle("on", button.dataset.tab === next);
    }
    if (!nodes[next]) {
      nodes[next] = el("div", "instr-view");
      body.append(nodes[next]);
      screens[next]();
      armList(next);
    }
    for (const [name, node] of Object.entries(nodes)) node.hidden = name !== next;
    for (const button of bar.querySelectorAll<HTMLElement>(".list-toggle")) button.hidden = button.dataset.for !== next;
  }

  // ---- экран «ход»: список ходов слева, разбор справа
  function buildRunScreen(): void {
    const node = nodes.run;
    const side = el("aside", "instr-side");
    const filter = el("select", "select");
    filter.setAttribute("aria-label", "Какие ходы");
    for (const [value, title] of FILTERS) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = title;
      filter.append(option);
    }
    const search = el("input", "chat-search");
    search.type = "search";
    search.placeholder = "Найти ход";
    search.autocomplete = "off";
    search.setAttribute("aria-label", "Найти ход");
    const list = el("nav", "instr-list");
    list.setAttribute("aria-label", "Ходы");
    const more = el("button", "chip", "Ещё ходы");
    more.type = "button";
    side.append(filter, search, list, more);
    const detail = el("section", "instr-detail", "Выберите ход слева.");
    const columns = el("div", "instr-columns-run");
    columns.append(side, detail);
    node.append(columns);
    const run = mountRun(detail);
    openRun = (pick) => run.show(pick);

    let runs: Run[] = [];
    let selected = "";
    let before = "";
    let busy = false;
    let version = 0;

    const pick = (item: Run) => {
      selected = item.id;
      for (const button of list.querySelectorAll<HTMLElement>("[data-run]")) button.setAttribute("aria-current", String(button.dataset.run === selected));
      run.show({ id: item.id, label: runLabel(item), kind: item.kind, chat: item.chat_title, created: item.created_at });
    };
    const paint = () => {
      const text = search.value.trim().toLocaleLowerCase("ru");
      const shown = runs.filter((item) => !text || `${runLabel(item)} ${item.chat_title || ""}`.toLocaleLowerCase("ru").includes(text));
      const finished = (item: Run) => (item.terminal_status && item.terminal_status !== "done" ? item.terminal_status : item.status === "running" ? "идёт" : "завершён");
      list.replaceChildren(...shown.map((item) => {
        const button = el("button", "instr-item run" + (runLive(item) ? " live" : item.terminal_status === "failed" || item.status === "failed" ? " failed" : ""));
        button.type = "button";
        button.dataset.run = item.id;
        button.setAttribute("aria-current", String(item.id === selected));
        const when = item.created_at ? new Date(item.created_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
        const parts = [when, item.kind === "wake" ? "" : KIND[item.kind || ""] || item.kind || "ход", finished(item), item.chat_title].filter(Boolean);
        const meta = el("span", "instr-item-meta", parts.join(" · "));
        const title = el("span", "instr-item-title", runLabel(item));
        button.append(title, meta);
        button.addEventListener("click", () => pick(item));
        return button;
      }));
      if (!shown.length) list.append(el("div", "empty", text ? "Ничего не нашлось." : "Ходов пока нет."));
      more.hidden = runs.length < PAGE;
    };
    const load = async (reset: boolean) => {
      if (!alive || busy) return;
      busy = true;
      more.disabled = true;
      more.textContent = "Читаю…";
      const mine = ++version;
      try {
        const query = filter.value === "operator" ? "audience=operator" : filter.value ? `audience=all&kind=${filter.value}` : "audience=all";
        const page = await api<Run[]>(`/api/runs?${query}&limit=${PAGE + 1}${!reset && before ? `&before=${encodeURIComponent(before)}` : ""}`);
        if (!alive || mine !== version) return;
        if (reset) {
          runs = [];
          selected = "";
          detail.replaceChildren(el("p", "muted", "Выберите ход слева."));
        }
        const known = new Set(runs.map((item) => item.id));
        runs = [...runs, ...page.slice(0, PAGE).filter((item) => !known.has(item.id))];
        before = runs[runs.length - 1]?.id ?? "";
        paint();
        if (!selected && runs.length) pick(runs[0]);
      } catch (error) {
        if (alive && mine === version) list.append(el("div", "empty", why(error)));
      } finally {
        busy = false;
        more.disabled = false;
        more.textContent = "Ещё ходы";
      }
    };
    filter.addEventListener("change", () => void load(true));
    more.addEventListener("click", () => void load(false));
    search.addEventListener("input", paint);
    void load(true);
  }

  for (const [id, title] of TABS) {
    const button = el("button", "chip", title);
    button.type = "button";
    button.dataset.tab = id;
    button.setAttribute("role", "tab");
    button.addEventListener("click", () => show(id));
    tabs.append(button);
  }
  show("run");
  return () => { alive = false; };
}

/** Открыть разбор конкретного хода — из списка ходов другого раздела. */
export function focus(intent: unknown): void {
  const id = intent && typeof intent === "object" ? String((intent as { run?: unknown }).run || "") : "";
  if (!id) return;
  const inList = document.querySelector<HTMLElement>(`#view .instr-list [data-run="${CSS.escape(id)}"]`);
  if (inList) { inList.click(); return; }
  // хода нет в показанном списке — открыть его напрямую; название придёт из журнала хода
  const created = /^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)/.exec(id.slice(4));
  const stamp = created ? `${created[1]}-${created[2]}-${created[3]}T${created[4]}:${created[5]}:${created[6]}Z` : "";
  openRun?.({ id, label: "", kind: "", chat: "", created: stamp });
}
