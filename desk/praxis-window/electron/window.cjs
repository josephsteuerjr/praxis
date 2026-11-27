// Оболочка окна Праксис — тонкая. Страницу она грузит с сервера Праксис (та же сборка,
// что открыта в браузере), хранит ключ подключения и помнит, как владелец хочет видеть
// окно: развёрнутым или обычного размера, и запускать ли вместе с Windows.
const { app, BrowserWindow, ipcMain, Menu, nativeTheme, safeStorage, screen, session, shell } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const proxy = require("./proxy-config.cjs");

const local = process.env.LOCALAPPDATA;
const pultConfig = path.join(local, "Programs", "PraxisPult", "helene.json");

/** Адрес сервера Праксис в исходнике не живёт — у каждого свой. Окно берёт его из server.json
 *  рядом с программой (образец — server.example.json), а без него — из прежнего подключения
 *  PraxisPult. Совсем без адреса окно честно скажет об этом на странице входа. */
function serverAddress() {
  const read = (file) => { try { return JSON.parse(fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "")); } catch { return {}; } };
  const own = read(path.join(__dirname, "server.json")).server;
  const pult = read(pultConfig);
  const value = typeof own === "string" ? own : pult.mode === "remote" ? pult.base : "";
  try { return new URL(value).origin; } catch { return ""; }
}
const server = serverAddress();
const ui = server + "/assets/praxis-window/index.html";
const login = pathToFileURL(path.join(__dirname, "connect.html")).href;
const profile = path.join(local, "Praxis", "DesktopWindow", "Electron");
const settingsFile = path.join(profile, "settings.json");
const startedByLogin = process.argv.includes("--autostart");

// Обычное окно — по пропорциям экрана, а не квадратом; развёрнутым владелец видит
// его на весь экран. Минимум — чтобы полка, раздел и панель хода помещались.
const FRACTION = { width: 0.78, height: 0.84 };
const MINIMUM = { width: 960, height: 620 };
const CHROME = 46;                      // высота полосы под системные кнопки окна
// network — какая дорога до сервера: auto (помнит, что сработало), direct, proxy;
// route — та, что сработала в прошлый раз; proxyTask — держать ли задачу «Praxis Proxy».
const DEFAULTS = { autostart: false, startMaximized: true, zoom: 1, network: "auto", route: "", proxyTask: true, bounds: null, maximized: true };
const ZOOM = { min: 0.6, max: 1.6, step: 1.1 };     // масштаб страницы: 60…160 %, шаг 10 %
const PAPER = { light: "#f8eee0", dark: "#1a1815" };

app.setPath("userData", profile);
app.setAppUserModelId("Praxis.DesktopWindow");
Menu.setApplicationMenu(null);

let window = null;
let channel = null;
let connecting = null;
let raiseRequested = false;             // ярлык щёлкнули, пока окно ещё не показалось: его просят открыть
let fitting = false;                    // идёт сверка размера страницы с окном
let repairing = false;                  // разница найдена: идёт починка
let repairs = 0;                        // сколько раз чинили после последней смены экрана
let fitTimer = null;

const clampZoom = (value) => Math.round(Math.min(ZOOM.max, Math.max(ZOOM.min, Number.isFinite(value) ? value : 1)) * 100) / 100;

function readSettings() {
  try {
    const saved = JSON.parse(fs.readFileSync(settingsFile, "utf8"));
    if (!saved || typeof saved !== "object") return { ...DEFAULTS };
    return { ...DEFAULTS, ...saved, zoom: clampZoom(Number(saved.zoom)),
      bounds: saved.bounds && typeof saved.bounds === "object" ? saved.bounds : null };
  } catch { return { ...DEFAULTS }; }
}

function writeSettings() {
  try {
    fs.mkdirSync(profile, { recursive: true });
    const temporary = settingsFile + ".next";
    fs.writeFileSync(temporary, JSON.stringify(saved, null, 1));
    fs.renameSync(temporary, settingsFile);
  } catch { /* окно живёт и без памяти о размере */ }
}

const saved = readSettings();

