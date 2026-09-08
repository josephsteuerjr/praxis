import { esc } from "./text";
import "./usage.css";

interface Counts {
  calls: number; total_tokens: number | null; input_tokens: number | null;
  output_tokens: number; cache_read_tokens: number; cache_percent: number | null;
  legacy_semantics: boolean; name?: string;
}
interface Usage {
  status: string; reason?: string; updated_at?: number;
  today_total: Counts | null; week_total: Counts | null;
  models: Counts[]; roles: Counts[];
  days: (Counts & { day: string; recorded: boolean })[];
}
interface Allowance {
  name: string; status: string; reason?: string; observed_at: number;
  windows: { label: string; used_percent: number | null; remaining_percent: number | null;
    resets_at: number | null; remaining?: number | null; limit?: number | null; unit?: string }[];
}
type Fetcher = <T>(path: string) => Promise<T>;
const num = (n: number | null | undefined) => n == null ? "—" : new Intl.NumberFormat("ru", { maximumFractionDigits: 1, notation: n >= 10000 ? "compact" : "standard" }).format(n);
const exact = (n: number | null) => n == null ? "неизвестно" : new Intl.NumberFormat("ru").format(n);
const clamp = (n: number) => Math.max(0, Math.min(100, n));
const date = (n: number) => new Date(n * 1000).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

let overviewOpen = false;
let modelsOpen = false;

interface SpendRow { calls: number; runs: number; total_tokens: number; output_tokens: number; cache_ratio: number | null }
interface Spend {
  days: number;
  summary: SpendRow & { bound_calls: number; unbound_calls: number };
  by_chat: (SpendRow & { chat_id: string; title: string })[];
  by_person: (SpendRow & { who: string; name: string; chats: string[] })[];
  unbound: (SpendRow & { role: string })[];
}

let spendOpen = false;

/** Расход по чатам и людям за 7 дней (`/api/spend`, deskd/spend.py) — та же ревизия, что в окне
 * на экране «Система», в сжатом виде для телефона и мини-аппа: топ-5 чатов и людей, остаток — числом. */
function spendHTML(s: Spend): string {
  if (!s.summary?.calls) return "";
  const pct = (r: number | null) => (r == null ? "—" : num(r * 100) + "%");
  const row = (name: string, sub: string, r: SpendRow) =>
    `<tr><th scope="row">${esc(name)}${sub ? `<small>${esc(sub)}</small>` : ""}</th><td>${num(r.calls)}</td><td>${num(r.total_tokens)}</td><td>${pct(r.cache_ratio)}</td></tr>`;
  const head = `<thead><tr><th>кто / где</th><th>Вызовы</th><th>Токены</th><th>Кэш</th></tr></thead>`;
  const chats = s.by_chat.slice(0, 5).map((c) => row(c.title || c.chat_id || "—", c.title && c.chat_id ? c.chat_id : "", c)).join("");
  const restChats = s.by_chat.length > 5 ? `<p class="usage-muted">и ещё ${s.by_chat.length - 5} ${s.by_chat.length - 5 === 1 ? "чат" : "чатов"}</p>` : "";
  const people = s.by_person.slice(0, 5).map((p) => row(p.name || p.who, p.chats.slice(0, 2).join(" · "), p)).join("");
  const unbound = s.summary.unbound_calls ? `<p class="usage-muted">Вне ходов (судья, Forge): ${num(s.summary.unbound_calls)} вызовов — их не приписать ни чату, ни человеку.</p>` : "";
  return `<details class="usage-details usage-spend" ${spendOpen ? "open" : ""}><summary>По чатам и людям за ${s.days} дней <span>${s.by_chat.length}</span></summary>
    <div class="usage-table-wrap"><table>${head}<tbody>${chats}</tbody></table></div>${restChats}
    <div class="usage-table-wrap"><table>${head}<tbody>${people}</tbody></table></div>${unbound}
    <p class="usage-muted">Токены, не деньги. «Сама, без человека» — её собственные ходы: будильник, задачи, Forge. Полный разрез — в окне, экран «Система».</p></details>`;
}

