// Хранитель прокси Праксис — без окна (praxis.exe --proxy-keeper). Держит SSH-туннель до машины
// с tinyproxy и вместе с ним местный HTTP-порт; туннель падает — поднимается сам. Тот же приём,
// что у Джарвиса в Hélène: состояние — в status.json, журнал — в keeper.log, второй хранитель
// не заводится. Ключ машины проверяется по отпечатку из настроек: не совпал — не входить.
//
//   praxis.exe --proxy-keeper                работать постоянно (задача «Praxis Proxy» при входе)
//   praxis.exe --proxy-keeper --proxy-test=N  пробный запуск на N секунд: печатает состояние
//                                             и выходит с кодом 0, если прокси отвечал
"use strict";
const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { FOLDER, readConfig, answers } = require("./proxy-config.cjs");
const net = require("node:net");

const LOG = path.join(FOLDER, "keeper.log");
const STATUS = path.join(FOLDER, "status.json");
const STOP = path.join(FOLDER, "stop");                    // окно создаёт файл — хранитель заканчивает работу
const PASSWORD_FILE = path.join(FOLDER, "plink.pw");
const PROBE_EVERY = 10000;                                 // как часто спрашивать прокси, жив ли он
const STALE_AFTER = 90000;                                 // так долго туннель стоит, а прокси молчит — перезапустить
const PASSWORD_GONE = 15000;                               // plink читает файл пароля при старте — потом он не нужен
const CONNECT_WAIT = 10000;                                // сколько ждать ответа прокси на CONNECT
const ESCAPE = String.fromCharCode(27);
const PAINT = new RegExp(ESCAPE + "\\[[0-9;?]*[A-Za-z]", "g");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const now = () => new Date().toISOString();
const dial = { listen: "", target: "", error: "", served: 0 };     // прозрачный выход (см. startDial)

function log(line) {
  try {
    fs.mkdirSync(FOLDER, { recursive: true });
    if (fs.existsSync(LOG) && fs.statSync(LOG).size > 512 * 1024) fs.renameSync(LOG, LOG.replace(/\.log$/, ".previous.log"));
    fs.appendFileSync(LOG, `${now()} ${line}\n`);
  } catch { /* без журнала хранитель всё равно работает */ }
}