/** Где стоять обычному окну: там, где его оставили, или по пропорциям экрана. */
function place() {
  const area = screen.getPrimaryDisplay().workArea;
  const fresh = {
    x: Math.round(area.x + area.width * (1 - FRACTION.width) / 2),
    y: Math.round(area.y + area.height * (1 - FRACTION.height) / 2),
    width: Math.round(area.width * FRACTION.width),
    height: Math.round(area.height * FRACTION.height),
  };
  const remembered = saved.bounds;
  if (!remembered || !Number.isFinite(remembered.width) || !Number.isFinite(remembered.height)
      || !Number.isFinite(remembered.x) || !Number.isFinite(remembered.y)) return fresh;
  // Монитор мог смениться: окно, которое больше не помещается ни на одном, встаёт заново.
  const fits = screen.getAllDisplays().some((display) => {
    const a = display.workArea;
    return remembered.x + remembered.width > a.x + 80 && remembered.x < a.x + a.width - 80
      && remembered.y + remembered.height > a.y + 40 && remembered.y < a.y + a.height - 40;
  });
  if (!fits) return fresh;
  return {
    x: Math.round(remembered.x), y: Math.round(remembered.y),
    width: Math.max(MINIMUM.width, Math.round(remembered.width)),
    height: Math.max(MINIMUM.height, Math.round(remembered.height)),
  };
}

function remember() {
  if (!window || window.isDestroyed() || window.isMinimized()) return;
  saved.maximized = window.isMaximized();
  if (!window.isMaximized()) saved.bounds = window.getBounds();
  writeSettings();
}

/** Автозапуск с Windows: окно стартует свёрнутым и не выскакивает поверх при входе. */
function applyLoginItem() {
  try {
    app.setLoginItemSettings({ openAtLogin: !!saved.autostart, path: process.execPath, args: ["--autostart"] });
  } catch { /* без права записи в реестр автозапуск просто не включится */ }
}

/** Масштаб страницы — владелец подгоняет под свой экран: Ctrl + колесо, Ctrl + «+» / «−» / «0»
 *  или кнопки в «Системе». Запоминается вместе с остальными настройками окна. */
function setZoom(next) {
  saved.zoom = clampZoom(next);
  writeSettings();
  if (window && !window.isDestroyed()) window.webContents.setZoomFactor(saved.zoom);
  return saved.zoom;
}

const isUi = (url) => { try { const u = new URL(url); return u.origin + u.pathname === ui; } catch { return false; } };
const isOnScreen = (event) => !!window && event.sender === window.webContents && event.senderFrame === event.sender.mainFrame
  && (isUi(event.senderFrame.url) || event.senderFrame.url === login);

function savedKey() {
  try { return safeStorage.decryptString(fs.readFileSync(path.join(profile, "access.bin"))); } catch {}
  try {
    // прежнее подключение PraxisPult: ключ владельца тот же. Адрес в нём может быть старым
    // (сервер переезжал): окно ходит по адресу из server.json, а ключ проверяет сам сервер
    const cfg = JSON.parse(fs.readFileSync(pultConfig, "utf8").replace(/^\uFEFF/, ""));
    if (cfg.mode === "remote" && typeof cfg.key === "string" && server) return cfg.key;
  } catch {}
  return "";
}

const CONNECTED = { ok: true };
const refused = (reason, message) => ({ ok: false, reason, message });

// ------------------------------------------------------------------ дорога до сервера

/* Сервер Праксис напрямую из России недоступен. Вторая дорога — местный прокси (SSH-туннель
 * до машины с tinyproxy, его держит задача «Praxis Proxy»): окно ходит в сеть сессией Electron,
 * а сессии можно сказать, через какой прокси. Так же ходит и тело Праксис (proxy_url в его настройках). */

/** В каком порядке пробовать дороги: напрямую или через прокси. «Авто» начинает с той, что
 *  сработала в прошлый раз, — иначе из России каждый запуск ждёт таймаут прямой дороги. */
function routes() {
  const mode = saved.network === "direct" || saved.network === "proxy" ? saved.network : "auto";
  if (mode === "direct" || (mode === "auto" && !proxy.readConfig())) return ["direct"];
  if (mode === "proxy") return ["proxy"];
  return saved.route === "proxy" ? ["proxy", "direct"] : ["direct", "proxy"];
}