export function usageShell(compact = false): string {
  const body = `<div class="usage-heading"><h2>Расход</h2><span>Загружаю счётчик…</span></div><div data-usage></div><div data-spend></div><div data-allowances></div>`;
  if (!compact) return `<section class="usage-panel" aria-label="Расход и лимиты">${body}</section>`;
  return `<details class="usage-panel usage-compact" ${overviewOpen ? "open" : ""}>
    <summary class="usage-summary"><span class="usage-summary-title">Расход и лимиты</span><span class="usage-summary-toggle">Подробнее <span aria-hidden="true">⌄</span></span>
    <span class="usage-brief" data-usage-brief>Загружаю счётчик…</span><span class="usage-limits-brief" data-limits-brief>Проверяю лимиты…</span></summary>
    <div class="usage-expanded">${body}</div></details>`;
}

function briefHTML(data: Usage): string {
  if (data.status !== "ok" || !data.today_total || !data.week_total) return `<span>${esc(data.reason || "Счётчик недоступен")}</span>`;
  return `<span><span>Сегодня</span><b>${num(data.today_total.total_tokens)} <small>токенов</small></b></span><span><span>7 дней</span><b>${num(data.week_total.total_tokens)} <small>токенов</small></b></span>`;
}

function limitsBriefHTML(providers: Allowance[]): string {
  if (!providers.length) return "Квоты подписки не подключены";
  return providers.map(p => {
    const windows = p.windows.filter(w => w.remaining_percent != null);
    const lowest = windows.reduce<Allowance["windows"][number] | undefined>((a, w) => !a || w.remaining_percent! < a.remaining_percent! ? w : a, undefined);
    const label = lowest ? `${num(lowest.remaining_percent)}% осталось · ${lowest.label}` : "лимит неизвестен";
    return `<span class="usage-limit-brief ${lowest && lowest.remaining_percent! <= 10 ? "usage-low" : ""}"><b>${esc(p.name)}</b> ${esc(label)}${p.status === "stale" ? " · прежние данные" : ""}</span>`;
  }).join("");
}

function countsHTML(data: Usage): string {
  if (data.status !== "ok" || !data.today_total || !data.week_total) return `<p class="usage-muted">${esc(data.reason || "Счётчик недоступен")}</p>`;
  const today = data.today_total, week = data.week_total;
  const max = Math.max(1, ...data.days.map(d => d.calls));
  const daily = data.days.map(d => `<div class="usage-day" title="${esc(d.day)}: ${exact(d.calls)} вызовов, ${exact(d.total_tokens)} токенов"><div class="usage-bar-track"><i style="height:${d.calls ? Math.max(5, d.calls / max * 100) : 0}%"></i></div><span>${esc(d.day.slice(8))}</span></div>`).join("");
  const rows = (data.models.length ? data.models : data.roles).map(row => `<tr><th scope="row">${esc(row.name || "Модель не записана")}</th><td>${num(row.calls)}</td><td>${num(row.input_tokens)}</td><td>${num(row.output_tokens)}</td><td>${row.cache_percent == null ? "—" : num(row.cache_percent) + "%"}</td></tr>`).join("");
  return `<div class="usage-overview">
    <div class="usage-stat"><span>Сегодня</span><strong title="${exact(today.total_tokens)} токенов">${num(today.total_tokens)}<small>токенов</small></strong><span>${num(today.calls)} вызовов модели</span></div>
    <div class="usage-stat"><span>Последние 7 дней</span><strong title="${exact(week.total_tokens)} токенов">${num(week.total_tokens)}<small>токенов</small></strong><span>${num(week.calls)} вызовов · ${week.cache_percent == null ? "кэш неизвестен" : num(week.cache_percent) + "% входа из кэша"}</span></div>
    <div class="usage-history" role="img" aria-label="Вызовы модели за семь дней: ${esc(data.days.map(d => d.day + ': ' + d.calls).join('; '))}">${daily}<span class="usage-history-label">Вызовы по дням</span></div>
  </div>
  <details class="usage-details"><summary>По моделям за 7 дней <span>${data.models.length || data.roles.length}</span></summary><div class="usage-table-wrap"><table><thead><tr><th>Модель</th><th>Вызовы</th><th>Вход</th><th>Выход</th><th>Кэш</th></tr></thead><tbody>${rows}</tbody></table></div><p class="usage-muted">${week.legacy_semantics ? "В старых записях вход считался по разным правилам. Итог токенов и доля кэша для них неизвестны. " : "Вход включает свежие токены и кэш. "}Это наблюдаемый расход агента; незавершённые запросы и работа в других приложениях могут не входить в счётчик.</p></details>`;
}

