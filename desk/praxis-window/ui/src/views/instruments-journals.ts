// «Приборы» → журналы: то, что её харнесс пишет о себе самом — отказы рук, пропуски
// восприятия, вердикты гномов, модерация, события ядра. Список закрытый и живёт в её
// канале; окно показывает последние записи и считает, чего в них больше.
import { esc, fmtTs } from "../../../ui-kit/text";
import { api, bytes, el } from "../core";

interface Journal { id: string; title: string; hint: string; columns: string[]; exists: boolean; bytes?: number; lines?: number; modified?: string }
interface Page { id: string; title: string; hint: string; columns: string[]; lines: number; rows: Array<Record<string, unknown>> }

const LABEL: Record<string, string> = {
  ts: "Когда", at: "Когда", day: "День", rail: "Рельс", op: "Рука", detail: "Подробность", stage: "Этап", class: "Класс",
  prev_n: "Прежде", gnome: "Гном", subject: "Субъект", verdict: "Вердикт", action: "Действие", status: "Состояние",
  peer_id: "Чат", sender_verified: "Отправитель проверен", banned: "Забанен", deleted: "Удалено", error: "Ошибка", kind: "Вид",
  target: "Куда", ok: "Успех", note: "Заметка", role: "Роль", model: "Модель", "in": "Вход", cached: "Из кэша", out: "Выход",
  ms: "Время", retries: "Повторы", run_id: "Ход", dest: "Кому", delivery: "Доставка", gist: "Суть", scope: "Область",
  latency_ms: "Задержка", step: "Шаг", reason: "Причина", unit: "Служба", source: "Источник",
};
const WHEN = new Set(["ts", "at", "day", "modified"]);

const label = (key: string) => LABEL[key] || key;
const when = (value: unknown): string => {
  if (typeof value === "number") return new Date(value * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value)) return fmtTs(Date.parse(value) / 1000);
  return value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
};
const count = (rows: Array<Record<string, unknown>>, key: string) => {
  const seen = new Map<string, number>();
  for (const row of rows) {
    const value = row[key];
    if (value == null || value === "" || typeof value === "object") continue;
    seen.set(String(value), (seen.get(String(value)) ?? 0) + 1);
  }
  return [...seen].sort((a, b) => b[1] - a[1]);
};