let routeUsed = "";                       // какой дорогой сессия ходит сейчас

async function takeRoute(route, config) {
  if (routeUsed === route) return;
  await channel.setProxy(route === "proxy" ? { proxyRules: config.proxy_url } : { mode: "direct" });
  channel.closeAllConnections?.();
  channel.clearHostResolverCache?.();
  routeUsed = route;
}

function offlineMessage(order) {
  if (order.includes("proxy")) return "Не удалось дозвониться до Праксис — ни напрямую, ни через прокси. Включи VPN или проверь прокси в «Система → Это окно».";
  return "Не удалось дозвониться до Праксис. Если VPN выключен — включи его.";
}

async function connect(manual) {
  if (!server) return refused("noserver", "Не задан адрес сервера Праксис: положи server.json рядом с программой — образец server.example.json там же.");
  const key = manual || savedKey();
  if (!key) return refused("nokey", "Введи ключ подключения к Праксис.");
  const order = routes();
  for (const route of order) {
    const config = proxy.readConfig();
    if (route === "proxy" && (!config || !(await proxy.alive(config.listen_port)))) continue;   // туннель не стоит
    try {
      await channel.cookies.set({ url: server, name: "desk_key", value: key, path: "/", secure: true, httpOnly: true, sameSite: "strict" });
      await takeRoute(route, config);
      // первая из двух дорог не должна держать долго: если она не выходит, вторая ждёт
      const check = await channel.fetch(server + "/api/who", { redirect: "error", cache: "no-store", signal: AbortSignal.timeout(order.length > 1 ? 9000 : 20000) });
      await check.body?.cancel();
      if (route !== saved.route) { saved.route = route; writeSettings(); }
      if (check.status === 401 || check.status === 403) return refused("rejected", "Праксис не пускает: ключ не подошёл.");
      if (!check.ok) return refused("offline", "Сервер не отвечает.");
      if (manual && safeStorage.isEncryptionAvailable()) {
        const dest = path.join(profile, "access.bin");
        fs.writeFileSync(dest + ".tmp", safeStorage.encryptString(key));
        fs.renameSync(dest + ".tmp", dest);
      }
      // Сервер не говорит странице, сколько её держать в кэше, и Chromium держит её часами:
      // после новой сборки окно открывалось бы со старой. Входную страницу перечитываем
      // через кэш — он обновляется, и loadURL берёт её же, уже свежую.
      await channel.fetch(ui, { cache: "reload" }).then((fresh) => fresh.body?.cancel()).catch(() => undefined);
      await window.loadURL(ui);
      return CONNECTED;
    } catch { /* эта дорога не вышла — следующая */ }
  }
  return refused("offline", offlineMessage(order));
}

function openConnection(key) {
  if (!connecting) connecting = connect(key).finally(() => { connecting = null; });
  return connecting;
}

/** Цвет системной рамки следует за бумагой окна: светлая — тёмные значки, тёмная — светлые. */
function paintChrome() {
  if (!window || window.isDestroyed() || !isUi(window.webContents.getURL())) return;
  window.webContents.executeJavaScript(
    "(() => { const s = getComputedStyle(document.documentElement); return [s.getPropertyValue('--ground').trim(), s.getPropertyValue('--ink').trim()]; })()")
    .then(([color, symbol]) => {
      if (window && !window.isDestroyed() && color) window.setTitleBarOverlay({ color, symbolColor: symbol || "#262320", height: CHROME });
    })
    .catch(() => undefined);
}

function sendState() {
  if (window && !window.isDestroyed()) window.webContents.send("praxis:event", { kind: "state", maximized: window.isMaximized() });
}

const publicSettings = () => ({ autostart: !!saved.autostart, startMaximized: saved.startMaximized !== false, zoom: saved.zoom, version: app.getVersion() });

// ------------------------------------------------------------------ прокси без VPN

const schtasks = (args) => new Promise((resolve) => execFile("schtasks", args, { windowsHide: true, encoding: "utf8" },
  (error, stdout, stderr) => resolve({ ok: !error, text: `${stdout}${stderr}` })));

