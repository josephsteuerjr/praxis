/** Desktop access belongs to the local Windows owner; server settings stay on the server. */
import { api, post, shell } from "../../ui-kit/window/api";
import { button, card, el, toggle } from "../../ui-kit/dom";
import { humanError } from "../../ui-kit/window/lib";
import type { EditionContext, Loaded } from "../../ui-kit/window/views/settings-frame";

const prefix = "/api/computer/remote";
const permissions = [
  ["computer.read", "Смотреть"], ["computer.apps", "Окна и ввод"],
  ["computer.files", "Файлы"], ["computer.process", "Процессы"],
] as const;
type Remote = { enabled?: boolean; id?: string; scopes?: string[] };
type Device = { id: string; name: string; connected: boolean; scopes: string[];
  identity?: { scopes?: string[]; system?: boolean } };
type Picture = { selected: string; devices: Device[] };

export async function remoteComputerCard(context: EditionContext): Promise<HTMLElement> {
  const box = el("div");
  box.append(el("p", "dim", "Агент на сервере может управлять выбранным Windows-компьютером. Доступ разрешает владелец этого компьютера."));
  const status = el("p", "dim", "Проверяю подключённые компьютеры…");
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
      status.textContent = picture.devices.length ? "Подключённые компьютеры" : "Компьютер ещё не подключён.";
      for (const device of picture.devices) {
        const row = el("div", "btn-row");
        const selected = picture.selected === device.id;
        row.append(el("span", "dim", `${device.name}: ${device.connected ? "в сети" : "не в сети"}${selected ? " · выбран" : ""}`));
        const choose = button(selected ? "Выбран" : "Выбрать", "quiet", () => void action(async () => {
          await post(prefix + "/select", { device: device.id });
        }));
        choose.disabled = selected;
        row.append(choose, button("Отозвать доступ", "ghost", () => void action(async () => {
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
    const chosen = new Set(Array.isArray(remote.scopes) ? remote.scopes.filter(scope => permissions.some(([known]) => known === scope)) : remote.id ? [] : permissions.map(([scope]) => scope));
    box.append(el("p", "dim", "Подключить этот компьютер"));
    let allowSystem = (context.saved.service as Record<string, unknown> | undefined)?.session0 === true;
    const system = toggle("Системные команды Windows через установленную службу", allowSystem, value => { allowSystem = value; });
    const rights = el("div", "mode-block");
    for (const [scope, name] of permissions) {
      rights.append(toggle(name, chosen.has(scope), value => {
        if (value) chosen.add(scope); else chosen.delete(scope);
        system.disabled = !chosen.has("computer.process");
        if (system.disabled) { allowSystem = false; system.setAttribute("aria-checked", "false"); }
      }));
    }
    system.disabled = !chosen.has("computer.process");
    if (system.disabled) { allowSystem = false; system.setAttribute("aria-checked", "false"); }
    rights.append(system);
    box.append(rights, el("p", "dim", "Права Windows относятся к этому компьютеру. Разрешение root на VPS настраивается отдельно владельцем сервера."));
    box.append(el("p", "dim", "Файлы, команды и управление приложениями выполняются с правами твоей учётной записи Windows, вне ограды сервера. Эти права разрешай агенту, которому доверяешь."));
    box.append(el("p", "dim", "Системные команды требуют установленной службы и её разрешения на нулевую сессию в основных настройках этой установки."));
    const acceptLocal = async (result: { ok: boolean; path: string; before_mtime_ns: string; mtime_ns: string }) => {
      if (!result.ok || result.path !== context.loaded.path) throw new Error("Подключение изменилось — открой настройки этого агента заново.");
      const fresh = await shell<Loaded>("config_load");
      if (fresh.path !== context.loaded.path) throw new Error("Выбран другой агент — открой его настройки заново.");
      context.draft.computer = fresh.config.computer;
      context.saved.computer = fresh.config.computer;
      context.draft.service = fresh.config.service;
      context.saved.service = fresh.config.service;
      context.freshness.accept?.(result.before_mtime_ns, result.mtime_ns);
      context.loaded.mtime_ns = result.mtime_ns;
    };
    box.append(button(remote.enabled ? "Обновить доступ этого компьютера" : "Подключить и разрешить управление", "primary", () => void action(async () => {
      const before = context.loaded.mtime_ns == null ? null : String(context.loaded.mtime_ns);
      const result = await shell<{ ok: boolean; path: string; before_mtime_ns: string; mtime_ns: string }>("remote_computer_pair", {
        scopes: [...chosen], system: allowSystem, mtime_ns: before,
      });
      await acceptLocal(result);
    })));
    box.append(button("Отключить этот компьютер", "quiet", () => void action(async () => {
      const before = context.loaded.mtime_ns == null ? null : String(context.loaded.mtime_ns);
      const result = await shell<{ ok: boolean; path: string; id?: string; before_mtime_ns: string; mtime_ns: string }>("remote_computer_disable", { mtime_ns: before });
      await acceptLocal(result); // local denial works even when the server is offline
      if (result.id) {
        try { await post(prefix + "/revoke", { device: result.id }); }
        catch { status.textContent = "Доступ этого компьютера выключен. Сервер пока не подтвердил удаление подключения."; }
      }
    })));
  }
  return card("Компьютер для серверного агента", box);
}