export function mountJournals(box: HTMLElement): { show(id: string): void } {
  const list = el("nav", "instr-list");
  list.setAttribute("aria-label", "Журналы");
  const search = el("input", "chat-search");
  search.type = "search";
  search.placeholder = "Найти запись";
  search.autocomplete = "off";
  search.setAttribute("aria-label", "Найти запись в журнале");
  const side = el("aside", "instr-side");
  side.append(search, list);
  const detail = el("section", "instr-detail");
  const columns = el("div", "instr-columns-run");
  columns.append(side, detail);
  box.append(columns);
  let current = "";
  let rows: Array<Record<string, unknown>> = [];
  let page: Journal | null = null;
  let version = 0;

  const draw = () => {
    if (!page) return;
    const filter = search.value.trim().toLocaleLowerCase("ru");
    const shown = filter ? rows.filter((row) => JSON.stringify(row).toLocaleLowerCase("ru").includes(filter)) : rows;
    detail.replaceChildren(el("div", "top-kicker", "Журнал"), el("h2", "section-title", page.title),
      el("p", "lead", page.hint), el("p", "muted", `${(page.lines ?? 0).toLocaleString("ru-RU")} записей всего · показаны последние ${rows.length}`));
    const counts = el("div", "instr-counts");
    for (const key of page.columns.filter((column) => !WHEN.has(column) && column !== "detail" && column !== "note")) {
      const top = count(shown, key).slice(0, 6);
      if (top.length < 2) continue;
      const group = el("div", "instr-count");
      group.append(el("span", "muted", label(key)));
      for (const [value, times] of top) group.append(el("span", "pill", `${value} · ${times}`));
      counts.append(group);
    }
    detail.append(counts);
    if (!shown.length) { detail.append(el("div", "empty", filter ? "Ничего не нашлось." : "Записей пока нет.")); return; }
    const table = el("table", "table journal");
    const head = el("tr");
    for (const column of page.columns) head.append(el("th", "", label(column)));
    table.append(head);
    for (const row of shown.slice().reverse()) {
      const line = el("tr");
      line.tabIndex = 0;
      const rest = Object.entries(row).filter(([key]) => !page!.columns.includes(key));
      for (const column of page.columns) {
        const cell = el("td", WHEN.has(column) ? "num when" : "", "");
        const value: string = when(row[column]);
        if (column === "ok" || column === "banned" || column === "deleted" || column === "sender_verified") cell.className += typeof row[column] === "boolean" ? (row[column] ? " ok-text" : " err") : "";
        cell.textContent = String(value);
        cell.title = String(value);
        line.append(cell);
      }
      const expand = () => {
        const next = line.nextElementSibling as HTMLElement | null;
        if (next?.dataset.full !== undefined) { next.remove(); return; }
        const full = el("tr", "full");
        full.dataset.full = "";
        const cell = el("td");
        cell.colSpan = page!.columns.length;
        const long = Object.entries(row).filter(([key, item]) => page!.columns.includes(key) && (typeof item === "object" || String(item).length > 80));
        for (const [key, value] of [...rest, ...long]) {
          const line2 = el("div", "kv");
          line2.append(el("span", "kv-label", label(key)), el("span", "kv-value", typeof value === "object" ? JSON.stringify(value, null, 1) : String(value ?? "—")));
          cell.append(line2);
        }
        if (!cell.childElementCount) cell.textContent = "Других полей в записи нет.";
        line.after(full);
      };
      line.addEventListener("click", expand);
      line.addEventListener("keydown", (event: KeyboardEvent) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); expand(); } });
      table.append(line);
    }
    detail.append(el("div", "table-wrap"), el("p", "field-hint", "Строка раскрывается щелчком: там остальные поля записи."));
    detail.querySelector(".table-wrap")!.append(table);
  };

  const open = async (id: string) => {
    current = id;
    const mine = ++version;
    detail.replaceChildren(el("p", "muted", "Читаю журнал…"));
    for (const item of list.querySelectorAll<HTMLElement>("[data-journal]")) item.setAttribute("aria-current", String(item.dataset.journal === id));
    try {
      const data = await api<Page>(`/api/window/journal/${encodeURIComponent(id)}?limit=200`);
      if (mine !== version) return;
      page = { ...data, lines: data.lines ?? 0, exists: true };
      rows = data.rows;
      draw();
    } catch (error) {
      if (mine === version) detail.replaceChildren(el("div", "empty", error instanceof Error ? error.message : "Журнал не прочитался."));
    }
  };
  search.addEventListener("input", draw);

  const load = async () => {
    try {
      const journals = await api<Journal[]>("/api/window/journals");
      list.replaceChildren(...journals.map((journal) => {
        const button = el("button", "instr-item" + (journal.exists ? "" : " empty"));
        button.type = "button";
        button.dataset.journal = journal.id;
        button.disabled = !journal.exists;
        button.setAttribute("aria-current", String(journal.id === current));
        const meta = journal.exists ? `${(journal.lines ?? 0).toLocaleString("ru-RU")} записей · ${bytes(journal.bytes)}` : "пусто";
        button.innerHTML = `<span class="instr-item-title">${esc(journal.title)}</span><span class="instr-item-hint">${esc(journal.hint)}</span><span class="instr-item-meta">${esc(meta)}${journal.modified ? ` · ${esc(fmtTs(Date.parse(journal.modified) / 1000))}` : ""}</span>`;
        button.addEventListener("click", () => void open(journal.id));
        return button;
      }));
      if (!current) {
        const first = journals.find((journal) => journal.exists && journal.id === "denials") ?? journals.find((journal) => journal.exists);
        if (first) await open(first.id);
      }
    } catch (error) {
      list.replaceChildren(el("div", "empty", error instanceof Error ? error.message : "Список журналов не прочитался."));
    }
  };
  void load();
  return { show: (id: string) => void open(id) };
}