/** Кто владеет задачей: без учётной записи она завелась бы на вход любого пользователя. */
function userSid() {
  try {
    const match = /S-1-5-(?:[0-9]+-)+[0-9]+/.exec(execFileSync("whoami", ["/user", "/fo", "csv", "/nh"], { encoding: "utf8", windowsHide: true }));
    return match ? match[0] : "";
  } catch { return ""; }
}

/** Задача «Praxis Proxy» — как «Helene Relay Proxy» у Джарвиса: скрытая, при входе владельца,
 *  перезапускается при сбое, без ограничения времени. */
function taskXml() {
  const sid = userSid();
  const who = sid ? `<UserId>${sid}</UserId>` : "";
  const quote = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const xml = `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Praxis proxy: an SSH tunnel to tinyproxy for the Praxis window and body without a VPN. Loopback only, pinned server key, reconnects after a network loss.</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">${who}
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Count>3</Count>
      <Interval>PT1M</Interval>
    </RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <LogonTrigger>
      ${who}
      <Enabled>true</Enabled>
      <Delay>PT15S</Delay>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>${quote(process.execPath)}</Command>
      <Arguments>--proxy-keeper</Arguments>
      <WorkingDirectory>${quote(path.dirname(process.execPath))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
`;
  return Buffer.concat([Buffer.from([0xFF, 0xFE]), Buffer.from(xml, "utf16le")]);
}

/** Попросить хранителя закончить работу (он читает файл stop раз в секунду); не вышел — снять. */
async function stopKeeper() {
  const before = proxy.readStatus();
  if (!before || !Number.isInteger(before.pid) || before.pid <= 0) return;
  try { fs.mkdirSync(proxy.FOLDER, { recursive: true }); fs.writeFileSync(path.join(proxy.FOLDER, "stop"), ""); } catch {}
  for (let _ = 0; _ < 12; _++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try { process.kill(before.pid, 0); } catch { return; }              // его больше нет
  }
  for (const pid of [before.plink_pid, before.pid]) {
    if (Number.isInteger(pid) && pid > 0) { try { process.kill(pid); } catch {} }
  }
}

/** Задача держит туннель, когда окно не запущено: он нужен и телу Праксис. Заводится сама, если
 *  прокси настроен и владелец его не выключал; туннель не стоит — задача запускается. */
async function applyProxyTask() {
  if (process.platform !== "win32") return;
  const config = proxy.readConfig();
  if (saved.proxyTask === false) {
    await schtasks(["/Delete", "/TN", proxy.TASK, "/F"]);                // задачи может и не быть — не ошибка
    await stopKeeper();
    return;
  }
  if (!config) return;
  if (!(await schtasks(["/Query", "/TN", proxy.TASK])).ok) {
    const file = path.join(os.tmpdir(), "praxis-proxy-task.xml");
    fs.writeFileSync(file, taskXml());
    const created = await schtasks(["/Create", "/TN", proxy.TASK, "/XML", file, "/F"]);
    try { fs.unlinkSync(file); } catch {}
    if (!created.ok) logShell(`proxy task not created: ${created.text.trim().slice(0, 300)}`);
  }
  if (!(await proxy.alive(config.listen_port))) await schtasks(["/Run", "/TN", proxy.TASK]);
}

async function proxyState() {
  const config = proxy.readConfig();
  const status = proxy.readStatus();
  return {
    network: ["auto", "direct", "proxy"].includes(saved.network) ? saved.network : "auto",
    route: saved.route,
    proxyTask: saved.proxyTask !== false,
    configured: !!config,
    proxy_url: config ? config.proxy_url : "",
    alive: config ? await proxy.alive(config.listen_port, 300) : false,
    keeper: status ? { state: status.state, at: status.at, why: status.why || "", attempt: status.attempt || 0 } : null,
  };
}

/** Поднять окно поверх остальных. Простого show() и focus() Windows мало, когда окно
 *  поднимает не то движение мыши, что породило процесс: окно открывается, но остаётся
 *  за активным. Приём тот же, что в оболочке Hélène: показать, поднять в порядке окон,
 *  на миг поставить «поверх всех» и снять — окно остаётся сверху. */
