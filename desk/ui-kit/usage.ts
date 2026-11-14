import { t as uiText, locale } from "../lang/lang";
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
const num = (n: number | null | undefined) => n == null ? "—" : new Intl.NumberFormat(locale(), { maximumFractionDigits: 1, notation: n >= 10000 ? "compact" : "standard" }).format(n);
const exact = (n: number | null) => n == null ? uiText("ui.ui.kit.usage.222b4bc59f") : new Intl.NumberFormat(locale()).format(n);
const clamp = (n: number) => Math.max(0, Math.min(100, n));
const date = (n: number) => new Date(n * 1000).toLocaleString(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

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
  const head = uiText("ui.ui.kit.usage.5527d576a3");
  const chats = s.by_chat.slice(0, 5).map((c) => row(c.title || c.chat_id || "—", c.title && c.chat_id ? c.chat_id : "", c)).join("");
  const restChats = s.by_chat.length > 5 ? `<p class="usage-muted">${uiText("ui.usage.more_chats", { count: num(s.by_chat.length - 5) })}</p>` : "";
  const people = s.by_person.slice(0, 5).map((p) => row(p.name || p.who, p.chats.slice(0, 2).join(" · "), p)).join("");
  const unbound = s.summary.unbound_calls ? uiText("ui.ui.kit.usage.68d557d34b", {p0: String(num(s.summary.unbound_calls))}) : "";
  return uiText("ui.ui.kit.usage.cb89953c86", {p0: String(spendOpen ? "open" : ""), p1: String(s.days), p2: String(s.by_chat.length), p3: String(head), p4: String(chats), p5: String(restChats), p6: String(head), p7: String(people), p8: String(unbound)});
}

export function usageShell(compact = false): string {
  const body = uiText("ui.ui.kit.usage.94450ee689");
  if (!compact) return uiText("ui.ui.kit.usage.282741e075", {p0: String(body)});
  return uiText("ui.ui.kit.usage.6313c111a2", {p0: String(overviewOpen ? "open" : ""), p1: String(body)});
}

function briefHTML(data: Usage): string {
  if (data.status !== "ok" || !data.today_total || !data.week_total) return `<span>${esc(data.reason || uiText("ui.ui.kit.usage.40f3b72327"))}</span>`;
  return uiText("ui.ui.kit.usage.ad84a6ad85", {p0: String(num(data.today_total.total_tokens)), p1: String(num(data.week_total.total_tokens))});
}

function limitsBriefHTML(providers: Allowance[]): string {
  if (!providers.length) return uiText("ui.ui.kit.usage.4793e70ae9");
  return providers.map(p => {
    const windows = p.windows.filter(w => w.remaining_percent != null);
    const lowest = windows.reduce<Allowance["windows"][number] | undefined>((a, w) => !a || w.remaining_percent! < a.remaining_percent! ? w : a, undefined);
    if (!lowest) return uiText("ui.ui.kit.usage.be79797986", {p0: String(esc(p.name))});
    const left = clamp(lowest.remaining_percent!);
    const low = left <= 10;
    // Дизайнер 29.09: «использовано/осталось» людям — полоской, а не одной строчкой.
    return uiText("ui.ui.kit.usage.22c3d43910", {p0: String(low ? "usage-low" : ""), p1: String(esc(p.name)), p2: String(esc(lowest.label)), p3: String(p.status === "stale" ? uiText("ui.ui.kit.usage.8a8d7f62b3") : ""), p4: String(num(left)), p5: String(esc(p.name + " · " + lowest.label)), p6: String(left), p7: String(low ? "usage-low" : ""), p8: String(left)});
  }).join("");
}

