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

export function usageShell(): string {
  return `<section class="usage-panel" aria-label="Расход и лимиты"><div class="usage-heading"><h2>Расход</h2><span>Загружаю счётчик…</span></div><div data-usage></div><div data-allowances></div></section>`;
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
  return `<div class="usage-allowances">${providers.map(p => `<div class="usage-provider"><div class="usage-provider-head"><b>${esc(p.name)}</b><span>${p.status === "stale" ? "Последние известные данные" : "Весь аккаунт"}</span></div>${p.windows.length ? p.windows.map(w => `<div class="usage-quota"><div class="usage-quota-label"><span>${esc(w.label)}</span><strong>${w.remaining_percent == null ? "Нет процента" : num(w.remaining_percent) + "% осталось"}</strong></div><div class="usage-meter" role="meter" aria-label="${esc(p.name + ' · ' + w.label)}: использовано" ${w.used_percent == null ? '' : `aria-valuenow="${clamp(w.used_percent)}" aria-valuemin="0" aria-valuemax="100"`}><i class="${(w.used_percent ?? 0) >= 90 ? "usage-low" : ""}" style="width:${clamp(w.used_percent ?? 0)}%"></i></div><div class="usage-quota-foot"><span>${w.remaining != null && w.limit != null ? `${num(w.remaining)} / ${num(w.limit)} ${w.unit === "credits" ? "кредитов" : "ед."}` : w.used_percent == null ? "Сервис не сообщил расход" : num(w.used_percent) + "% использовано"}</span><span>${w.resets_at ? "Сброс " + date(w.resets_at) : "Время сброса не сообщено"}</span></div></div>`).join("") : `<p class="usage-muted">${esc(p.reason || "Сервис не вернул квоты")}</p>`}<div class="usage-source">Данные сервиса · ${date(p.observed_at)}</div></div>`).join("")}</div>`;
}

export function mountUsage(container: HTMLElement, api: Fetcher): void {
  const panel = container.querySelector<HTMLElement>(".usage-panel");
  if (!panel) return;
  const refresh = async () => {
    if (!panel.isConnected) return;
    await Promise.allSettled([
      api<Usage>("/api/usage").then(data => {
        if (!panel.isConnected) return;
        const open = !!panel.querySelector<HTMLDetailsElement>("details")?.open;
        panel.querySelector<HTMLElement>("[data-usage]")!.innerHTML = countsHTML(data);
        const details = panel.querySelector<HTMLDetailsElement>("details");
        if (details) details.open = open;
        panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = "Учёт агента";
      }).catch(() => {
        if (panel.isConnected) panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = "Счётчик недоступен";
      }),
      api<{providers: Allowance[]}>("/api/allowances").then(data => {
        if (panel.isConnected) panel.querySelector<HTMLElement>("[data-allowances]")!.innerHTML = allowancesHTML(data.providers);
      }).catch(() => {
        if (panel.isConnected) panel.querySelector<HTMLElement>("[data-allowances]")!.innerHTML = '<p class="usage-muted">Не удалось обновить квоты подписки</p>';
      }),
    ]);
    if (panel.isConnected) window.setTimeout(refresh, 15000);
  };
  void refresh();
}
