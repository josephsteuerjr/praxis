// Команды окна (прежние #[tauri::command] оболочки). Те, что про окно и систему, Electron
// делает сам; остальные выполняет общий Rust-хост helene-host.
import { Notification, shell } from "electron";
import { existsSync } from "node:fs";

export interface Ctx {
  root: string;
  version: string;
  product: string;
  relaunch(): void;
  host: { invoke(command: string, args?: Record<string, unknown>): Promise<unknown> };
}

type Args = Record<string, unknown>;
type Handler = (args: Args, ctx: Ctx) => unknown | Promise<unknown>;

const NATIVE: Record<string, Handler> = {
  open_path: async (a) => {
    const p = String(a.path ?? "");
    if (!p) throw new Error("пустой путь");
    if (/^https?:\/\//i.test(p)) {
      await shell.openExternal(p);
      return null;
    }
    if (!existsSync(p)) throw new Error(`Нет такого места: ${p}`);
    const err = await shell.openPath(p);
    if (err) throw new Error(err);
    return null;
  },

  reveal_path: (a) => {
    const p = String(a.path ?? "");
    if (!p || !existsSync(p)) throw new Error(`Нет такого места: ${p}`);
    shell.showItemInFolder(p);
    return null;
  },

  notify: (a, ctx) => {
    if (!Notification.isSupported()) return false;
    new Notification({ title: String(a.title ?? ctx.product), body: String(a.body ?? "") }).show();
    return true;
  },


};

export async function runCommand(cmd: string, args: Args, ctx: Ctx): Promise<unknown> {
  // Config, children, service, updates and backups use the same Rust commands as Tauri.
  const h = NATIVE[cmd];
  if (h) return h(args ?? {}, ctx);
  if (process.platform === "linux") {
    if (cmd === "update_download" || cmd === "update_install") throw new Error("На Linux обновление устанавливается новым .deb/.rpm через пакетный менеджер.");
    if (cmd === "update_check") {
      const result = await ctx.host.invoke(cmd, args ?? {}) as Record<string, unknown>;
      const url = "https://github.com/josephsteuerjr/praxis/releases/tag/v" + encodeURIComponent(String(result.latest));
      return { ...result, url, sha256: "", package_manager: true };
    }
  }
  return ctx.host.invoke(cmd, args ?? {});
}