function countsHTML(data: Usage): string {
  if (data.status !== "ok" || !data.today_total || !data.week_total) return `<p class="usage-muted">${esc(data.reason || uiText("ui.ui.kit.usage.40f3b72327"))}</p>`;
  const today = data.today_total, week = data.week_total;
  const max = Math.max(1, ...data.days.map(d => d.calls));
  const daily = data.days.map(d => uiText("ui.ui.kit.usage.dffbe17a79", {p0: String(esc(d.day)), p1: String(exact(d.calls)), p2: String(exact(d.total_tokens)), p3: String(d.calls ? Math.max(5, d.calls / max * 100) : 0), p4: String(esc(d.day.slice(8)))})).join("");
  const rows = (data.models.length ? data.models : data.roles).map(row => `<tr><th scope="row">${esc(row.name || uiText("ui.ui.kit.usage.a0137daf33"))}</th><td>${num(row.calls)}</td><td>${num(row.input_tokens)}</td><td>${num(row.output_tokens)}</td><td>${row.cache_percent == null ? "—" : num(row.cache_percent) + "%"}</td></tr>`).join("");
  return uiText("ui.ui.kit.usage.70d57f77ff", {p0: String(exact(today.total_tokens)), p1: String(num(today.total_tokens)), p2: String(num(today.calls)), p3: String(exact(week.total_tokens)), p4: String(num(week.total_tokens)), p5: String(num(week.calls)), p6: String(week.cache_percent == null ? uiText("ui.ui.kit.usage.990290fe4e") : num(week.cache_percent) + uiText("ui.ui.kit.usage.0e01799b87")), p7: String(esc(data.days.map(d => d.day + ': ' + d.calls).join('; '))), p8: String(daily), p9: String(data.models.length || data.roles.length), p10: String(rows), p11: String(week.legacy_semantics ? uiText("ui.ui.kit.usage.f6b234f043") : uiText("ui.ui.kit.usage.085fd39ca6"))});
}

function allowancesHTML(providers: Allowance[]): string {
  if (!providers.length) return uiText("ui.ui.kit.usage.9d4f1d28a3");
  return `<div class="usage-allowances">${providers.map(p => uiText("ui.ui.kit.usage.e1d994652c", {p0: String(esc(p.name)), p1: String(p.status === "stale" ? uiText("ui.ui.kit.usage.474a75b708") : uiText("ui.ui.kit.usage.f0a4daddb0")), p2: String(p.windows.length ? p.windows.map(w => {
    const remaining = w.remaining_percent == null ? null : clamp(w.remaining_percent);
    const meter = remaining == null ? "" : uiText("ui.ui.kit.usage.665d35cd70", {p0: String(esc(p.name + ' · ' + w.label)), p1: String(remaining), p2: String(remaining <= 10 ? "usage-low" : ""), p3: String(remaining)});
    return `<div class="usage-quota"><div class="usage-quota-label"><span>${esc(w.label)}</span><strong>${remaining == null ? uiText("ui.ui.kit.usage.3592f634c6") : num(remaining) + uiText("ui.ui.kit.usage.47dd624969")}</strong></div>${meter}<div class="usage-quota-foot"><span>${w.remaining != null && w.limit != null ? `${num(w.remaining)} / ${num(w.limit)} ${w.unit === "credits" ? uiText("ui.ui.kit.usage.e82c376ba0") : uiText("ui.ui.kit.usage.14eaabc8ee")}` : w.used_percent == null ? uiText("ui.ui.kit.usage.13de0b1d16") : num(w.used_percent) + uiText("ui.ui.kit.usage.b1a7d50b06")}</span><span>${w.resets_at ? uiText("ui.ui.kit.usage.b757d077a4") + date(w.resets_at) : uiText("ui.ui.kit.usage.3d268c9adf")}</span></div></div>`;
  }).join("") : `<p class="usage-muted">${esc(p.reason || uiText("ui.ui.kit.usage.abb71b26d3"))}</p>`), p3: String(date(p.observed_at))})).join("")}</div>`;
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
        panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = uiText("ui.ui.kit.usage.2f1d5fbebb");
      }).catch(() => {
        if (panel.isConnected) {
          panel.querySelector<HTMLElement>(".usage-heading > span")!.textContent = uiText("ui.ui.kit.usage.40f3b72327");
          if (brief) brief.textContent = uiText("ui.ui.kit.usage.c5f2b6e42e");
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
          panel.querySelector<HTMLElement>("[data-allowances]")!.innerHTML = uiText("ui.ui.kit.usage.86ad16fecb");
          if (limits) limits.textContent = uiText("ui.ui.kit.usage.f5067d631e");
        }
      }),
    ]);
    if (panel.isConnected) window.setTimeout(refresh, 15000);
  };
  void refresh();
}
