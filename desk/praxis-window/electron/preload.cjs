// Узкий мост между страницей и процессом Electron. Страница работает с изоляцией
// контекста и песочницей Chromium: отсюда ей видно ровно перечисленное здесь.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("praxisWindow", {
  // страница входа
  connect: (key) => ipcRenderer.invoke("praxis:connect", key),
  postMessage: (command) => ipcRenderer.send("praxis:window", String(command)),

  // страница окна
  reload: () => { ipcRenderer.send("praxis:window", "reload"); return Promise.resolve(); },
  titleBar: () => { ipcRenderer.send("praxis:chrome"); },
  settings: () => ipcRenderer.invoke("praxis:settings"),
  setSettings: (patch) => ipcRenderer.invoke("praxis:settings:set", patch),
  // масштаб страницы: { step: ±1 } | { factor } | { reset: true } → новый масштаб
  zoom: (change) => ipcRenderer.invoke("praxis:zoom", change),
  // прокси без VPN: состояние; { network } | { proxyTask } → новое состояние
  proxyState: () => ipcRenderer.invoke("praxis:proxy"),
  setProxy: (patch) => ipcRenderer.invoke("praxis:proxy:set", patch),
  onMaximized: (listener) => {
    if (typeof listener !== "function") return;
    const handler = (_event, data) => { if (data && data.kind === "state") listener(!!data.maximized); };
    ipcRenderer.on("praxis:event", handler);
  },
});
