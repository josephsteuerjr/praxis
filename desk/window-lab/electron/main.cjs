// Electron-оболочка лаборатории: тот же dist, что у Tauri и WebKitGTK.
// --auto: прогнать стенд, напечатать BENCH и память процессов, выйти.
const { app, BrowserWindow, Menu } = require("electron");
Menu.setApplicationMenu(null);
const path = require("node:path");

const auto = process.argv.includes("--auto");
app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1440, height: 900, backgroundColor: "#f4e4cf", title: "Hélène · лаборатория (Electron)",
    webPreferences: { contextIsolation: true },
  });
  win.webContents.on("console-message", (e) => {
    const msg = e.message ?? arguments[2];
    if (String(msg).startsWith("BENCH ")) {
      const mem = app.getAppMetrics().reduce((s, m) => s + (m.memory?.workingSetSize || 0), 0);
      console.log(msg);
      console.log("MEM_MB " + Math.round(mem / 1024));
      if (auto) setTimeout(() => app.quit(), 300);
    }
  });
  await win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { query: { host: "electron", ...(auto ? { auto: "1" } : {}) } });
});
app.on("window-all-closed", () => app.quit());
