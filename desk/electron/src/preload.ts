// Мост окна к оболочке: канал к агенту (как DESK_CONFIG_OVERRIDE прежней оболочки) и
// команды (как invoke у Tauri). Страница видит только эти два имени — ни Node, ни IPC.
import { contextBridge, ipcRenderer } from "electron";

const channel = ipcRenderer.sendSync("helene:config");
contextBridge.exposeInMainWorld("DESK_CONFIG_OVERRIDE", channel);

contextBridge.exposeInMainWorld("__HELENE__", {
  shell: "electron",
  platform: process.platform,
  async invoke(cmd: string, args?: Record<string, unknown>) {
    try {
      return await ipcRenderer.invoke("helene:invoke", cmd, args ?? {});
    } catch (e) {
      // Electron оборачивает отказ: «Error invoking remote method …: Error: слова». Человеку — слова.
      const msg = e instanceof Error ? e.message : String(e);
      throw new Error(msg.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ""));
    }
  },
  win(action: "minimize" | "maximize" | "close") {
    ipcRenderer.send("helene:win", action);
  },
  look(paper: { day: string; night: string }) {
    ipcRenderer.send("helene:look", paper);
  },
});