function bringToFront() {
  const target = window;
  if (!target || target.isDestroyed()) return;
  raiseRequested = false;
  if (target.isMinimized()) target.restore();
  target.show();
  target.moveTop();
  target.focus();
  target.setAlwaysOnTop(true);
  setTimeout(() => { if (target === window && !target.isDestroyed()) target.setAlwaysOnTop(false); }, 120);
}

/** Размер, в котором страница размечена, — словами самой страницы. */
function pageViewport() {
  if (!window || window.isDestroyed()) return Promise.resolve(null);
  return window.webContents.executeJavaScript("({ w: innerWidth, h: innerHeight })").catch(() => null);
}

/** Настоящая смена размера окна — как если владелец потянул за край: Chromium заново
 *  сообщает странице её размер. Развёрнутое окно на миг снимается и ставится обратно. */
function nudgeSize() {
  if (window.isMaximized()) { window.unmaximize(); window.maximize(); }
  else {
    const bounds = window.getBounds();
    window.setBounds({ ...bounds, width: bounds.width + 1 });
    window.setBounds(bounds);
  }
}

/** Страница внутри окна должна быть ровно размером с окно. После смены экрана — удалённый
 *  рабочий стол подключился и отключился, монитор, масштаб — Chromium может оставить
 *  странице её прежний размер: окно уже большое, а страница размечена под маленькое, и
 *  справа и снизу остаётся пустая бумага. Оболочка сверяет размеры и, найдя разницу,
 *  напоминает окну его размер; не помогло — перечитывает страницу (раздел остаётся тем же)
 *  и напоминает ещё раз; после трёх заходов на этом экране перестаёт, чтобы не мигать. */
async function ensureFits(step = 0) {
  if (fitting || !window || window.isDestroyed() || window.isMinimized()) return;
  fitting = true;
  try {
    const [width, height] = window.getContentSize();
    const zoom = window.webContents.getZoomFactor() || 1;
    const page = await pageViewport();
    const same = !!page && Math.abs(page.w - width / zoom) <= 1 && Math.abs(page.h - height / zoom) <= 1;
    if (same) { repairing = false; return; }
    if (step === 0 && repairs >= 3) return;        // на этом экране уже чинили и не вышло: не мигать
    repairing = true;
    const again = (next, delay) => { fitTimer = setTimeout(() => { fitTimer = null; void ensureFits(next).catch(logShell); }, delay); };
    if (step === 1 && isUi(window.webContents.getURL())) {
      logShell(`page size: reminder did not help (page ${page && page.w}×${page && page.h}, window ${width}×${height}) — reloading the page`);
      window.webContents.reload();
      again(2, 2500);
      return;
    }
    if (step > 2) { repairing = false; return; }   // не помогло — ждёт следующего повода
    if (step === 0) repairs++;
    logShell(`page size: page ${page && page.w}×${page && page.h}, window ${width}×${height} — reminder ${step === 0 ? 1 : 2}`);
    nudgeSize();
    again(step + 1, 500);
  } finally { fitting = false; }
}

// Пока идёт починка, события размера окна (их она сама и порождает) проверку не перезапускают.
const scheduleFit = (delay = 450) => {
  if (repairing) return;
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => { fitTimer = null; void ensureFits(); }, delay);
};