function writeStatus(state) {
  try {
    fs.mkdirSync(FOLDER, { recursive: true });
    const temporary = `${STATUS}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ pid: process.pid, at: now(), ...state,
      ...(dial.listen ? { dial: { listen: dial.listen, target: dial.target, error: dial.error, served: dial.served } } : {}) }));
    fs.renameSync(temporary, STATUS);
  } catch { /* состояние — для окна и для человека, не для работы туннеля */ }
}

/** Прозрачный выход для программ, которые о прокси не знают (тело Праксис): слушает
 *  dial_listen и ведёт каждое соединение через прокси к dial_target. Что внутри — TLS,
 *  WebSocket, — хранитель не разбирает: для программы это просто дорога до сервера под его
 *  настоящим именем. Прокси не стоит — соединение закрывается сразу, и программа идёт своей
 *  прямой дорогой. Ведёт только к одному заданному адресу: это не открытый прокси. */
function startDial(config) {
  const [host, port] = config.dial_listen.split(":");
  dial.listen = config.dial_listen;
  dial.target = config.dial_target;
  const server = net.createServer((client) => {
    const upstream = net.connect({ host: "127.0.0.1", port: config.listen_port });
    let stage = "connect";                                  // connect → tunnel
    let head = Buffer.alloc(0);
    let early = [];                                         // что программа прислала, пока прокси отвечал
    let earlyBytes = 0;
    const drop = () => { client.destroy(); upstream.destroy(); };
    client.setNoDelay(true);
    upstream.setNoDelay(true);
    client.setTimeout(CONNECT_WAIT, () => { if (stage !== "tunnel") drop(); });
    client.once("error", drop);
    upstream.once("error", drop);
    client.once("close", () => upstream.destroy());
    upstream.once("close", () => client.destroy());
    upstream.once("connect", () => upstream.write(`CONNECT ${config.dial_target} HTTP/1.1\r\nHost: ${config.dial_target}\r\n\r\n`));
    upstream.on("data", (piece) => {
      if (stage === "tunnel") {
        if (!client.write(piece)) upstream.pause();
        return;
      }
      head = Buffer.concat([head, piece]);
      const end = head.indexOf("\r\n\r\n");
      if (end < 0) {
        if (head.length > 8192) drop();                     // прокси отвечает не тем
        return;
      }
      if (!/^HTTP\/1\.[01] 200\b/.test(head.toString("latin1").split("\r\n")[0])) { drop(); return; }
      stage = "tunnel";
      client.setTimeout(0);
      dial.served += 1;
      const rest = head.subarray(end + 4);
      if (rest.length) client.write(rest);
      // программа начала говорить сразу (TLS, WebSocket) — её первые байты ждут прокси здесь
      for (const part of early) if (!upstream.write(part)) { client.pause(); break; }
      early = [];
    });
    client.on("data", (piece) => {
      if (stage === "tunnel") {
        if (!upstream.write(piece)) client.pause();
        return;
      }
      earlyBytes += piece.length;
      if (earlyBytes > 256 * 1024) { drop(); return; }       // соединение без ответа прокси — не буферизуем
      early.push(piece);
    });
    client.on("drain", () => upstream.resume());
    upstream.on("drain", () => client.resume());
  });
  server.on("error", (error) => {
    dial.error = String((error && error.code) || error);
    log(`dial ${config.dial_listen} not listening: ${dial.error}`);
  });
  server.listen(Number(port), host, () => {
    dial.error = "";
    log(`dial listening ${config.dial_listen} → ${config.dial_target} through ${config.proxy_url}`);
  });
  return server;
}

/** Один запуск туннеля: заканчивается, когда plink вышел (или его попросили). Пока он жив —
 *  пробует прокси и пишет состояние. `interrupted` — правда, если прокси отвечал. */
function tunnel(config, state) {
  return new Promise((resolve) => {
    const lines = [];
    let child = null;
    let plinkPid = null;
    let settled = false;
    let probe = null;
    let removePassword = null;
    let lastOk = Date.now();
    const started = Date.now();
    const remember = (piece) => {
      for (const line of piece.toString("utf8").replace(PAINT, "").split(/\r?\n/)) if (line.trim()) lines.push(line.trim());
      if (lines.length > 60) lines.splice(0, lines.length - 60);
    };
    const finish = (code) => {
      if (settled) return;
      settled = true;
      clearInterval(probe);
      clearTimeout(removePassword);
      try { fs.unlinkSync(PASSWORD_FILE); } catch {}
      resolve({ code, lines: lines.slice(), lived: Date.now() - started });
    };
    try {
      fs.mkdirSync(FOLDER, { recursive: true });
      fs.writeFileSync(PASSWORD_FILE, `${config.password}\n`, { mode: 0o600 });
      child = spawn(config.plink, ["-ssh", "-batch", "-noagent", "-noshare", "-N", "-T",
        "-P", String(config.port), "-l", config.user, "-pwfile", PASSWORD_FILE, "-hostkey", config.hostkey,
        "-L", `127.0.0.1:${config.listen_port}:${config.remote_host}:${config.remote_port}`, config.server],
        { cwd: FOLDER, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    } catch (error) {
      remember(String(error && error.message ? error.message : error));
      finish(-1);
      return;
    }
    plinkPid = child.pid;
    child.stdout.on("data", remember);
    child.stderr.on("data", remember);
    child.on("error", (error) => { remember(String(error && error.message ? error.message : error)); finish(-1); });
    child.on("exit", (code) => finish(Number.isInteger(code) ? code : -1));
    removePassword = setTimeout(() => { try { fs.unlinkSync(PASSWORD_FILE); } catch {} }, PASSWORD_GONE);
    writeStatus({ state: "tunnel", attempt: state.attempts, plink_pid: plinkPid, listen_port: config.listen_port, proxy_url: config.proxy_url });
    probe = setInterval(() => {
      void answers(config.listen_port).then((ok) => {
        if (settled) return;
        if (ok) {
          lastOk = Date.now();
          state.answered = true;
          writeStatus({ state: "ok", attempt: state.attempts, plink_pid: plinkPid, listen_port: config.listen_port, proxy_url: config.proxy_url });
        } else if (Date.now() - lastOk > STALE_AFTER && child) {
          log("proxy silent for too long — restarting the tunnel");
          child.kill();
        }
      });
    }, PROBE_EVERY);
  });
}

/** Что сказал plink: почему вышло. */
function reason(outcome) {
  const text = outcome.lines.join("\n");
  if (/host key/i.test(text) && /not accept|does not match|breach|mismatch|different/i.test(text)) return "hostkey";
  if (/access denied|authentication refused|password authentication failed|unable to authenticate/i.test(text)) return "auth";
  if (/local port|forwarding not permitted|administratively prohibited/i.test(text)) return "forward";
  if (/refused|timed out|unreachable|no route|network error/i.test(text)) return "network";
  return outcome.code === -1 ? "start" : "exit";
}

async function supervise(config, testSeconds) {
  const state = { attempts: 0, answered: false };
  let stopping = false;
  let delay = 5000;
  // Остановиться — значит снять туннель: иначе ждём, пока он упадёт сам.
  const stop = () => {
    stopping = true;
    try {
      const status = JSON.parse(fs.readFileSync(STATUS, "utf8"));
      if (Number.isInteger(status.plink_pid) && status.plink_pid > 0) process.kill(status.plink_pid);
    } catch {}
  };
  app.on("before-quit", stop);
  let stopSeen = false;
  const watch = setInterval(() => {
    if (!stopSeen && fs.existsSync(STOP)) {
      stopSeen = true;
      log("stop requested — finishing");
      stop();
    }
  }, 1000);
  const test = testSeconds ? setTimeout(stop, testSeconds * 1000) : null;
  writeStatus({ state: "connecting", listen_port: config.listen_port, proxy_url: config.proxy_url });
  while (!stopping) {
    state.attempts++;
    log(`tunnel attempt ${state.attempts} → ${config.user}@${config.server}:${config.port}`);
    const outcome = await tunnel(config, state);
    if (stopping) break;
    const why = reason(outcome);
    log(`tunnel ended: ${why} (exit ${outcome.code}, lived ${Math.round(outcome.lived / 1000)} s)`);
    if (why === "hostkey") {                       // ключ машины не тот: не перебираем и не входим
      writeStatus({ state: "failed", why, attempt: state.attempts, exit_code: outcome.code, detail: outcome.lines.slice(-6),
        listen_port: config.listen_port, proxy_url: config.proxy_url });
      break;
    }
    writeStatus({ state: "retrying", why, attempt: state.attempts, exit_code: outcome.code, detail: outcome.lines.slice(-4),
      listen_port: config.listen_port, proxy_url: config.proxy_url, next_in_s: Math.round(delay / 1000) });
    await sleep(delay);
    delay = outcome.lived > 60000 ? 5000 : Math.min(delay * 2, 60000);
  }
  clearInterval(watch);
  if (test) clearTimeout(test);
  return state.answered;
}

function run() {
  const test = /--proxy-test=(\d+)/.exec(process.argv.join(" "));
  const testSeconds = test ? Number(test[1]) : 0;
  app.setPath("userData", path.join(FOLDER, "keeper"));
  app.disableHardwareAcceleration();
  app.setAppUserModelId("Praxis.ProxyKeeper");
  if (!app.requestSingleInstanceLock()) { app.quit(); return; }
  app.whenReady().then(async () => {
    try { fs.unlinkSync(STOP); } catch {}              // прошлая просьба остановиться не мешает новому запуску
    try { fs.unlinkSync(PASSWORD_FILE); } catch {}     // и забытый файл пароля тоже
    const config = readConfig();
    if (!config) {
      writeStatus({ state: "unconfigured" });         // нет настроек — нечего держать; окно скажет об этом словами
      log("no proxy configuration — exiting");
      app.quit();
      return;
    }
    if (!fs.existsSync(config.plink)) {
      writeStatus({ state: "failed", why: "plink", detail: [config.plink] });
      log(`plink not found: ${config.plink}`);
      app.quit();
      return;
    }
    log(`keeper started (pid ${process.pid})`);
    if (config.dial_listen) startDial(config);
    const answered = await supervise(config, testSeconds);
    writeStatus({ state: "stopped", listen_port: config.listen_port, proxy_url: config.proxy_url });
    log("keeper stopped");
    if (testSeconds) {
      // итог пробного запуска — тому, кто его запустил
      try { process.stdout.write(`${JSON.stringify({ answered, log: fs.existsSync(LOG) ? fs.readFileSync(LOG, "utf8").trim().split("\n").slice(-8) : [] })}\n`); } catch {}
      app.exit(answered ? 0 : 1);
      return;
    }
    app.quit();
  }).catch((error) => {
    writeStatus({ state: "failed", why: "error", detail: [String(error && error.message ? error.message : error)] });
    log(`keeper failed: ${String(error && error.stack ? error.stack : error)}`);
    app.quit();
  });
}

module.exports = { run };
