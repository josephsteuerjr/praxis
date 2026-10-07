// Оболочка Hélène на Electron (решение Егора 28.09: «нужен хороший движок. и электрон.»).
//
// Electron — это и есть программа для человека: окно, значок у часов, один экземпляр,
// память размера окна, уведомления. «Сердце» — дети-агенты, реле, брокер, обновления,
// копии — остаётся в Rust (helene-host, переезжает из shell/src/main.rs отдельным шагом);
// до его переезда окно подключается к агенту, которого держит служба или прежнее окно.
//
// Страница окна приходит своим протоколом helene://localhost — этот origin канал агента
// уже пускает (deskapp.py: _SHELL_SCHEMES), правки в харнессе не нужны.
import { BrowserWindow, Tray, Notification, app, dialog, ipcMain, nativeImage, nativeTheme, net, protocol, screen } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, extname, join, normalize, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { HostClient } from "./host";
import { bootstrapChannel, initOwner, ownerRoot, programRoot } from "./layout";
import { runCommand, type Ctx } from "./commands";
import { placeTray, type PopupPoint } from "../../ui-kit/tray-position";

const PRODUCT_UI = "Hélène";
const TOAST_ID = "app.helene.desk"; // тот же AUMID, что у прежней оболочки: ярлык и уведомления одни
const HIDDEN_FREE_MS = 10 * 60_000; // спрятанное окно через 10 минут отпускает память

app.setName("Helene");
// В поставке — тот же AUMID, что у ярлыка программы (значок и уведомления одни). Предпросмотр
// из разработки — свой: иначе Windows рисует на панели задач значок ярлыка установленной
// версии, а не окна (Егор 28.09: «значок тот же»).
if (process.platform === "win32") app.setAppUserModelId(app.isPackaged ? TOAST_ID : TOAST_ID + ".preview");

protocol.registerSchemesAsPrivileged([
  { scheme: "helene", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);

// ------------------------------------------------------------------ где что лежит

/** Папка установки (helene.json, app/static, helene.log). В разработке — рядом стоящая Hélène. */
function installRoot(): string {
  if (process.platform === "linux") return ownerRoot(app.getPath("home"), process.env.HELENE_ROOT);
  if (process.env.HELENE_ROOT) return process.env.HELENE_ROOT;
  if (app.isPackaged) return dirname(process.execPath);
  if (process.platform === "win32") return "C:\\Program Files\\Helene";
  if (process.platform === "darwin") return "/Applications/Helene.app/Contents/Resources";
  return ownerRoot(app.getPath("home"), process.env.HELENE_ROOT);
}

/** Статика окна: HELENE_UI, в разработке — сборка app/ этого дерева, в поставке — app/static. */
function uiRoot(root: string): string {
  if (process.env.HELENE_UI) return process.env.HELENE_UI;
  if (!app.isPackaged) return join(__dirname, "..", "..", "app", "dist");
  return join(root, "app", "static");
}

const ROOT = installRoot();
const PROGRAM = process.platform === "linux" ? programRoot(process.execPath, process.env.HELENE_PROGRAM_ROOT) : ROOT;
if (process.platform === "linux") {
  mkdirSync(join(ROOT, "window"), { recursive: true });
  app.setPath("userData", join(ROOT, "window"));
}
const UI = process.env.HELENE_UI || (app.isPackaged ? join(PROGRAM, "app", "static") : uiRoot(ROOT));
let host: HostClient;
let channel: Record<string, unknown>;
let initScript = "";
let stopped = false;
let closing = false;

function log(line: string) {
  const stamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  const text = `${stamp} [окно] ${line}\n`;
  try { appendFileSync(join(app.getPath("userData"), "window.log"), text); } catch { /* журнал не пишется — не повод падать */ }
  if (!app.isPackaged) process.stdout.write(text);
}

// ------------------------------------------------------------------ один экземпляр

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => show());
  app.whenReady().then(start);
}

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let trayPopup: BrowserWindow | null = null;
let trayAnchor: PopupPoint | null = null;
let quitting = false;
let freeTimer: NodeJS.Timeout | undefined;

const ctx: Ctx = {
  root: ROOT,
  version: app.getVersion(),
  product: PRODUCT_UI,
  relaunch: () => { app.relaunch(); app.quit(); },
  host: { invoke: (cmd, args) => host.invoke(cmd, args) },
};

