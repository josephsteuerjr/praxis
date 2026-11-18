import { t as uiText } from "../../lang/lang";
import { shell } from "../../ui-kit/window/api";
import { button, el, field } from "../../ui-kit/dom";
import { humanError } from "../../ui-kit/window/lib";

type ProxySettings = { mode: string; protocol: string; host: string; port: string; username: string; password: string; [key: string]: unknown };
export function relayProxy(saved: unknown, options: { application?: boolean } = {}) {
  const prior = saved && typeof saved === "object" ? saved as Record<string, unknown> : {};
  const value: ProxySettings = { ...prior, mode: String(prior.mode || "system"), protocol: String(prior.protocol || "http"),
    host: String(prior.host || ""), port: String(prior.port || ""), username: String(prior.username || ""), password: String(prior.password || "") };
  const element = el("section", ""); element.style.marginTop = "24px";
  element.append(el("h4", "", options.application ? uiText("ui.app.src.settings.agent.e2d6eafc73")
    : uiText("ui.app.src.relay.proxy.2f5eaed169")));
  const select = (label: string, selected: string, choices: [string, string][], changed: (v: string) => void) => {
    const wrapper = el("label", "field"); const input = el("select", "field-input");
    if (!choices.some(([key]) => key === selected)) choices = [...choices, [selected, uiText("ui.app.src.relay.proxy.f432767d68", {p0: String(selected)})]];
    for (const [key, title] of choices) { const option = el("option", "", title); option.value = key; input.append(option); }
    input.value = selected; input.addEventListener("change", () => changed(input.value));
    wrapper.append(el("span", "", label), input); return wrapper;
  };
  const detail = el("div", "form-grid two"); detail.style.marginTop = "12px";
  const show = () => { detail.hidden = value.mode !== "proxy"; detail.style.display = detail.hidden ? "none" : "grid"; };
  element.append(select(uiText("ui.app.src.relay.proxy.9c2c7eeb43"), value.mode, [["system", uiText("ui.app.src.relay.proxy.65053aab1f")], ["direct", uiText("ui.app.src.relay.proxy.79248c4e87")], ["proxy", uiText("ui.app.src.relay.proxy.7d5193bc84")]], mode => { value.mode = mode; show(); }));
  detail.append(select(uiText("ui.app.src.relay.proxy.b3680d4b20"), value.protocol, options.application ? [["http", "HTTP"], ["https", "HTTPS"]] : [["http", "HTTP"], ["https", "HTTPS"], ["socks5h", uiText("ui.app.src.relay.proxy.bc36b74c5e")], ["socks5", uiText("ui.app.src.relay.proxy.a525836f44")]], v => value.protocol = v),
    field(uiText("ui.app.src.relay.proxy.5a03382f19"), value.host, v => value.host = v, { placeholder: "proxy.example.org", mono: true }),
    field(uiText("ui.app.src.relay.proxy.90947197f8"), value.port, v => value.port = v, { type: "number", placeholder: "1080", mono: true }),
    field(uiText("ui.app.src.relay.proxy.b12a661d08"), value.username, v => value.username = v),
    field(uiText("ui.app.src.relay.proxy.cb1a2074b3"), value.password, v => value.password = v, { type: "password" }));
  const result = el("p", "receipt"); result.setAttribute("aria-live", "polite");
  const snapshot = () => JSON.stringify(value);
  let savedSnapshot = snapshot();
  let checking = false;
  const updateCheck = () => { check.disabled = checking || snapshot() !== savedSnapshot; };
  const check = button(uiText("ui.app.src.relay.proxy.ff324286b5"), "quiet", async () => {
    const checked = savedSnapshot;
    checking = true; updateCheck(); result.textContent = uiText("ui.app.src.relay.proxy.fe7b960445");
    try {
      const response = await shell<{ ok: boolean; http_status?: number; error_code?: string }>("relay_network_check");
      if (checked !== savedSnapshot || checked !== snapshot()) return;
      result.textContent = response.ok ? uiText("ui.app.src.relay.proxy.1e1f9bb15c")
        : response.http_status ? uiText("ui.app.src.relay.proxy.8d3f165c4c", {p0: String(response.http_status)})
        : response.error_code === 'unsupported' ? uiText('connections.check_unsupported')
        : response.error_code === 'timeout' ? uiText('connections.check_timeout')
        : response.error_code === 'redirected' ? uiText('connections.check_redirected')
        : response.error_code === 'transport' ? uiText('connections.check_transport')
        : uiText("ui.app.src.relay.proxy.5f2af98a51");
    } catch (error) {
      if (checked === savedSnapshot && checked === snapshot()) result.textContent = humanError(error).text;
    } finally { checking = false; updateCheck(); }
  });
  element.append(detail, el("p", "field-hint", options.application
    ? uiText("ui.app.src.relay.proxy.ef07983c23")
    : uiText("ui.app.src.relay.proxy.3af67a39de")));
  if (!options.application) {
    const routeEdited = () => {
      result.textContent = snapshot() !== savedSnapshot ? uiText('connections.save_before_check') : '';
      updateCheck();
    };
    element.addEventListener('input', routeEdited);
    element.addEventListener('change', routeEdited);
    element.append(check, result);
  }
  show();
  return { element, collect: () => ({ ...value }), set: (next:Partial<ProxySettings>) => {
    Object.assign(value,next);
    const selects=element.querySelectorAll<HTMLSelectElement>('select');
    [value.mode,value.protocol].forEach((item,index)=>{selects[index].value=item;selects[index].dispatchEvent(new Event('change',{bubbles:true}))});
    const inputs=detail.querySelectorAll<HTMLInputElement>('input');
    [value.host,value.port,value.username,value.password].forEach((item,index)=>{inputs[index].value=item;inputs[index].dispatchEvent(new Event('input',{bubbles:true}))});
    show();
  }, didSave: () => {
    if (savedSnapshot === snapshot()) return;
    savedSnapshot = snapshot(); result.textContent = ''; updateCheck();
  }, error: () => {
    if (!["system", "direct", "proxy"].includes(value.mode)) return uiText("ui.app.src.relay.proxy.39ab6758e8");
    if (value.mode !== "proxy") return "";
    if (!(options.application ? ["http", "https"] : ["http", "https", "socks5", "socks5h"]).includes(value.protocol) || !value.host.trim() || /[\/@?#\\\s]/.test(value.host) || !/^\d+$/.test(value.port) || Number(value.port) < 1 || Number(value.port) > 65535)
      return uiText("ui.app.src.relay.proxy.52f74959f8");
    if (value.password && !value.username) return uiText("ui.app.src.relay.proxy.880cabd9ea");
    return "";
  } };
}