function makeWindow() {
  channel = session.fromPartition("persist:praxis");
  const bounds = place();
  const maximized = saved.startMaximized !== false || !!saved.maximized;
  const dark = nativeTheme.shouldUseDarkColors;
  window = new BrowserWindow({
    x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
    minWidth: MINIMUM.width, minHeight: MINIMUM.height,
    show: false, title: "Праксис", icon: path.join(__dirname, "praxis.ico"),
    backgroundColor: dark ? PAPER.dark : PAPER.light,
    titleBarStyle: "hidden", titleBarOverlay: { color: dark ? PAPER.dark : PAPER.light, symbolColor: dark ? "#efe9df" : "#262320", height: CHROME },
    autoHideMenuBar: true,
    webPreferences: { session: channel, preload: path.join(__dirname, "preload.cjs"),
      sandbox: true, contextIsolation: true, nodeIntegration: false, spellcheck: false },
  });
  const contents = window.webContents;
  contents.on("will-navigate", (event, url) => { if (!isUi(url) && url !== login) event.preventDefault(); });
  contents.on("will-redirect", (event, url) => { if (!isUi(url) && url !== login) event.preventDefault(); });
  contents.setWindowOpenHandler(({ url }) => {
    // ссылки из её переписки — в браузер по умолчанию; внутри окна чужих страниц не бывает
    try { const u = new URL(url); if (u.protocol === "https:" && !u.username && !u.password) void shell.openExternal(u.href); } catch {}
    return { action: "deny" };
  });
  // Ctrl+R и F5 — перечитать окно; Ctrl + «+» / «−» / «0» — масштаб. Меню у окна нет (и его
  // клавиш вместе с ним), поэтому клавиши читает сама оболочка; код клавиши не зависит от раскладки.
  const ZOOM_KEYS = { Equal: 1, NumpadAdd: 1, Minus: -1, NumpadSubtract: -1, Digit0: 0, Numpad0: 0 };
  contents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown" || input.alt || input.meta || input.isAutoRepeat) return;
    if (input.code === "F5" || (input.control && !input.shift && input.code === "KeyR")) {
      event.preventDefault();
      if (contents.getURL() === login) void openConnection("");
      else contents.reload();
      return;
    }
    const step = input.control && isUi(contents.getURL()) ? ZOOM_KEYS[input.code] : undefined;
    if (step === undefined || (input.shift && input.code !== "Equal")) return;
    event.preventDefault();
    setZoom(step === 0 ? 1 : saved.zoom * Math.pow(ZOOM.step, step));
  });
  // Страница не открылась (сеть пропала в момент перечитывания) — не показывать браузерную
  // заглушку с ошибкой: страница входа скажет словами и будет пробовать сама.
  contents.on("did-fail-load", (_event, code, _description, url, isMainFrame) => {
    if (!isMainFrame || code === -3 || url === login || !window || window.isDestroyed()) return;
    void window.loadURL(login).catch(() => undefined);
  });
  // Масштаб, выбранный владельцем, ставится на новой странице сразу — до первой краски.
  contents.on("did-navigate", (_event, url) => { if (isUi(url) && saved.zoom !== 1) contents.setZoomFactor(saved.zoom); });
  contents.on("did-finish-load", () => { paintChrome(); scheduleFit(300); });
  contents.on("context-menu", (_event, params) => {
    if (!isUi(contents.getURL())) return;
    Menu.buildFromTemplate([
      { label: "Обновить окно", accelerator: "F5", click: () => contents.reload() },
      { type: "separator" },
      { label: "Копировать", role: "copy", enabled: params.editFlags.canCopy },
      { label: "Вставить", role: "paste", enabled: params.editFlags.canPaste },
    ]).popup({ window });
  });
  for (const name of ["maximize", "unmaximize", "move", "resize"]) window.on(name, () => { remember(); sendState(); paintChrome(); });
  window.on("resize", () => scheduleFit());
  for (const name of ["show", "focus", "restore"]) window.on(name, () => scheduleFit(250));
  window.on("closed", () => { window = null; clearTimeout(fitTimer); });
  window.once("ready-to-show", () => {
    if (maximized) window.maximize();          // до показа: окно сразу раскрывается на весь экран, без прыжка
    if (startedByLogin && !raiseRequested) window.minimize();   // вместе с Windows — свёрнутым (но развёрнутым по размеру), не поверх открытого
    else bringToFront();
  });
  writeSettings();                            // настройки окна известны и до первой правки размера
  applyLoginItem();
  void window.loadURL(login);
}

ipcMain.handle("praxis:connect", (event, key) => {
  if (!window || event.sender !== window.webContents || event.senderFrame !== event.sender.mainFrame
      || event.senderFrame.url !== login || typeof key !== "string") return refused("ignored", "");
  return openConnection(key.trim());
});