async function start() {
  try {
    if (process.platform === "linux") initOwner(PROGRAM, ROOT, !!process.env.HELENE_ROOT);
    host = new HostClient(process.env.HELENE_HOST || join(PROGRAM, process.platform === "win32" ? "helene-host.exe" : "helene-host"), ROOT, log);
    host.on("event", (name, data) => {
      if (name === "show-window") show();
      if (name === "replace-window") {
        initScript = data.script; channel = bootstrapChannel(initScript);
        win?.destroy(); win = null; show();
      }
      if (name === "notify" && Notification.isSupported()) new Notification(data).show();
      if (name === "message") void dialog.showMessageBox({ type: data.type, title: data.title, message: data.message }).catch((e) => log(String(e)));
      if (name === "relaunch") { app.relaunch(); app.quit(); }
      if (name === "exit") app.quit();
    });
    const ready = await host.ready;
    initScript = ready.script;
    channel = bootstrapChannel(initScript);
    host.on("closed", (error) => {
      if (!quitting) { dialog.showErrorBox("Движок оболочки остановился", String(error)); app.quit(); }
    });
  } catch (error) {
    dialog.showErrorBox("Hélène не открылась", error instanceof Error ? error.message : String(error));
    app.quit(); return;
  }
  // Проверка темы глазами: HELENE_THEME=dark|light (в поставке — как в системе).
  const theme = process.env.HELENE_THEME;
  if (theme === "dark" || theme === "light") nativeTheme.themeSource = theme;
  log(`старт ${PRODUCT_UI} ${app.getVersion()} · Electron ${process.versions.electron} · установка ${ROOT} · статика ${UI}`);
  protocol.handle("helene", (req) => serveStatic(req.url));
  ipcMain.on("helene:config", (e) => {
    if (!trustedSender(e)) { e.returnValue = null; return; }
    e.returnValue = channel;
  });
  ipcMain.handle("helene:invoke", (e, cmd: string, args: Record<string, unknown>) => {
    if (!trustedSender(e) || String(cmd).startsWith("host_")) throw new Error("Недоступный вызов оболочки");
    if (String(cmd).startsWith("tray_")) {
      if (BrowserWindow.fromWebContents(e.sender) !== trayPopup) throw new Error("Недоступный вызов карточки");
      return trayCommand(String(cmd), args ?? {});
    }
    return runCommand(String(cmd), args ?? {}, ctx);
  });
  ipcMain.on("helene:look", (e, paper: { day?: string; night?: string }) => {
    if (!trustedSender(e)) return;
    try {
      mkdirSync(app.getPath("userData"), { recursive: true });
      writeFileSync(lookFile(), JSON.stringify({ day: paper?.day, night: paper?.night }));
    } catch { /* не запомнится — откроется бумагой по умолчанию */ }
    const w = BrowserWindow.fromWebContents(e.sender);
    if (w && w === win) w.setBackgroundColor(paperColor());
  });
  ipcMain.on("helene:win", (e, action: string) => {
    if (!trustedSender(e)) return;
    const w = BrowserWindow.fromWebContents(e.sender);
    if (!w) return;
    if (action === "minimize") w.minimize();
    else if (action === "maximize") (w.isMaximized() ? w.unmaximize() : w.maximize());
    else if (action === "close") w.close();
  });
  makeTray();
  show();
}

// ------------------------------------------------------------------ статика окна

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".jpg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
  ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".txt": "text/plain; charset=utf-8",
};

async function serveStatic(url: string): Promise<Response> {
  const u = new URL(url);
  const rel = decodeURIComponent(u.pathname).replace(/^\/+/, "") || "index.html";
  const full = normalize(join(UI, rel));
  // Только внутри папки статики: `..` и абсолютные пути — мимо.
  if (full !== UI && !full.startsWith(UI.endsWith(sep) ? UI : UI + sep)) return new Response("нет", { status: 403 });
  if (!existsSync(full)) {
    // config.js — только у веб-версии за каналом; в оболочке его нет, 404 безвреден.
    return new Response("нет такого файла", { status: 404 });
  }
  const res = await net.fetch(pathToFileURL(full).toString());
  const type = MIME[extname(full).toLowerCase()] ?? "application/octet-stream";
  return new Response(res.body, { status: 200, headers: { "content-type": type, "cache-control": "no-cache" } });
}

