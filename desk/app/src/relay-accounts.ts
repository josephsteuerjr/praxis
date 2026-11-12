import { shell, electron } from "../../ui-kit/window/api";
import { button, copyText } from "../../ui-kit/dom";
import { el, humanError, toast } from "../../ui-kit/window/lib";

type Slot = "primary" | "secondary";
type SlotView = { slot: Slot; stored?: boolean; pending?: boolean; serving?: boolean; loaded?: boolean; login_error?: string };
export type RelayLogin = { pending?: boolean; slot?: Slot; id?: string; method?: string; url?: string; error?: string; expired?: boolean;
  device_code?: { user_code: string; expires_at: number; verification_url: string } };
export type AccountView = { reachable?: boolean; active_slot?: Slot; port?: number; slots?: SlotView[]; catalog_revision?: string;
  note?: string; browser_login?: boolean; login?: RelayLogin };
type Surface = HTMLElement & { onMount?: () => void; onDispose?: () => void };

/** Shared by desktop onboarding/settings and authenticated server/phone settings. */
export function relayAccounts(transport: typeof shell = shell) {
  if (!customElements.get("helene-relay-accounts")) {
    customElements.define("helene-relay-accounts", class extends HTMLElement {
      connectedCallback() { (this as Surface).onMount?.(); }
      disconnectedCallback() { (this as Surface).onDispose?.(); }
    });
  }
  const element = document.createElement("helene-relay-accounts") as Surface;
  element.style.display = "block";
  const overview = el("p", "field-hint", "Проверяю сохранённые входы…");
  const rows = new Map<Slot, { status: HTMLElement; login: HTMLButtonElement; browser: HTMLButtonElement; cancel: HTMLButtonElement;
    logout: HTMLButtonElement; use: HTMLButtonElement; prompt: HTMLElement; code: HTMLElement; expires: HTMLElement; links: HTMLElement;
    copy: HTMLButtonElement; codeValue: string; url: string; loginId?: string }>();
  let timer = 0, epoch = 0, inflight = false, changing = false, authorized = false;
  let catalogIdentity: string | undefined;
  const stop = () => { ++epoch; window.clearTimeout(timer); timer = 0; };
  element.onDispose = stop;
  const schedule = () => {
    window.clearTimeout(timer);
    if (element.isConnected) timer = window.setTimeout(() => void refresh(), 2000);
  };
  async function refresh() {
    if (inflight) { schedule(); return; }
    const generation = epoch;
    inflight = true;
    try {
      const view = await transport<AccountView>("relay_account");
      if (generation !== epoch) return;
      const identity = JSON.stringify([view.catalog_revision, view.active_slot, view.slots?.map(row => row.stored), view.login?.pending, view.login?.id]);
      const changed = catalogIdentity !== undefined && catalogIdentity !== identity;
      catalogIdentity = identity;
      if (changed) element.dispatchEvent(new CustomEvent("relay-account-change"));
      authorized = !!view.slots?.some(row => row.stored);
      overview.textContent = view.reachable
        ? "Реле отвечает" + (view.active_slot ? " · используется слот " + (view.active_slot === "secondary" ? 2 : 1) : "") + ". Выбор аккаунта сохраняется сразу."
        : view.note || "Реле на порту " + (view.port ?? 5011) + " пока недоступно. Обнови состояние после запуска.";
      const pending = !!view.login?.pending || !!view.slots?.some(row => row.pending);
      for (const [slot, row] of rows) {
        const state = view.slots?.find(item => item.slot === slot);
        const login = view.login?.slot === slot ? view.login : undefined;
        const ownPending = !!login?.pending || !!state?.pending;
        const device = ownPending && login?.method === "device_code" ? login.device_code : undefined;
        const expired = !!login?.expired || !!device && device.expires_at * 1000 <= Date.now();
        row.status.textContent = ownPending
          ? expired ? "Срок кода истёк. Отмени эту попытку и запроси новый код."
            : login?.method === "device_code" ? device ? "Введи код на странице ChatGPT и подтверди вход" : "Запрашиваем код у ChatGPT…"
            : "Ждём подтверждения входа в браузере"
          : state?.login_error || (state?.stored ? state.serving ? "Активен" : "Вход сохранён · резерв" : "Нет сохранённого входа");
        row.login.textContent = state?.stored ? "Сменить аккаунт по коду" : "Войти по коду";
        row.login.disabled = row.browser.disabled = changing || pending || !view.reachable;
        row.browser.hidden = view.browser_login === false || electron?.presentation === 'miniapp';
        row.logout.disabled = changing || pending || !state?.stored || !view.reachable;
        row.use.disabled = changing || pending || !state?.stored || !view.reachable || !!state?.serving;
        row.use.textContent = state?.serving ? "Используется" : "Использовать";
        row.loginId = ownPending ? login?.id : undefined;
        row.cancel.hidden = !ownPending;
        row.cancel.disabled = changing || !row.loginId;
        row.codeValue = device && !expired ? device.user_code : "";
        row.code.textContent = row.codeValue;
        row.prompt.hidden = !row.codeValue;
        row.copy.disabled = !row.codeValue;
        row.expires.textContent = device && !expired ? "Действует ещё " + Math.ceil((device.expires_at * 1000 - Date.now()) / 60000) + " мин. Подтвердить можно на телефоне или ПК." : "";
        row.url = ownPending && !expired ? login?.url || "" : "";
        row.links.hidden = !row.url;
        row.links.style.display = row.url ? "flex" : "none";
      }
      if (pending || changing || !view.reachable) schedule();
    } catch (error) {
      if (generation === epoch) {
        overview.textContent = "Состояние недоступно: " + humanError(error).text;
        // A lost status is not proof of a failed login. Do not start another one.
        for (const row of rows.values()) {
          row.login.disabled = row.browser.disabled = row.use.disabled = row.logout.disabled = true;
          row.prompt.hidden = true; row.links.style.display = "none";
          row.codeValue = ""; row.code.textContent = ""; row.url = "";
        }
        schedule();
      }
    } finally { inflight = false; if (generation !== epoch && element.isConnected) schedule(); }
  }
  async function act(command: string, slot: Slot, extra: Record<string, unknown> = {}) {
    if (changing) return;
    ++epoch; changing = true;
    for (const row of rows.values()) {
      for (const control of [row.login, row.browser, row.cancel, row.logout, row.use]) control.disabled = true;
      row.prompt.hidden = true; row.links.style.display = "none"; row.codeValue = ""; row.code.textContent = "";
    }
    try { await transport(command, { slot, ...extra }); }
    catch (error) { if (element.isConnected) toast(humanError(error).text); }
    finally { changing = false; if (element.isConnected) { await refresh(); schedule(); } }
  }
  for (const [slot, title] of [["primary", "Слот 1"], ["secondary", "Слот 2"]] as const) {
    const section = el("section", "subscription-slot");
    section.style.cssText = "padding:14px 0;border-bottom:1px solid var(--line, #6b5c42)";
    const status = el("p", "field-hint", "Проверяю…"); status.setAttribute("aria-live", "polite");
    const login = button("Войти по коду", "quiet", () => void act("relay_login", slot, { method: "device_code" }));
    const browser = button("Войти через браузер на этом ПК", "quiet", () => void act("relay_login", slot, { method: "browser" }));
    const cancel = button("Отменить вход", "quiet", () => void act("relay_cancel_login", slot, { login_id: rows.get(slot)?.loginId }));
    cancel.hidden = true;
    const logout = button("Выйти", "quiet", () => void act("relay_logout", slot));
    const use = button("Использовать", "quiet", () => void act("relay_switch", slot));
    use.disabled = logout.disabled = login.disabled = browser.disabled = true;
    const actions = el("div", "actions"); actions.append(use, login, browser, logout, cancel);
    const prompt = el("div", "subscription-device-code"); prompt.hidden = true;
    const code = el("code"); code.style.cssText = "display:block;font-size:1.6em;letter-spacing:.12em;overflow-wrap:anywhere;padding:12px 0;user-select:text";
    const copy = button("Скопировать код", "quiet", async () => {
      const value = rows.get(slot)?.codeValue;
      toast(value && await copyText(value) ? "Код скопирован" : "Код уже недоступен");
    });
    const expires = el("p", "field-hint"); prompt.append(code, copy, expires);
    const links = el("div", "actions"); links.hidden = true; links.style.display = "none";
    links.append(button("Открыть страницу входа", "quiet", async () => {
      try { await transport("open_login_page", { slot }); } catch (error) { toast(humanError(error).text); }
    }), button("Скопировать ссылку", "quiet", async () => {
      const value = rows.get(slot)?.url;
      toast(value && await copyText(value) ? "Ссылка скопирована" : "Ссылка недоступна");
    }));
    section.append(el("h4", "", title), status, actions, prompt, links);
    rows.set(slot, { status, login, browser, cancel, logout, use, prompt, code, expires, copy, links, codeValue: "", url: "" });
    element.append(section);
  }
  element.prepend(overview);
  const reload = button("Обновить состояние", "quiet", () => void refresh()); reload.style.marginTop = "12px";
  element.append(reload, el("p", "field-hint", "Вход, выход и выбор аккаунта действуют сразу, отдельно от «Сохранить». Одновременно выполняется один вход. При смене аккаунта прежний сохранённый вход остаётся до успешного подтверждения нового."));
  element.onMount = () => { ++epoch; void refresh(); };
  return { element, refresh, authorized: () => authorized };
}