ipcMain.handle("praxis:settings", (event) => (isOnScreen(event) ? publicSettings() : null));
ipcMain.handle("praxis:settings:set", (event, patch) => {
  if (!isOnScreen(event) || !patch || typeof patch !== "object") return null;
  if (typeof patch.autostart === "boolean") saved.autostart = patch.autostart;
  if (typeof patch.startMaximized === "boolean") saved.startMaximized = patch.startMaximized;
  writeSettings();
  applyLoginItem();
  return publicSettings();
});
// Масштаб страницы: { step: ±1 } — на шаг, { factor } — точно, { reset: true } — 100 %. Возвращает новый масштаб.
// Прокси без VPN: состояние — странице, а решение — владельцу (какая дорога, держать ли задачу).
ipcMain.handle("praxis:proxy", (event) => (isOnScreen(event) ? proxyState() : null));
ipcMain.handle("praxis:proxy:set", async (event, patch) => {
  if (!isOnScreen(event) || !patch || typeof patch !== "object") return null;
  if (["auto", "direct", "proxy"].includes(patch.network)) { saved.network = patch.network; saved.route = ""; }
  if (typeof patch.proxyTask === "boolean") saved.proxyTask = patch.proxyTask;
  writeSettings();
  await applyProxyTask();
  return proxyState();
});
ipcMain.handle("praxis:zoom", (event, change) => {
  if (!isOnScreen(event) || !isUi(event.senderFrame.url) || !change || typeof change !== "object") return saved.zoom;
  if (change.reset === true) return setZoom(1);
  if (Number.isFinite(Number(change.factor))) return setZoom(Number(change.factor));
  const step = Number(change.step);
  return step ? setZoom(saved.zoom * Math.pow(ZOOM.step, Math.sign(step))) : saved.zoom;
});

const WINDOW_COMMANDS = {
  "window:close": (target) => target.close(),
  "window:minimize": (target) => target.minimize(),
  "window:maximize": (target) => { if (target.isMaximized()) target.unmaximize(); else target.maximize(); },
  reload: (target) => target.webContents.reload(),
};
ipcMain.on("praxis:window", (event, command) => {
  if (typeof command !== "string" || !window || !isOnScreen(event)) return;
  // страница входа умеет только закрыть окно
  if (event.senderFrame.url === login) { if (command === "window:close") window.close(); return; }
  if (WINDOW_COMMANDS[command]) WINDOW_COMMANDS[command](window);
});
ipcMain.on("praxis:state", (event) => { if (isOnScreen(event)) sendState(); });
ipcMain.on("praxis:chrome", (event) => { if (isOnScreen(event)) paintChrome(); });

nativeTheme.on("updated", paintChrome);

/** Журнал оболочки — файл рядом с настройками: ошибки и починки размера страницы (без ключей), чтобы не пропасть молча. */
function logShell(note) {
  try {
    fs.mkdirSync(profile, { recursive: true });
    fs.appendFileSync(path.join(profile, "shell.log"), `${new Date().toISOString()} ${String(note && note.stack ? note.stack : note).slice(0, 2000)}\n`);
  } catch {}
}

// Тихий присмотр: раз в десять секунд сверить размер страницы с окном, окно при этом не трогая.
setInterval(() => { if (window && !window.isDestroyed() && !fitting && fitTimer === null) void ensureFits().catch(logShell); }, 10000);

if (app.requestSingleInstanceLock()) {
  app.on("second-instance", (_event, argv) => {
    // Ярлык вторым разом — открыть окно поверх остальных; автозапуск — не вытягивать на экран.
    if (argv.includes("--autostart")) return;
    if (window && !window.isDestroyed()) bringToFront();
    else raiseRequested = true;               // окно ещё создаётся: покажут его уже поднятым
  });
  app.whenReady().then(() => {
    // Экран сменился (подключился или отвалился удалённый рабочий стол, другой монитор,
    // масштаб) — страница могла не узнать свой новый размер. (`screen` живёт только после ready.)
    for (const name of ["display-metrics-changed", "display-added", "display-removed"]) {
      screen.on(name, () => { repairs = 0; scheduleFit(700); });
    }
    makeWindow();
    void applyProxyTask().catch(logShell);   // туннель без VPN: задача при входе и хранитель, если он не стоит
    void openConnection("");
  }).catch(logShell);
  app.on("window-all-closed", () => app.quit());
} else app.quit();