// ------------------------------------------------------------------ окно

interface Bounds { x?: number; y?: number; width: number; height: number; maximized?: boolean }
const stateFile = () => join(app.getPath("userData"), "window-state.json");

function loadBounds(): Bounds {
  const fallback: Bounds = { width: 1360, height: 880 };
  try {
    const b = JSON.parse(readFileSync(stateFile(), "utf8")) as Bounds;
    if (!(b.width >= 640 && b.height >= 480)) return fallback;
    // Окно не должно открыться за пределами экранов (монитор отключили).
    if (b.x != null && b.y != null) {
      const seen = screen.getAllDisplays().some((d) => {
        const a = d.workArea;
        return b.x! + 80 < a.x + a.width && b.x! + b.width - 80 > a.x && b.y! >= a.y - 20 && b.y! < a.y + a.height - 60;
      });
      if (!seen) return { width: b.width, height: b.height, maximized: b.maximized };
    }
    return b;
  } catch {
    return fallback;
  }
}

function saveBounds(w: BrowserWindow) {
  try {
    mkdirSync(app.getPath("userData"), { recursive: true });
    const b: Bounds = { ...w.getNormalBounds(), maximized: w.isMaximized() };
    writeFileSync(stateFile(), JSON.stringify(b));
  } catch { /* не запомнится — откроется по умолчанию */ }
}

/** Тон бумаги из «Вида» (окно присылает его само): окно открывается нужного цвета. */
const lookFile = () => join(app.getPath("userData"), "look.json");
function paperColor(): string {
  let day = "#f4e4cf", night = "#1d1914";
  try {
    const p = JSON.parse(readFileSync(lookFile(), "utf8")) as { day?: string; night?: string };
    const ok = (c?: string) => typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c);
    if (ok(p.day)) day = p.day!;
    if (ok(p.night)) night = p.night!;
  } catch { /* ещё не выбирали — бумага по умолчанию */ }
  return nativeTheme.shouldUseDarkColors ? night : day;
}

function icon(name: string): Electron.NativeImage | undefined {
  const dev = join(__dirname, "..", "..", "shell", "icons", name);
  const packed = join(PROGRAM, "electron", "resources", "icons", name);
  const p = app.isPackaged ? packed : dev;
  return existsSync(p) ? nativeImage.createFromPath(p) : undefined;
}

function createWindow(): BrowserWindow {
  const b = loadBounds();
  const w = new BrowserWindow({
    x: b.x, y: b.y, width: b.width, height: b.height,
    minWidth: 720, minHeight: 520,
    show: false,
    frame: false, // своя шапка окна (ui-kit: #chrome), как было в Tauri
    backgroundColor: paperColor(),
    title: PRODUCT_UI,
    icon: icon("icon.png"),
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      additionalArguments: ["--helene-session=" + (process.env.XDG_SESSION_TYPE === "wayland" ? "wayland" : "x11")],
      spellcheck: false,
    },
  });
  if (b.maximized) w.maximize();
  w.once("ready-to-show", () => w.show());
  const visibility = () => void host.invoke("host_visibility", { visible: w.isVisible(), focused: w.isFocused() }).catch((e) => log(String(e)));
  // Четыре отдельных вызова: общий union имён не выбирает одну перегрузку BrowserWindow.on.
  w.on("show", visibility);
  w.on("hide", visibility);
  w.on("focus", visibility);
  w.on("blur", visibility);
  w.webContents.on("did-finish-load", () => {
    // Bootstrap owns this code. It may render the explicit foreign-port refusal.
    void w.webContents.executeJavaScript(initScript).catch((e) => log(String(e)));
  });
  // Закрыть окно ≠ выйти: окно прячется к часам, агент живёт; выход — из меню значка.
  w.on("close", (e) => {
    saveBounds(w);
    if (quitting) return;
    e.preventDefault();
    w.hide();
    armFree();
  });
  w.on("show", () => clearTimeout(freeTimer));
  // Внешние ссылки — в браузер, а не в окно программы.
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void import("electron").then(({ shell }) => shell.openExternal(url));
    return { action: "deny" };
  });
  w.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("helene://")) e.preventDefault();
  });
  void w.loadURL("helene://localhost/index.html");
  return w;
}

