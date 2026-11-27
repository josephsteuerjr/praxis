// «Приборы» → кэш и расход: доля входа из кэша, время и обрывы — по группам действий
// за срок. Считает её канал (взвешенно: по сумме токенов группы, а не средним процентов).
// Два отчёта приходят каждый в свой срок и показываются, когда готовы.
import { api, bar, el, fmtK, ms, pct, why } from "../core";

interface Group { group: string; calls: number; runs: number; input_tokens: number; cached_tokens: number; cache_ratio: number | null;
  output_tokens: number; median_ms: number; p90_ms: number; cuts: number }
interface Stats { days: number; summary: { calls: number; runs: number; input_tokens: number; cached_tokens: number; cache_ratio: number | null; output_tokens: number; cuts: number };
  by: Record<string, Group[]> }
interface Spend { summary: { calls: number; runs: number; total_tokens: number; output_tokens: number; cache_ratio: number | null; seconds: number; errors: number; bound_calls: number; unbound_calls: number };
  by_chat: Array<{ chat_id: string; title: string; calls: number; runs: number; total_tokens: number; output_tokens: number; cache_ratio: number | null }> }

const AXES: Array<[string, string, string]> = [
  ["iteration", "Первая итерация против продолжений", "Первый кадр хода тяжёлый: его нужно собрать и прочитать целиком. Продолжения едут по уже прочитанному префиксу — и должны быть лёгкими."],
  ["kind", "По виду хода", ""],
  ["role", "По её роли", ""],
  ["model", "По модели", ""],
  ["frame_mode", "По режиму кадра", ""],
  ["hand", "По первой руке после ответа", "Что она сделала первым шагом после ответа модели. Показаны двадцать самых частых."],
];
const PERIODS = [[1, "За сутки"], [7, "За 7 дней"], [30, "За 30 дней"]] as const;
const DAYS_WORD = (days: number) => (days === 1 ? "сутки" : days < 5 ? "дня" : "дней");

const big = (value: unknown) => (value == null ? "—" : fmtK(value));
const count = (value: number) => value.toLocaleString("ru-RU");

function figure(label: string, value: string, sub = "", meter?: { share: number | null; warn?: boolean }): HTMLElement {
  const node = el("div", "instr-fact");
  node.append(el("span", "instr-fact-label", label), el("strong", "instr-fact-value", value));
  if (sub) node.append(el("span", "muted", sub));
  if (meter) node.append(bar(meter.share, meter.warn ? "warn" : ""));
  return node;
}

function groupTable(rows: Group[]): HTMLElement {
  const table = el("table", "table");
  const head = el("tr");
  for (const title of ["Группа", "Вызовов", "Ходов", "Вход из кэша", "Вход, токенов", "Выход, токенов", "Медиана ответа", "Хвост (p90)", "Обрывов потолком"]) head.append(el("th", "", title));
  table.append(head);
  for (const row of rows) {
    const line = el("tr", row.cuts ? "err" : "");
    const cache = el("td");
    cache.append(el("span", "num", pct(row.cache_ratio == null ? null : row.cache_ratio * 100)), bar(row.cache_ratio, row.cache_ratio != null && row.cache_ratio < 0.5 ? "warn" : ""));
    line.append(el("td", "", row.group), el("td", "num", count(row.calls)), el("td", "num", count(row.runs)), cache,
      el("td", "num", big(row.input_tokens)), el("td", "num", big(row.output_tokens)), el("td", "num", ms(row.median_ms)), el("td", "num", ms(row.p90_ms)),
      el("td", "num", row.cuts ? String(row.cuts) : "—"));
    table.append(line);
  }
  const wrap = el("div", "table-wrap");
  wrap.append(table);
  return wrap;
}

function chatTable(rows: Spend["by_chat"]): HTMLElement {
  const table = el("table", "table");
  const head = el("tr");
  for (const title of ["Чат", "Вызовов", "Ходов", "Токенов всего", "Из них кэш", "Выход, токенов"]) head.append(el("th", "", title));
  table.append(head);
  for (const row of rows) {
    const line = el("tr");
    const cache = el("td");
    cache.append(el("span", "num", pct(row.cache_ratio == null ? null : row.cache_ratio * 100)), bar(row.cache_ratio));
    line.append(el("td", "", row.title || row.chat_id), el("td", "num", count(row.calls)), el("td", "num", count(row.runs)),
      el("td", "num", big(row.total_tokens)), cache, el("td", "num", big(row.output_tokens)));
    table.append(line);
  }
  const wrap = el("div", "table-wrap");
  wrap.append(table);
  return wrap;
}

