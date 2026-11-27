// Прокси Праксис — общее для окна и хранителя: где лежат настройки и состояние, как их
// прочитать и как узнать, жив ли местный прокси.
//
// Сам прокси — тот же приём, что у Джарвиса в Hélène («Helene Relay Proxy»): SSH-туннель до
// машины с tinyproxy открывает на этом компьютере HTTP-порт 127.0.0.1:<listen_port>, и через
// него окно и тело Праксис доходят до её сервера из России без VPN. Туннель держит хранитель
// (proxy-keeper.cjs) — обычно из задачи «Praxis Proxy» при входе в Windows.
"use strict";
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const FOLDER = path.join(process.env.LOCALAPPDATA, "Praxis", "DesktopWindow", "proxy");
const CONFIG = path.join(FOLDER, "config.json");
const STATUS = path.join(FOLDER, "status.json");
const TASK = "Praxis Proxy";

const DEFAULTS = {
  server: "",              // машина с tinyproxy, SSH
  port: 22,
  user: "",
  password: "",            // пароль SSH-учётной записи туннеля (ту же, что в start_server_proxy.bat)
  hostkey: "",             // отпечаток ключа машины (SHA256:…): не совпал — не входить
  listen_port: 18890,      // местный HTTP-прокси
  remote_host: "127.0.0.1",
  remote_port: 8888,       // tinyproxy на той машине
  plink: "",               // путь к plink.exe; пусто — рядом с программой
  dial_listen: "",         // прозрачный выход для программ, которые о прокси не знают (тело Праксис):
  dial_target: "",         //   всё, что пришло на dial_listen, уходит через прокси к dial_target
};

const json = (file) => {
  try {
    const text = fs.readFileSync(file, "utf8");
    const value = JSON.parse(text.charCodeAt(0) === 0xFEFF ? text.slice(1) : text);
    return value && typeof value === "object" ? value : null;
  } catch { return null; }
};

const port = (value, fallback) => Number.isInteger(value) && value >= 1 && value <= 65535 ? value : fallback;

/** Настройки прокси. null — прокси не настроен или настроен неполно: тогда ходим напрямую. */
function readConfig() {
  const value = json(CONFIG);
  if (!value) return null;
  const config = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS)) if (typeof value[key] === typeof DEFAULTS[key]) config[key] = value[key];
  const plain = (text) => text.length > 0 && text.length < 256 && !/[\s"'\\]/.test(text);
  if (!/^[0-9a-z.:-]+$/i.test(config.server) || !plain(config.server)) return null;
  if (!/^[0-9a-z_-]+$/i.test(config.user) || !plain(config.user)) return null;
  if (!/^sha256:[0-9a-z+/]+={0,2}$/i.test(config.hostkey)) return null;   // без пина ключа не входить
  if (!config.password) return null;
  config.port = port(config.port, 22);
  config.listen_port = port(config.listen_port, DEFAULTS.listen_port);
  config.remote_port = port(config.remote_port, DEFAULTS.remote_port);
  if (!/^[0-9a-z.:-]+$/i.test(config.remote_host)) config.remote_host = DEFAULTS.remote_host;
  // прозрачный выход — только парой «где слушать → куда вести», оба «адрес:порт»; иначе его нет
  const endpoint = (text) => /^[0-9a-z.]+:[0-9]{1,5}$/i.test(text) && port(Number(text.split(":").pop()), 0) ? text : "";
  config.dial_target = endpoint(config.dial_target);
  config.dial_listen = config.dial_target ? endpoint(config.dial_listen) : "";
  if (!config.dial_listen) config.dial_target = "";
  config.plink = config.plink || path.join(__dirname, "plink.exe");
  config.proxy_url = `http://127.0.0.1:${config.listen_port}`;
  return config;
}

/** Состояние хранителя туннеля (см. proxy-keeper.cjs) или null, если он не писал его. */
function readStatus() {
  const value = json(STATUS);
  return value && typeof value === "object" && typeof value.state === "string" ? value : null;
}

/** Слушает ли кто-нибудь местный порт прокси. */
function alive(listen, timeout = 400) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port: listen });
    const done = (yes) => { socket.destroy(); resolve(yes); };
    socket.setTimeout(timeout, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

/** Отвечает ли прокси: tinyproxy за туннелем отвечает на свою служебную страницу. */
function answers(listen, timeout = 5000) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port: listen });
    let data = "";
    const done = (yes) => { socket.destroy(); resolve(yes); };
    socket.setTimeout(timeout, () => done(false));
    socket.once("error", () => done(false));
    socket.once("connect", () => socket.write("GET http://tinyproxy.stats/ HTTP/1.0\r\nHost: tinyproxy.stats\r\n\r\n"));
    socket.on("data", (piece) => {
      data += piece.toString("latin1");
      if (/\r\n/.test(data)) done(/^HTTP\/1\.[01] 200\b/.test(data));
    });
  });
}

module.exports = { FOLDER, CONFIG, STATUS, TASK, DEFAULTS, readConfig, readStatus, alive, answers };