/** Спрятанное окно долго не нужно — отпускаем его память; значок у часов остаётся. */
function armFree() {
  clearTimeout(freeTimer);
  freeTimer = setTimeout(() => {
    if (win && !win.isVisible()) {
      log("окно спрятано 10 минут — отпускаю его память");
      win.destroy();
      win = null;
    }
  }, HIDDEN_FREE_MS);
}

function show() {
  if (!channel || quitting) return;
  if (!win || win.isDestroyed()) win = createWindow();
  else {
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }
}

// ------------------------------------------------------------------ значок у часов

function makeTray() {
  const img = icon(process.platform === "darwin" ? "tray-template@2x.png" : "32x32.png");
  if (!img) { log("значка для трея нет — работаю без него"); return; }
  if (process.platform === "darwin") img.setTemplateImage(true);
  tray = new Tray(img);
  tray.setToolTip(PRODUCT_UI);
  tray.on("right-click", () => showTrayPopup());
  tray.on("click", () => show());
}

function popupArea() {
  const point = trayAnchor || screen.getCursorScreenPoint();
  return { point, area: screen.getDisplayNearestPoint(point).workArea };
}

function fitTray(height: number) {
  if (!trayPopup || trayPopup.isDestroyed()) return;
  const { point, area } = popupArea();
  const box = placeTray(point, area, height);
  const current = trayPopup.getBounds();
  if (current.x !== box.x || current.y !== box.y || current.width !== box.width || current.height !== box.height) trayPopup.setBounds(box);
}

function showTrayPopup() {
  if (quitting) return;
  if (trayPopup?.isVisible() && trayPopup.isFocused()) { trayPopup.hide(); return; }
  trayAnchor = screen.getCursorScreenPoint();
  if (!trayPopup || trayPopup.isDestroyed()) {
    trayPopup = new BrowserWindow({ width: 368, height: 320, show: false, frame: false,
      transparent: true, backgroundColor: '#00000000', hasShadow: false, alwaysOnTop: true,
      skipTaskbar: true, resizable: false,
      webPreferences: { preload: join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
    const popup = trayPopup;
    popup.on('blur', () => popup.hide());
    popup.on('close', e => { if (!quitting) { e.preventDefault(); popup.hide(); } });
    popup.on('closed', () => { if (trayPopup === popup) trayPopup = null; });
    popup.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    popup.webContents.on('will-navigate', (e, url) => { if (url !== 'helene://localhost/tray.html') e.preventDefault(); });
    popup.once('ready-to-show', () => { if (!quitting) { fitTray(320); popup.show(); popup.focus(); } });
    void popup.loadURL('helene://localhost/tray.html').catch(e => { log(String(e)); show(); });
  } else { fitTray(trayPopup.getBounds().height); trayPopup.show(); trayPopup.focus(); }
}

async function trayCommand(command: string, args: Record<string, unknown>) {
  if (command === 'tray_hide') { trayPopup?.hide(); return; }
  if (command === 'tray_fit') { fitTray(Number(args.height)); return; }
  if (command === 'tray_open_main') { trayPopup?.hide(); show(); return; }
  if (command === 'tray_exit') { quitting = true; app.quit(); return; }
  if (command === 'tray_context') {
    const [owner, list] = await Promise.all([host.invoke('owner_state'), host.invoke('agents_list')]);
    let background: boolean | null = null;
    try { background = !!JSON.parse(readFileSync(join(ROOT, 'helene.json'), 'utf8')).installed?.service; } catch { /* unknown */ }
    const { area } = popupArea();
    const agents = list as { agents: unknown[]; current: string };
    return { owner, background, agents: agents.agents, current: agents.current, max_height: area.height + 32 };
  }
  throw new Error('Неизвестное действие карточки');
}

app.on("before-quit", (event) => {
  quitting = true;
  if (stopped || !host) return;
  event.preventDefault();
  if (closing) return;
  closing = true;
  void host.close().finally(() => { stopped = true; app.quit(); });
});
app.on("window-all-closed", () => { /* живём у часов */ });
app.on("activate", () => show()); // macOS: клик по значку в Dock

function trustedSender(event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  if (!frame || frame !== event.sender.mainFrame) return false;
  try { const url = new URL(frame.url); return url.protocol === "helene:" && url.hostname === "localhost"; }
  catch { return false; }
}
