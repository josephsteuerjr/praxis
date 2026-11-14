import { t as uiText } from "../../lang/lang";
import { shell, electron } from "../../ui-kit/window/api";
import { button, copyText } from "../../ui-kit/dom";
import { el, humanError, toast } from "../../ui-kit/window/lib";

type Slot = "primary" | "secondary";
type SlotView = { slot: Slot; stored?: boolean; pending?: boolean; serving?: boolean; loaded?: boolean; login_error?: string; cooldown_seconds_left?:number };
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
  const overview = el("p", "field-hint", uiText("ui.app.src.relay.accounts.5e552214a6"));
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
        ? uiText("ui.app.src.relay.accounts.040f26cc3c") + (view.active_slot ? uiText("ui.app.src.relay.accounts.68198697cb") + (view.active_slot === "secondary" ? 2 : 1) : "") + uiText("ui.app.src.relay.accounts.e43d98830d")
        : view.note || uiText("ui.app.src.relay.accounts.9697064888") + (view.port ?? 5011) + uiText("ui.app.src.relay.accounts.84055e7b4d");
      const pending = !!view.login?.pending || !!view.slots?.some(row => row.pending);
      for (const [slot, row] of rows) {
        const state = view.slots?.find(item => item.slot === slot);
        const login = view.login?.slot === slot ? view.login : undefined;
        const ownPending = !!login?.pending || !!state?.pending;
        const device = ownPending && login?.method === "device_code" ? login.device_code : undefined;
        const expired = !!login?.expired || !!device && device.expires_at * 1000 <= Date.now();
        row.status.textContent = ownPending
          ? expired ? uiText("ui.app.src.relay.accounts.d4637d452c")
            : login?.method === "device_code" ? device ? uiText("ui.app.src.relay.accounts.c6117e2b06") : uiText("ui.app.src.relay.accounts.4646360773")
            : uiText("ui.app.src.relay.accounts.2b272af143")
          : state?.login_error || (state?.stored ? state.cooldown_seconds_left && state.cooldown_seconds_left > 0 ? uiText("ui.app.src.relay.accounts.56b48526be", {p0: String(Math.ceil(state.cooldown_seconds_left/60))}) : state.serving ? uiText("ui.app.src.relay.accounts.a87a4b39c3") : uiText("ui.app.src.relay.accounts.cd7fb0fc87") : uiText("ui.app.src.relay.accounts.30e92f0fbc"));
        row.login.textContent = state?.stored ? uiText("ui.app.src.relay.accounts.64248af843") : uiText("ui.app.src.relay.accounts.b1448aaf65");
        row.login.disabled = row.browser.disabled = changing || pending || !view.reachable;
        row.browser.hidden = view.browser_login === false || electron?.presentation === 'miniapp';
        row.logout.disabled = changing || pending || !state?.stored || !view.reachable;
        row.use.disabled = changing || pending || !state?.stored || !view.reachable || !!state?.serving;
        row.use.textContent = state?.serving ? uiText("ui.app.src.relay.accounts.7c00a93b31") : uiText("ui.app.src.relay.accounts.2c3624a418");
        row.loginId = ownPending ? login?.id : undefined;
        row.cancel.hidden = !ownPending;
        row.cancel.disabled = changing || !row.loginId;
        row.codeValue = device && !expired ? device.user_code : "";
        row.code.textContent = row.codeValue;
        row.prompt.hidden = !row.codeValue;
        row.copy.disabled = !row.codeValue;
        row.expires.textContent = device && !expired ? uiText("ui.app.src.relay.accounts.d27a5b4a9e") + Math.ceil((device.expires_at * 1000 - Date.now()) / 60000) + uiText("ui.app.src.relay.accounts.a2d5b04ba3") : "";
        row.url = ownPending && !expired ? login?.url || "" : "";
        row.links.hidden = !row.url;
        row.links.style.display = row.url ? "flex" : "none";
      }
      schedule(); // Quota failover can change the serving account without a UI action.
    } catch (error) {
      if (generation === epoch) {
        overview.textContent = uiText("ui.app.src.relay.accounts.5b29439c51") + humanError(error).text;
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
  for (const [slot, title] of [["primary", uiText("ui.app.src.relay.accounts.b1ff8a89e8")], ["secondary", uiText("ui.app.src.relay.accounts.4f44657afd")]] as const) {
    const section = el("section", "subscription-slot");
    section.style.cssText = "padding:14px 0;border-bottom:1px solid var(--line, #6b5c42)";
    const status = el("p", "field-hint", uiText("ui.app.src.relay.accounts.74d0346f0c")); status.setAttribute("aria-live", "polite");
    const login = button(uiText("ui.app.src.relay.accounts.b1448aaf65"), "quiet", () => void act("relay_login", slot, { method: "device_code" }));
    const browser = button(uiText("ui.app.src.relay.accounts.f051b6bbec"), "quiet", () => void act("relay_login", slot, { method: "browser" }));
    const cancel = button(uiText("ui.app.src.relay.accounts.a4ee02a4c1"), "quiet", () => void act("relay_cancel_login", slot, { login_id: rows.get(slot)?.loginId }));
    cancel.hidden = true;
    const logout = button(uiText("ui.app.src.relay.accounts.5690c2e63b"), "quiet", () => void act("relay_logout", slot));
    const use = button(uiText("ui.app.src.relay.accounts.2c3624a418"), "quiet", () => void act("relay_switch", slot));
    use.disabled = logout.disabled = login.disabled = browser.disabled = true;
    const actions = el("div", "actions"); actions.append(use, login, browser, logout, cancel);
    const prompt = el("div", "subscription-device-code"); prompt.hidden = true;
    const code = el("code"); code.style.cssText = "display:block;font-size:1.6em;letter-spacing:.12em;overflow-wrap:anywhere;padding:12px 0;user-select:text";
    const copy = button(uiText("ui.app.src.relay.accounts.3ae997a8f3"), "quiet", async () => {
      const value = rows.get(slot)?.codeValue;
      toast(value && await copyText(value) ? uiText("ui.app.src.relay.accounts.f2fd139eb7") : uiText("ui.app.src.relay.accounts.99f345252e"));
    });
    const expires = el("p", "field-hint"); prompt.append(code, copy, expires);
    const links = el("div", "actions"); links.hidden = true; links.style.display = "none";
    links.append(button(uiText("ui.app.src.relay.accounts.23b5fabf7b"), "quiet", async () => {
      try { await transport("open_login_page", { slot }); } catch (error) { toast(humanError(error).text); }
    }), button(uiText("ui.app.src.relay.accounts.8d81013698"), "quiet", async () => {
      const value = rows.get(slot)?.url;
      toast(value && await copyText(value) ? uiText("ui.app.src.relay.accounts.bb67fa3d1a") : uiText("ui.app.src.relay.accounts.60423d940a"));
    }));
    section.append(el("h4", "", title), status, actions, prompt, links);
    rows.set(slot, { status, login, browser, cancel, logout, use, prompt, code, expires, copy, links, codeValue: "", url: "" });
    element.append(section);
  }
  element.prepend(overview);
  const reload = button(uiText("ui.app.src.relay.accounts.d1005e64bf"), "quiet", () => void refresh()); reload.style.marginTop = "12px";
  element.append(reload, el("p", "field-hint", uiText("ui.app.src.relay.accounts.2c3fdb2ff6")));
  element.onMount = () => { ++epoch; void refresh(); };
  return { element, refresh, authorized: () => authorized };
}
