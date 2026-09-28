// Команды окна (прежние #[tauri::command] оболочки). Те, что про окно и систему, Electron
// делает сам; остальные — дело «сердца» программы (helene-host, Rust без окна), пока оно
// не переехало — честный отказ словами, а не тишина.
import { app, Notification, shell } from "electron";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { CONFIG_NAME, agentName, mtimeNs, readConfig, treeOf } from "./channel";

export interface Ctx {
  root: string;
  version: string;
  product: string;
  relaunch(): void;
}

type Args = Record<string, unknown>;
type Handler = (args: Args, ctx: Ctx) => unknown | Promise<unknown>;

const platform = (): string => (process.platform === "win32" ? "windows" : process.platform === "darwin" ? "macos" : "linux");

const NATIVE: Record<string, Handler> = {
  app_info: (_a, ctx) => ({
    version: ctx.version,
    exe_dir: ctx.root,
    root: ctx.root,
    log: join(ctx.root, "helene.log"),
    platform: platform(),
    arch: process.arch === "x64" ? "x86_64" : process.arch === "arm64" ? "aarch64" : process.arch,
    shell: "electron",
  }),

  config_load: (_a, ctx) => {
    const path = join(ctx.root, CONFIG_NAME);
    const read = readConfig(path);
    if (read.kind === "broken") throw new Error(`${path} не разобрался: ${read.why}`);
    const cfg = read.kind === "ok" ? read.value : {};
    return {
      config: cfg,
      path,
      tree: treeOf(path, cfg),
      exe_dir: ctx.root,
      agent_id: "main",
      agent_name: agentName(cfg),
      mtime_ns: mtimeNs(path),
    };
  },

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

  restart_self: (_a, ctx) => {
    ctx.relaunch();
    return null;
  },

  autostart_get: () => app.getLoginItemSettings().openAtLogin,
  autostart_set: (a) => {
    app.setLoginItemSettings({ openAtLogin: !!a.on });
    return null;
  },
};

export async function runCommand(cmd: string, args: Args, ctx: Ctx): Promise<unknown> {
  const h = NATIVE[cmd];
  if (h) return h(args ?? {}, ctx);
  throw new Error(
    `Эта кнопка заработает, когда в новую оболочку переедет «сердце» программы (команда «${cmd}»). ` +
      "Пока окно на Electron — предпросмотр: агент, переписка и ходы живые, настройки меняются в прежнем окне.",
  );
}
