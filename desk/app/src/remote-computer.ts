import { t as uiText, UiError } from "../../lang/lang";
/** Desktop access belongs to the local Windows owner; server settings stay on the server. */
import { api, post, shell } from "../../ui-kit/window/api";
import { button, card, el, toggle } from "../../ui-kit/dom";
import { humanError } from "../../ui-kit/window/lib";
import type { EditionContext, Loaded } from "../../ui-kit/window/views/settings-frame";

const prefix = "/api/computer/remote";
const permissions = () => [
  ["computer.read", uiText("ui.app.src.remote.computer.7ac84107c1")], ["computer.apps", uiText("ui.app.src.remote.computer.f719eef1d3")],
  ["computer.files", uiText("ui.app.src.remote.computer.9088ca9c0d")], ["computer.process", uiText("ui.app.src.remote.computer.115c88c9a2")],
] as const;
type Remote = { enabled?: boolean; id?: string; scopes?: string[] };
type Device = { id: string; name: string; connected: boolean; scopes: string[];
  identity?: { scopes?: string[]; system?: boolean } };
type Picture = { selected: string; devices: Device[] };

export async function remoteComputerCard(context: EditionContext): Promise<HTMLElement> {
  const box = el("div");
  box.append(el("p", "dim", uiText("ui.app.src.remote.computer.5cded4e973")));
  const status = el("p", "dim", uiText("ui.app.src.remote.computer.4d76cdebee"));
  const devices = el("div");
  box.append(status, devices);
  let busy = false;
  let rendered = "";
  const refresh = async () => {
    try {
      const picture = await api<Picture>(prefix + "/state");
      const signature = JSON.stringify(picture);
      if (signature === rendered) return; // keep keyboard focus during status polling
      rendered = signature;
      devices.replaceChildren();
      status.textContent = picture.devices.length ? uiText("ui.app.src.remote.computer.7e02add735") : uiText("ui.app.src.remote.computer.a9e80f3816");
      for (const device of picture.devices) {
        const row = el("div", "btn-row");
        const selected = picture.selected === device.id;
        row.append(el("span", "dim", `${device.name}: ${device.connected ? uiText("ui.app.src.remote.computer.e1870f7b6b") : uiText("ui.app.src.remote.computer.befa3ab807")}${selected ? uiText("ui.app.src.remote.computer.99b3db37a6") : ""}`));
        const choose = button(selected ? uiText("ui.app.src.remote.computer.954fea1716") : uiText("ui.app.src.remote.computer.38aa0ac1ff"), "quiet", () => void action(async () => {
          await post(prefix + "/select", { device: device.id });
        }));
        choose.disabled = selected;
        row.append(choose, button(uiText("ui.app.src.remote.computer.de1aec86ab"), "ghost", () => void action(async () => {
          await post(prefix + "/revoke", { device: device.id });
        })));
        devices.append(row);
      }
    } catch (error) { status.textContent = humanError(error).text; }
  };
  const action = async (fn: () => Promise<void>) => {
    if (busy) return;
    busy = true;
    try { await fn(); await refresh(); }
    catch (error) { status.textContent = humanError(error).text; }
    finally { busy = false; }
  };
  await refresh();
  const poll = async () => {
    if (!box.isConnected) return;
    if (!busy) await refresh();
    window.setTimeout(() => void poll(), 3000);
  };
  window.setTimeout(() => void poll(), 3000);
  if (context.host?.platform === "windows") {
    const block = context.saved.computer as Record<string, unknown> | undefined;
    const remote = (block?.remote || {}) as Remote;
    const chosen = new Set(Array.isArray(remote.scopes) ? remote.scopes.filter(scope => permissions().some(([known]) => known === scope)) : remote.id ? [] : permissions().map(([scope]) => scope));
    box.append(el("p", "dim", uiText("ui.app.src.remote.computer.8f82722455")));
    let allowSystem = (context.saved.service as Record<string, unknown> | undefined)?.session0 === true;
    const system = toggle(uiText("ui.app.src.remote.computer.5a788d10a8"), allowSystem, value => { allowSystem = value; });
    const rights = el("div", "mode-block");
    for (const [scope, name] of permissions()) {
      rights.append(toggle(name, chosen.has(scope), value => {
        if (value) chosen.add(scope); else chosen.delete(scope);
        system.disabled = !chosen.has("computer.process");
        if (system.disabled) { allowSystem = false; system.setAttribute("aria-checked", "false"); }
      }));
    }
    system.disabled = !chosen.has("computer.process");
    if (system.disabled) { allowSystem = false; system.setAttribute("aria-checked", "false"); }
    rights.append(system);
    box.append(rights, el("p", "dim", uiText("ui.app.src.remote.computer.9cba420a0e")));
    box.append(el("p", "dim", uiText("ui.app.src.remote.computer.7f0efafc6b")));
    box.append(el("p", "dim", uiText("ui.app.src.remote.computer.9e4fef6896")));
    const acceptLocal = async (result: { ok: boolean; path: string; before_mtime_ns: string; mtime_ns: string }) => {
      if (!result.ok || result.path !== context.loaded.path) throw new UiError(uiText("ui.app.src.remote.computer.83265989ff"));
      const fresh = await shell<Loaded>("config_load");
      if (fresh.path !== context.loaded.path) throw new Error(uiText("ui.app.src.remote.computer.18f4a4b95b"));
      context.draft.computer = fresh.config.computer;
      context.saved.computer = fresh.config.computer;
      context.draft.service = fresh.config.service;
      context.saved.service = fresh.config.service;
      context.freshness.accept?.(result.before_mtime_ns, result.mtime_ns);
      context.loaded.mtime_ns = result.mtime_ns;
    };
    box.append(button(remote.enabled ? uiText("ui.app.src.remote.computer.23e391cb98") : uiText("ui.app.src.remote.computer.4e54cac759"), "primary", () => void action(async () => {
      const before = context.loaded.mtime_ns == null ? null : String(context.loaded.mtime_ns);
      const result = await shell<{ ok: boolean; path: string; before_mtime_ns: string; mtime_ns: string }>("remote_computer_pair", {
        scopes: [...chosen], system: allowSystem, mtime_ns: before,
      });
      await acceptLocal(result);
    })));
    box.append(button(uiText("ui.app.src.remote.computer.53be70feb7"), "quiet", () => void action(async () => {
      const before = context.loaded.mtime_ns == null ? null : String(context.loaded.mtime_ns);
      const result = await shell<{ ok: boolean; path: string; id?: string; before_mtime_ns: string; mtime_ns: string }>("remote_computer_disable", { mtime_ns: before });
      await acceptLocal(result); // local denial works even when the server is offline
      if (result.id) {
        try { await post(prefix + "/revoke", { device: result.id }); }
        catch { status.textContent = uiText("ui.app.src.remote.computer.2e9e0e3b2f"); }
      }
    })));
  }
  return card(uiText("ui.app.src.remote.computer.1472d8395e"), box);
}
