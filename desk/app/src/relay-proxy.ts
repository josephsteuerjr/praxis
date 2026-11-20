import { t as uiText } from "../../lang/lang";
import { shell } from "../../ui-kit/window/api";
import { button, choice, el, field } from "../../ui-kit/dom";
import { humanError } from "../../ui-kit/window/lib";

type ProxySettings = { mode: string; protocol: string; host: string; port: string; username: string; password: string; [key: string]: unknown };
/** A saved Hélène bridge that serves a local HTTP proxy on this computer. */
export type BridgeSource = { id: string; name: string; port: number };
type Options = {
  /** Telegram and Mini App route (HTTP/HTTPS only); otherwise the ChatGPT relay route. */
  application?: boolean;
  /** Bridge this route is bound to in the saved settings. */
  bridge?: string;
};
const TRAY = { protocol: "http", host: "127.0.0.1", port: "8888" };

/**
 * One consumer's route: system settings, direct, one of the user's Hélène
 * bridges, or a custom proxy (Tray Bridge, VPN, any address). 2.8.12 (owner,
 * 20.11): sources and consumers are separate, so each route picks a bridge by
 * name instead of copying its address and an «use for this agent» button.
 */
export function relayProxy(saved: unknown, options: Options = {}) {
  const prior = saved && typeof saved === "object" ? saved as Record<string, unknown> : {};
  const custom = { protocol: String(prior.protocol || "http"), host: String(prior.host || ""), port: String(prior.port || ""),
    username: String(prior.username || ""), password: String(prior.password || "") };
  let bridgeId = String(options.bridge || "");
  let sources: BridgeSource[] | null = null;
  const savedMode = String(prior.mode || "system");
  let route = bridgeId ? "bridge:" + bridgeId : savedMode;
  const element = el("section", "network-route");
  element.append(el("h4", "", uiText(options.application ? "route.telegram.title" : "route.relay.title")));
  const choiceBox = el("div");
  const detail = el("div");
  const result = el("p", "receipt"); result.setAttribute("aria-live", "polite");

  const bridgePort = () => {
    const id = route.slice("bridge:".length);
    const source = sources?.find(row => row.id === id);
    if (source) return String(source.port);
    // Until the list loads, the saved address is the bridge's own.
    return bridgeId === id && savedMode === "proxy" && custom.host === "127.0.0.1" ? custom.port : "";
  };
  const value = (): ProxySettings => {
    if (route.startsWith("bridge:"))
      return { ...prior, mode: "proxy", protocol: "http", host: "127.0.0.1", port: bridgePort(), username: "", password: "" };
    return { ...prior, mode: route === "proxy" ? "proxy" : route, ...custom };
  };
  const snapshot = () => JSON.stringify([route, value()]);
  let savedSnapshot = snapshot();
  const changed = () => {
    element.dispatchEvent(new Event("input", { bubbles: true }));
    document.dispatchEvent(new CustomEvent("helene-route-changed"));
    if (!options.application) {
      result.textContent = snapshot() !== savedSnapshot ? uiText("connections.save_before_check") : "";
      updateCheck();
    }
  };

  function drawChoice() {
    const items: Array<{ value: string; title: string; text?: string }> = [
      { value: "system", title: uiText("route.system"), text: uiText("route.system_text") },
      { value: "direct", title: uiText("route.direct"), text: uiText("route.direct_text") },
    ];
    for (const source of sources || [])
      items.push({ value: "bridge:" + source.id, title: uiText("route.bridge", { p0: source.name }),
        text: uiText("route.bridge_text", { p0: "127.0.0.1:" + source.port }) });
    if (route.startsWith("bridge:") && !items.some(item => item.value === route))
      items.push({ value: route, title: uiText("route.bridge_saved"),
        text: sources ? uiText("route.bridge_missing") : (bridgePort() ? "127.0.0.1:" + bridgePort() : "") });
    items.push({ value: "proxy", title: uiText("route.custom"), text: uiText("route.custom_text") });
    if (!items.some(item => item.value === route))
      items.splice(items.length - 1, 0, { value: route, title: uiText("ui.app.src.relay.proxy.f432767d68", { p0: route }) });
    const picker = choice(items, route, next => {
      route = next;
      bridgeId = next.startsWith("bridge:") ? next.slice("bridge:".length) : "";
      detail.hidden = route !== "proxy";
      changed();
    });
    picker.el.setAttribute("aria-label", uiText(options.application ? "route.telegram.title" : "route.relay.title"));
    choiceBox.replaceChildren(picker.el);
  }

  // Custom proxy: address fields plus the external Tray Bridge shortcut.
  const select = (label: string, selected: string, choices: [string, string][], onChange: (v: string) => void) => {
    const wrapper = el("label", "field"); const input = el("select", "field-input");
    if (!choices.some(([key]) => key === selected)) choices = [...choices, [selected, uiText("ui.app.src.relay.proxy.f432767d68", { p0: String(selected) })]];
    for (const [key, title] of choices) { const option = el("option", "", title); option.value = key; input.append(option); }
    input.value = selected; input.addEventListener("change", () => { onChange(input.value); changed(); });
    wrapper.append(el("span", "", label), input); return { wrapper, input };
  };
  const protocol = select(uiText("ui.app.src.relay.proxy.b3680d4b20"), custom.protocol, options.application
    ? [["http", "HTTP"], ["https", "HTTPS"]]
    : [["http", "HTTP"], ["https", "HTTPS"], ["socks5h", uiText("ui.app.src.relay.proxy.bc36b74c5e")], ["socks5", uiText("ui.app.src.relay.proxy.a525836f44")]],
    v => custom.protocol = v);
  const input = (label: string, key: "host" | "port" | "username" | "password", extra = {}) => {
    const node = field(label, custom[key], v => { custom[key] = v; changed(); }, extra);
    return { node, set: (v: string) => { node.querySelector("input")!.value = v; custom[key] = v; } };
  };
  const host = input(uiText("ui.app.src.relay.proxy.5a03382f19"), "host", { placeholder: "127.0.0.1", mono: true });
  const port = input(uiText("ui.app.src.relay.proxy.90947197f8"), "port", { type: "number", placeholder: "8888", mono: true });
  const grid = el("div", "form-grid two");
  grid.append(protocol.wrapper, host.node, port.node,
    input(uiText("ui.app.src.relay.proxy.b12a661d08"), "username").node,
    input(uiText("ui.app.src.relay.proxy.cb1a2074b3"), "password", { type: "password" }).node);
  const trayNote = el("p", "receipt"); trayNote.setAttribute("aria-live", "polite");
  const tray = button(uiText("route.tray_fill"), "quiet", async () => {
    const same = custom.protocol === TRAY.protocol && custom.host === TRAY.host && custom.port === TRAY.port;
    custom.protocol = TRAY.protocol; protocol.input.value = TRAY.protocol; host.set(TRAY.host); port.set(TRAY.port);
    changed();
    const filled = uiText(options.application ? (same ? "connections.tray_already_telegram" : "connections.tray_filled_telegram")
      : (same ? "connections.tray_already_relay" : "connections.tray_filled_relay"));
    trayNote.textContent = filled + " " + uiText("connections.tray_probing");
    try {
      const probe = await shell<{ ok: boolean; state: string }>("local_proxy_probe", { port: Number(TRAY.port) });
      trayNote.textContent = filled + " " + uiText(probe.ok ? "connections.tray_alive"
        : probe.state === "absent" ? "connections.tray_absent" : "connections.tray_not_proxy");
    } catch {
      trayNote.textContent = filled;
    }
  });
  detail.append(el("p", "field-hint", uiText("route.custom_hint")), tray, trayNote, grid);
  detail.hidden = route !== "proxy";

  let checking = false;
  const updateCheck = () => { check.disabled = checking || snapshot() !== savedSnapshot; };
  const check = button(uiText("ui.app.src.relay.proxy.ff324286b5"), "quiet", async () => {
    const checked = savedSnapshot;
    checking = true; updateCheck(); result.textContent = uiText("ui.app.src.relay.proxy.fe7b960445");
    try {
      const response = await shell<{ ok: boolean; http_status?: number; error_code?: string }>("relay_network_check");
      if (checked !== savedSnapshot || checked !== snapshot()) return;
      result.textContent = response.ok ? uiText("ui.app.src.relay.proxy.1e1f9bb15c")
        : response.http_status ? uiText("ui.app.src.relay.proxy.8d3f165c4c", { p0: String(response.http_status) })
        : response.error_code === "unsupported" ? uiText("connections.check_unsupported")
        : response.error_code === "timeout" ? uiText("connections.check_timeout")
        : response.error_code === "redirected" ? uiText("connections.check_redirected")
        : response.error_code === "transport" ? uiText("connections.check_transport")
        : uiText("ui.app.src.relay.proxy.5f2af98a51");
    } catch (error) {
      if (checked === savedSnapshot && checked === snapshot()) result.textContent = humanError(error).text;
    } finally { checking = false; updateCheck(); }
  });
  element.append(choiceBox, detail, el("p", "field-hint", uiText(options.application
    ? "ui.app.src.relay.proxy.ef07983c23" : "ui.app.src.relay.proxy.3af67a39de")));
  if (!options.application) element.append(check, result);
  drawChoice();
  updateCheck();

  return {
    element,
    collect: (): ProxySettings => value(),
    /** Bridge id when this route goes through one of the user's bridges. */
    bridge: () => route.startsWith("bridge:") ? route.slice("bridge:".length) : "",
    /** The user's bridges that serve a local proxy; null until known. */
    setSources: (next: BridgeSource[]) => {
      const before = snapshot();
      sources = next; drawChoice();
      // A bridge address learned after loading is not an edit by the person.
      if (savedSnapshot === before) savedSnapshot = snapshot();
      updateCheck();
    },
    didSave: () => {
      if (savedSnapshot === snapshot()) return;
      savedSnapshot = snapshot(); result.textContent = ""; updateCheck();
    },
    error: () => {
      if (route.startsWith("bridge:")) return bridgePort() ? "" : uiText("route.bridge_missing");
      if (!["system", "direct", "proxy"].includes(route)) return uiText("ui.app.src.relay.proxy.39ab6758e8");
      if (route !== "proxy") return "";
      if (!(options.application ? ["http", "https"] : ["http", "https", "socks5", "socks5h"]).includes(custom.protocol) || !custom.host.trim()
        || /[\/@?#\\\s]/.test(custom.host) || !/^\d+$/.test(custom.port) || Number(custom.port) < 1 || Number(custom.port) > 65535)
        return uiText("ui.app.src.relay.proxy.52f74959f8");
      if (custom.password && !custom.username) return uiText("ui.app.src.relay.proxy.880cabd9ea");
      return "";
    },
  };
}
