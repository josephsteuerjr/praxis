// Electron-оболочка лаборатории: тот же dist, что у Tauri и WebKitGTK.
// --auto: прогнать стенд, напечатать BENCH и память процессов, выйти.
// PREFS (выбор Егора в тонкой настройке) → ../prefs.json; WHEEL (журнал жестов) → ../wheel.log.
const { app, BrowserWindow, Menu } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

Menu.setApplicationMenu(null);
const auto = process.argv.includes("--auto");
const selftest = process.argv.includes("--selftest");
const prefsFile = path.join(__dirname, "..", "prefs.json");
const wheelFile = path.join(__dirname, "..", "wheel.log");

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1440, height: 900, backgroundColor: "#f4e4cf", title: "Hélène · лаборатория (Electron)",
    webPreferences: { contextIsolation: true },
  });
  win.webContents.on("console-message", (e) => {
    const msg = String(e.message || "");
    if (msg.startsWith("SELFTEST ")) {
      console.log(msg);
      if (selftest) setTimeout(() => app.quit(), 200);
    } else if (msg.startsWith("BENCH ")) {
      const mem = app.getAppMetrics().reduce((s, m) => s + (m.memory?.workingSetSize || 0), 0);
      console.log(msg);
      console.log("MEM_MB " + Math.round(mem / 1024));
      if (auto) setTimeout(() => app.quit(), 300);
    } else if (msg.startsWith("PREFS ")) {
      try { fs.writeFileSync(prefsFile, JSON.stringify(JSON.parse(msg.slice(6)), null, 2) + "\n"); } catch { /* */ }
    } else if (msg.startsWith("WHEEL\n")) {
      try {
        if (fs.existsSync(wheelFile) && fs.statSync(wheelFile).size > 4_000_000) fs.renameSync(wheelFile, wheelFile + ".old");
        fs.appendFileSync(wheelFile, msg.slice(6) + "\n");
      } catch { /* */ }
    }
  });
  await win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { query: { host: "electron", ...(auto ? { auto: "1" } : {}), ...(selftest ? { selftest: "1" } : {}) } });
});
app.on("window-all-closed", () => app.quit());
