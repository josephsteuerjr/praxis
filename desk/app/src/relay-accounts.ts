import { shell } from "../../ui-kit/window/api";
import { button, copyText } from "../../ui-kit/dom";
import { el, humanError, toast } from "../../ui-kit/window/lib";

type Slot = "primary" | "secondary";
type SlotView = { slot: Slot; stored?: boolean; pending?: boolean; serving?: boolean; loaded?: boolean; cooldown_seconds_left?: number; login_error?: string };
type AccountView = { reachable?: boolean; active_slot?: Slot; port?: number; slots?: SlotView[] };
type Surface = HTMLElement & { onMount?: () => void; onDispose?: () => void };

/** Two persistent identities, never two labels for the same login command. */
export function relayAccounts() {
  if (!customElements.get("helene-relay-accounts")) {
    customElements.define("helene-relay-accounts", class extends HTMLElement {
      connectedCallback() { (this as Surface).onMount?.(); }
      disconnectedCallback() { (this as Surface).onDispose?.(); }
    });
  }
  const element = document.createElement("helene-relay-accounts") as Surface;
  element.style.display = "block";
  const overview = el("p", "field-hint", "Проверяю сохранённые входы…");
  const rows = new Map<Slot, { status: HTMLElement; login: HTMLButtonElement; logout: HTMLButtonElement; use: HTMLButtonElement; links: HTMLElement; url: string }>();
  let timer = 0, epoch = 0, inflight = false, changing = false, authorized = false;
  const stop = () => { ++epoch; window.clearTimeout(timer); timer = 0; };
  element.onDispose = stop;
  const schedule = () => {
    window.clearTimeout(timer);
    if (element.isConnected) timer = window.setTimeout(() => void refresh(), 2000);
  };
  async function refresh() {
    if (inflight) return;
    const generation = epoch;
    inflight = true;
    try {
      const view = await shell<AccountView>("relay_account");
      if (generation !== epoch) return;
      authorized = !!view.slots?.some(row => row.stored);
      overview.textContent = view.reachable
        ? `Реле отвечает · работает ${view.active_slot === "secondary" ? "слот 2" : "слот 1"}. Выбор применяется сразу и сохраняется реле.`
        : `Состояние реле на порту ${view.port ?? 5011} недоступно. Сохранённый вход не подтверждает работающую подписку.`;
      let pending = false;
      for (const [slot, row] of rows) {
        const state = view.slots?.find(item => item.slot === slot);
        pending ||= !!state?.pending;
        row.status.textContent = state?.pending ? "Ждём вход в браузере" : state?.login_error || (state?.stored
          ? state.serving ? "Активен" : state.loaded ? "Вход сохранён · резерв" : "Вход сохранён · ждёт применения"
          : state?.loaded ? "Вход удалён · реле ещё применяет выход" : "Нет сохранённого входа");
        if (state?.cooldown_seconds_left) row.status.textContent += ` · пауза ${Math.ceil(state.cooldown_seconds_left / 60)} мин`;
        row.login.textContent = state?.pending ? "Начать вход заново" : state?.stored ? "Сменить аккаунт" : "Войти";
        row.login.disabled = changing;
        row.logout.disabled = changing || !state?.stored && !state?.pending;
        row.use.disabled = changing || !state?.stored || !view.reachable || !!state?.serving;
        row.use.textContent = state?.serving ? "Используется" : "Использовать";
        row.url = state?.pending ? (await shell<string | null>("relay_login_url", { slot })) || "" : "";
        if (generation !== epoch) return;
        row.links.hidden = !row.url;
        row.links.style.display = row.url ? "flex" : "none";
      }
      if (pending || changing || view.slots?.some(row => row.stored !== !!row.loaded) && view.reachable) schedule();
    } catch (error) {
      if (generation === epoch) overview.textContent = "Состояние недоступно: " + humanError(error).text;
    } finally { inflight = false; if (generation !== epoch && element.isConnected) schedule(); }
  }
  async function act(command: string, slot: Slot) {
    if (changing) return;
    changing = true;
    for (const row of rows.values()) for (const control of [row.login, row.logout, row.use]) control.disabled = true;
    try { await shell(command, { slot }); }
    catch (error) { toast(humanError(error).text); }
    finally { changing = false; await refresh(); schedule(); }
  }
  for (const [slot, title] of [["primary", "Слот 1"], ["secondary", "Слот 2"]] as const) {
    const section = el("section", "subscription-slot");
    section.style.cssText = "padding:14px 0;border-bottom:1px solid var(--line, #6b5c42)";
    const status = el("p", "field-hint", "Проверяю…");
    status.setAttribute("aria-live", "polite");
    const login = button("Войти", "quiet", () => void act("relay_login", slot));
    const logout = button("Выйти", "quiet", () => void act("relay_logout", slot));
    const use = button("Использовать", "quiet", () => void act("relay_switch", slot));
    use.disabled = true; logout.disabled = true; // unknown until the first state observation
    const actions = el("div", "actions"); actions.append(use, login, logout);
    const links = el("div", "actions"); links.hidden = true; links.style.display = "none";
    links.append(button("Открыть страницу входа", "quiet", async () => {
      try { await shell("open_login_page", { slot }); } catch (error) { toast(humanError(error).text); }
    }), button("Скопировать ссылку", "quiet", async () => {
      toast(await copyText(rows.get(slot)?.url || "") ? "Ссылка скопирована" : "Ссылка недоступна");
    }));
    section.append(el("h4", "", title), status, actions, links);
    rows.set(slot, { status, login, logout, use, links, url: "" });
    element.append(section);
  }
  element.prepend(el("p", "field-hint", "Вход, выход и выбор активного слота выполняются сразу — кнопка «Сохранить» для них не нужна."), overview);
  const reload = button("Обновить состояние", "quiet", () => void refresh());
  reload.style.marginTop = "12px";
  element.append(reload,
    el("p", "field-hint", "Вход и выход действуют сразу, отдельно от «Сохранить». Новый вход отменяет незавершённую попытку в другом слоте. Выход удаляет только локальный вход этого слота, не завершает сессию на сайте ChatGPT."));
  element.onMount = () => { ++epoch; void refresh(); };
  return { element, refresh, authorized: () => authorized };
}