function allowancesHTML(providers: Allowance[]): string {
  if (!providers.length) return `<div class="usage-quota-empty">Квоты подписки не подключены</div>`;
  return `<div class="usage-allowances">${providers.map(p => `<div class="usage-provider"><div class="usage-provider-head"><b>${esc(p.name)}</b><span>${p.status === "stale" ? "Последние известные данные" : "Весь аккаунт"}</span></div>${p.windows.length ? p.windows.map(w => {
    const remaining = w.remaining_percent == null ? null : clamp(w.remaining_percent);
    const meter = remaining == null ? "" : `<div class="usage-meter" role="meter" aria-label="${esc(p.name + ' · ' + w.label)}: осталось" aria-valuenow="${remaining}" aria-valuemin="0" aria-valuemax="100"><i class="${remaining <= 10 ? "usage-low" : ""}" style="width:${remaining}%"></i></div>`;
    return `<div class="usage-quota"><div class="usage-quota-label"><span>${esc(w.label)}</span><strong>${remaining == null ? "Нет процента" : num(remaining) + "% осталось"}</strong></div>${meter}<div class="usage-quota-foot"><span>${w.remaining != null && w.limit != null ? `${num(w.remaining)} / ${num(w.limit)} ${w.unit === "credits" ? "кредитов" : "ед."}` : w.used_percent == null ? "Сервис не сообщил расход" : num(w.used_percent) + "% использовано"}</span><span>${w.resets_at ? "Сброс " + date(w.resets_at) : "Время сброса не сообщено"}</span></div></div>`;
  }).join("") : `<p class="usage-muted">${esc(p.reason || "Сервис не вернул квоты")}</p>`}<div class="usage-source">Данные сервиса · ${date(p.observed_at)}</div></div>`).join("")}</div>`;
}

export function mountUsage(container: HTMLElement, api: Fetcher): void {
  const panel = container.querySelector<HTMLElement>(".usage-panel");
  if (!panel) return;
  if (panel instanceof HTMLDetailsElement) panel.addEventListener("toggle", () => { overviewOpen = panel.open; });
  const brief = panel.querySelector<HTMLElement>("[data-usage-brief]");
  const limits = panel.querySelector<HTMLElement>("[data-limits-brief]");
  const refresh = async () => {
    if (!panel.isConnected) return;
    await Promise.allSettled([
      api<Usage>("/api/usage").then(data => {
        if (!panel.isConnected) return;
        const focused = panel.querySelector(".usage-details > summary") === document.activeElement;
        panel.querySelector<HTMLElement>("[data-usage]")!.innerHTML = countsHTML(data);
        const details = panel.querySelector<HTMLDetailsElement>(".usage-details");
        if (details) {
          details.open = modelsOpen;
          details.addEventListener("toggle", () => { modelsOpen = details.open; });
          if (focused) details.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
        }
        if (brief) brief.innerHTML = briefHTML(data);
        panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = "Учёт агента";
      }).catch(() => {
        if (panel.isConnected) {
          panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = "Счётчик недоступен";
          if (brief) brief.textContent = "Не удалось обновить расход";
        }
      }),
      api<Spend>("/api/spend?days=7").then(data => {
        const box = panel.querySelector<HTMLElement>("[data-spend]");
        if (!panel.isConnected || !box) return;
        const focused = box.querySelector(".usage-spend > summary") === document.activeElement;
        box.innerHTML = spendHTML(data);
        const details = box.querySelector<HTMLDetailsElement>(".usage-spend");
        if (details) {
          details.addEventListener("toggle", () => { spendOpen = details.open; });
          if (focused) details.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
        }
      }).catch(() => {
        // Разреза нет (старый канал без /api/spend) — молчим: счётчик выше уже сказал главное.
      }),
      api<{providers: Allowance[]}>("/api/allowances").then(data => {
        if (panel.isConnected) {
          panel.querySelector<HTMLElement>("[data-allowances]")!.innerHTML = allowancesHTML(data.providers);
          if (limits) limits.innerHTML = limitsBriefHTML(data.providers);
        }
      }).catch(() => {
        if (panel.isConnected) {
          panel.querySelector<HTMLElement>("[data-allowances]")!.innerHTML = '<p class="usage-muted">Не удалось обновить квоты подписки</p>';
          if (limits) limits.textContent = "Не удалось обновить лимиты";
        }
      }),
    ]);
    if (panel.isConnected) window.setTimeout(refresh, 15000);
  };
  void refresh();
}
