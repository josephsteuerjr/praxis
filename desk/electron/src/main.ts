// Оболочка Hélène на Electron (решение Егора 28.09: «нужен хороший движок. и электрон.»).
//
// Electron — это и есть программа для человека: окно, значок у часов, один экземпляр,
// память размера окна, уведомления. «Сердце» — дети-агенты, реле, брокер, обновления,
// копии — остаётся в Rust (helene-host, переезжает из shell/src/main.rs отдельным шагом);
// до его переезда окно подключается к агенту, которого держит служба или прежнее окно.
//
// Страница окна приходит своим протоколом helene://localhost — этот origin канал агента
// уже пускает (deskapp.py: _SHELL_SCHEMES), правки в харнессе не нужны.
import { BrowserWindow, Menu, Tray, app, ipcMain, nativeImage, nativeTheme, net, protocol, screen } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, extname, join, normalize, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { channelFor } from "./channel";
import { runCommand, type Ctx } from "./commands";

const PRODUCT_UI = "Hélène";
const TOAST_ID = "app.helene.desk"; // тот же AUMID, что у прежней оболочки: ярлык и уведомления одни
const HIDDEN_FREE_MS = 10 * 60_000; // спрятанное окно через 10 минут отпускает память

app.setName("Helene");
if (process.platform === "win32") app.setAppUserModelId(TOAST_ID);

protocol.registerSchemesAsPrivileged([
  { scheme: "helene", privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);

// ------------------------------------------------------------------ где что лежит

/** Папка установки (helene.json, app/static, helene.log). В разработке — рядом стоящая Hélène. */
function installRoot(): string {
  if (process.env.HELENE_ROOT) return process.env.HELENE_ROOT;
  if (app.isPackaged) return dirname(process.execPath);
  if (process.platform === "win32") return "C:\\Program Files\\Helene";
  if (process.platform === "darwin") return "/Applications/Helene.app/Contents/Resources";
  return "/opt/helene";
}

/** Статика окна: HELENE_UI, в разработке — сборка app/ этого дерева, в поставке — app/static. */
function uiRoot(root: string): string {
  if (process.env.HELENE_UI) return process.env.HELENE_UI;
  if (!app.isPackaged) return join(__dirname, "..", "..", "app", "dist");
  return join(root, "app", "static");
}

const ROOT = installRoot();
const UI = uiRoot(ROOT);

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
let quitting = false;
let freeTimer: NodeJS.Timeout | undefined;

const ctx: Ctx = {
  root: ROOT,
  version: app.getVersion(),
  product: PRODUCT_UI,
  relaunch: () => { quitting = true; app.relaunch(); app.exit(0); },
};

function start() {
  log(`старт ${PRODUCT_UI} ${app.getVersion()} · Electron ${process.versions.electron} · установка ${ROOT} · статика ${UI}`);
  protocol.handle("helene", (req) => serveStatic(req.url));
  ipcMain.on("helene:config", (e) => {
    const { channel, note } = channelFor(ROOT, PRODUCT_UI);
    log(note);
    e.returnValue = channel;
  });
  ipcMain.handle("helene:invoke", (_e, cmd: string, args: Record<string, unknown>) => runCommand(String(cmd), args ?? {}, ctx));
  ipcMain.on("helene:win", (e, action: string) => {
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

function paperColor(): string {
  return nativeTheme.shouldUseDarkColors ? "#1d1914" : "#f4e4cf";
}

function icon(name: string): Electron.NativeImage | undefined {
  const dev = join(__dirname, "..", "..", "shell", "icons", name);
  const packed = join(process.resourcesPath || "", "icons", name);
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
      spellcheck: false,
    },
  });
  if (b.maximized) w.maximize();
  w.once("ready-to-show", () => w.show());
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
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: `Открыть ${PRODUCT_UI}`, click: () => show() },
    { type: "separator" },
    { label: "Выход (агент остаётся под службой)", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", () => show());
}

app.on("before-quit", () => { quitting = true; });
app.on("window-all-closed", () => { /* живём у часов */ });
app.on("activate", () => show()); // macOS: клик по значку в Dock
