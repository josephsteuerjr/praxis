// «Приборы» → тень: теневые кадры, которые харнесс собирает для её комнат, — что
// входило в кадр, что изменилось между двумя снимками и где порвался общий префикс
// (до первого расхождения вход читается из кэша, после — заново).
import { md } from "../../../ui-kit/text";
import { api, bytes, el, relTime, why } from "../core";

interface Stream { stream: string; captures: number; latest: string }
interface Diff { prefix_bytes: number; old_len: number; new_len: number; zones: Array<{ zone: string; state: string; chars: number; was_chars?: number; prefix?: number }>; gone: string[]; diff: string[] }
interface Metric { ts?: string; epoch?: number; sizes?: { total_chars?: number; stable_bytes?: number; k?: number; e?: number; a?: number; t?: number }; lcp?: Record<string, unknown>; e_over_target?: number; fold_stuck?: unknown }
const STATE: Record<string, string> = { same: "не изменилась", changed: "изменилась", new: "появилась" };

const title = (stream: string) => stream.replace(/^chat-/, "чат ").replace(/^dm-/, "личка ").replace(/^topic-/, "тема ");

export function mountShadow(box: HTMLElement): { refresh(): void } {
  const list = el("nav", "instr-list");
  list.setAttribute("aria-label", "Потоки тени");
  const side = el("aside", "instr-side");
  const detail = el("section", "instr-detail");
  const columns = el("div", "instr-columns-run");
  columns.append(side, detail);
  side.append(list);
  box.append(el("p", "lead", "Тень — запасной кадр той же комнаты, собранный в сторону от хода. Сравнив два снимка, видно, что в кадре держится, а что уходит: общее начало читается из кэша."), columns);

  let stream = "";
  let version = 0;

  const load = async () => {
    try {
      const streams = await api<Stream[]>("/api/shadow");
      list.replaceChildren(...streams.slice().sort((a, b) => b.captures - a.captures).map((item) => {
        const button = el("button", "instr-item");
        button.type = "button";
        button.dataset.stream = item.stream;
        button.setAttribute("aria-current", String(item.stream === stream));
        button.innerHTML = `<span class="instr-item-title">${title(item.stream)}</span><span class="instr-item-meta">${item.captures} снимков · последний ${item.latest.slice(0, 15).replace("T", " ")}</span>`;
        button.addEventListener("click", () => void open(item.stream));
        return button;
      }));
      if (!stream) {
        const first = streams.slice().sort((a, b) => b.captures - a.captures)[0];
        if (first) await open(first.stream);
      }
    } catch (error) {
      list.replaceChildren(el("div", "empty", why(error)));
    }
  };

  async function open(name: string) {
    stream = name;
    const mine = ++version;
    const quoted = encodeURIComponent(name);
    for (const item of list.querySelectorAll<HTMLElement>("[data-stream]")) item.setAttribute("aria-current", String(item.dataset.stream === name));
    detail.replaceChildren(el("div", "top-kicker", "Тень"), el("h2", "section-title", title(name)), el("p", "muted", "Читаю снимки…"));
    let names: string[];
    try {
      names = await api<string[]>(`/api/shadow/${quoted}/captures?limit=30`);
    } catch (error) {
      if (mine === version) detail.append(el("div", "empty", why(error)));
      return;
    }
    if (mine !== version) return;
    detail.replaceChildren(el("div", "top-kicker", "Тень"), el("h2", "section-title", title(name)));
    if (names.length < 2) { detail.append(el("div", "empty", "У этого потока пока один снимок — сравнивать не с чем.")); return; }
    const toolbar = el("div", "instr-toolbar");
    const older = el("select", "select");
    const newer = el("select", "select");
    older.setAttribute("aria-label", "Прежний снимок");
    newer.setAttribute("aria-label", "Новый снимок");
    for (const item of names) {
      for (const [select, value] of [[older, names[1]], [newer, names[0]]] as const) {
        const option = document.createElement("option");
        option.value = item;
        option.textContent = item.slice(0, 15).replace("T", " ");
        option.selected = item === value;
        select.append(option);
      }
    }
    const result = el("div");
    const compare = async () => {
      const take = ++version;
      result.replaceChildren(el("p", "muted", "Сравниваю…"));
      let diff: Diff;
      try {
        diff = await api<Diff>(`/api/shadow/${quoted}/diff?old=${encodeURIComponent(older.value)}&new=${encodeURIComponent(newer.value)}`);
      } catch (error) {
        if (take === version) result.replaceChildren(el("div", "empty", why(error)));
        return;
      }
      if (take !== version) return;
      const share = diff.new_len ? diff.prefix_bytes / diff.new_len : 0;
      const card = el("section", "card instr-card");
      const facts = el("div", "instr-facts");
      const fact = (label: string, value: string, sub = "") => {
        const node = el("div", "instr-fact");
        node.append(el("span", "instr-fact-label", label), el("strong", "instr-fact-value", value));
        if (sub) node.append(el("span", "muted", sub));
        return node;
      };
      const prefix = fact("Общее начало", `${Math.round(share * 100)}%`, `${bytes(diff.prefix_bytes)} из ${bytes(diff.new_len)} нового снимка`);
      const meter = el("div", "bar");
      const fill = el("div", "bar-fill" + (share < 0.8 ? " warn" : ""));
      fill.style.width = (share * 100).toFixed(1) + "%";
      meter.append(fill);
      prefix.append(meter);
      facts.append(prefix, fact("Прежний снимок", bytes(diff.old_len)), fact("Новый снимок", bytes(diff.new_len), `${Math.round((diff.new_len - diff.old_len) / 1024)} КБ разницы`));
      card.append(el("h3", "", "Где порвался префикс"), facts,
        el("p", "field-hint", "До первого расхождения вход совпадает с прежним и читается из кэша; всё после — собирается и читается заново."));
      const table = el("table", "table");
      const head = el("tr");
      for (const caption of ["Зона кадра", "Состояние", "Знаков", "Было", "Общее начало зоны"]) head.append(el("th", "", caption));
      table.append(head);
      const zones: Array<{ zone: string; state: string; chars: number; was_chars?: number; prefix?: number }> = [...diff.zones, ...diff.gone.map((gone) => ({ zone: gone, state: "gone", chars: 0 }))];
      for (const zone of zones) {
        const line = el("tr", zone.state === "changed" || zone.state === "gone" ? "err" : zone.state === "new" ? "" : "quiet");
        const state = el("td");
        const tone = zone.state === "same" ? "ok" : zone.state === "changed" ? "warn" : zone.state === "gone" ? "err" : "live";
        state.append(el("span", "pill " + tone, zone.state === "gone" ? "ушла" : STATE[zone.state] || zone.state));
        line.append(el("td", "", zone.zone), state, el("td", "num", zone.chars ? zone.chars.toLocaleString("ru-RU") : "—"),
          el("td", "num", zone.was_chars != null ? zone.was_chars.toLocaleString("ru-RU") : "—"),
          el("td", "num", zone.prefix != null ? `${Math.round((zone.chars ? zone.prefix / zone.chars : 0) * 100)}%` : "—"));
        table.append(line);
      }
      const wrap = el("div", "table-wrap");
      wrap.append(table);
      card.append(wrap);
      const changes = el("details", "fold");
      changes.append(el("summary", "", `Что именно изменилось · ${diff.diff.length} строк`));
      const body = el("div", "fold-body");
      const pre = el("pre", "code", diff.diff.join("\n"));
      body.append(pre);
      changes.append(body);
      result.replaceChildren(card, changes);
    };
    const show = el("details", "fold");
    show.append(el("summary", "", "Открыть новый снимок целиком"));
    const showBody = el("div", "fold-body");
    show.append(showBody);
    show.addEventListener("toggle", async () => {
      if (!show.open || showBody.childElementCount) return;
      const take = version;
      try {
        const capture = await api<{ text: string }>(`/api/shadow/${quoted}/capture?name=${encodeURIComponent(newer.value)}`);
        if (take !== version) return;
        const paper = el("article", "card md");
        paper.innerHTML = md(capture.text);
        showBody.append(paper);
      } catch (error) { showBody.textContent = why(error); }
    });
    toolbar.append(el("span", "muted", "Прежний"), older, el("span", "muted", "Новый"), newer);
    detail.append(toolbar, result, show);
    const change = () => { if (older.value === newer.value) { result.replaceChildren(el("p", "err", "Выберите два разных снимка.")); return; } void compare(); };
    older.addEventListener("change", change);
    newer.addEventListener("change", change);
    await compare();
    void api<Metric[]>(`/api/shadow/${quoted}/metrics?n=40`).then((metrics) => {
      if (mine !== version || !metrics.length) return;
      const card = el("section", "card instr-card");
      card.append(el("h3", "", "Метрики последних снимков"), el("p", "field-hint", "Размер кадра по частям: конституция, блоки о ней, окно аудитории, инструменты."));
      const table = el("table", "table");
      const head = el("tr");
      for (const caption of ["Когда", "Эпоха", "Всего знаков", "Стабильные байты", "Конституция", "О ней", "Окно аудитории", "Инструменты"]) head.append(el("th", "", caption));
      table.append(head);
      for (const metric of metrics.slice(-25)) {
        const sizes = metric.sizes || {};
        const line = el("tr");
        table.append(line);
        for (const value of [metric.ts ? relTime(metric.ts) : "—", metric.epoch ?? "—", sizes.total_chars ?? "—", sizes.stable_bytes ?? "—", sizes.k ?? "—", sizes.e ?? "—", sizes.a ?? "—", sizes.t ?? "—"])
          line.append(el("td", typeof value === "number" ? "num" : "", typeof value === "number" ? value.toLocaleString("ru-RU") : String(value)));
      }
      const wrap = el("div", "table-wrap");
      wrap.append(table);
      card.append(wrap);
      detail.append(card);
    }).catch(() => undefined);
  }

  void load();
  return { refresh: () => { if (stream) void open(stream); else void load(); } };
}