function drawSpend(box: HTMLElement, spend: Spend): void {
  box.replaceChildren();
  const total = spend.summary;
  const account = el("section", "card instr-card");
  account.append(el("h3", "", "Расход по всему журналу вызовов"), el("div", "instr-facts"));
  account.lastElementChild!.append(
    figure("Всего вызовов", count(total.calls), `${count(total.bound_calls)} привязаны к чату или человеку, ${count(total.unbound_calls)} — нет`),
    figure("Все токены", big(total.total_tokens), `из них кэш: ${pct(total.cache_ratio == null ? null : total.cache_ratio * 100)}`),
    figure("Время моделей", total.seconds > 5400 ? `${Math.round(total.seconds / 3600)} ч` : `${Math.round(total.seconds / 60)} мин`, "суммарно"),
    figure("Ошибок вызовов", total.errors ? String(total.errors) : "нет", total.errors ? "вызовы, закончившиеся ошибкой" : "ни один вызов не ошибся",
      { share: total.calls ? total.errors / total.calls : null, warn: total.errors > 0 }));
  const byChat = el("section", "card instr-card");
  byChat.append(el("h3", "", "Где ушло больше всего"), el("p", "field-hint", "По чатам: счётчик считает токены, а не деньги."), chatTable(spend.by_chat.slice(0, 12)));
  box.append(account, byChat);
}

function drawCache(box: HTMLElement, data: Stats): void {
  box.replaceChildren();
  const summary = el("section", "card instr-card");
  const facts = el("div", "instr-facts");
  facts.append(figure("Вызовов модели", count(data.summary.calls), `в ${count(data.summary.runs)} ходах`),
    figure("Вход из кэша", pct(data.summary.cache_ratio == null ? null : data.summary.cache_ratio * 100),
      `${big(data.summary.cached_tokens)} из ${big(data.summary.input_tokens)} токенов`, { share: data.summary.cache_ratio, warn: data.summary.cache_ratio != null && data.summary.cache_ratio < 0.7 }),
    figure("Ответ", big(data.summary.output_tokens), "токенов"),
    figure("Обрывов потолком", data.summary.cuts ? String(data.summary.cuts) : "нет", data.summary.cuts ? "ответ оборвался, не закончившись" : "ни один ответ не оборван"));
  summary.append(el("h3", "", `Кэш за ${data.days} ${DAYS_WORD(data.days)}`), facts);
  box.append(summary);
  for (const [axis, title, hint] of AXES) {
    const rows = data.by[axis];
    if (!rows?.length) continue;
    const section = el("section", "card instr-card");
    section.append(el("h3", "", title));
    if (hint) section.append(el("p", "field-hint", hint));
    section.append(groupTable(axis === "hand" ? rows.slice(0, 20) : rows));
    box.append(section);
  }
  if (data.by.day?.length) {
    const section = el("section", "card instr-card");
    section.append(el("h3", "", "По дням"), groupTable([...data.by.day].reverse()));
    box.append(section);
  }
}

export function mountCache(box: HTMLElement): { refresh(): void } {
  const period = el("select", "select");
  period.setAttribute("aria-label", "Срок");
  for (const [days, title] of PERIODS) {
    const option = document.createElement("option");
    option.value = String(days);
    option.textContent = title;
    option.selected = days === 7;
    period.append(option);
  }
  const toolbar = el("div", "instr-toolbar");
  toolbar.append(el("span", "muted", "Показать"), period);
  const spendBox = el("div");
  const cacheBox = el("div");
  box.append(toolbar, spendBox, cacheBox);
  let version = 0;

  const load = async () => {
    const span = Number(period.value) || 7;
    const mine = ++version;
    // Расход по журналу вызовов приходит быстро; разрезы кэша канал считает по журналу всех ходов — дольше.
    spendBox.replaceChildren(el("p", "muted", "Считаю расход…"));
    cacheBox.replaceChildren(el("p", "muted", "Считаю разрезы кэша по журналу ходов — это может занять до полуминуты…"));
    void api<Spend>(`/api/spend?days=${span}`)
      .then((value) => { if (mine === version) drawSpend(spendBox, value); })
      .catch((error) => { if (mine === version) spendBox.replaceChildren(el("div", "empty", why(error))); });
    try {
      const data = await api<Stats>(`/api/frame-stats?days=${span}`);
      if (mine === version) drawCache(cacheBox, data);
    } catch (error) {
      if (mine === version) cacheBox.replaceChildren(el("div", "empty", why(error)));
    }
  };
  period.addEventListener("change", () => void load());
  void load();
  return { refresh: () => void load() };
}
