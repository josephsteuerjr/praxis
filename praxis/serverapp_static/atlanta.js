/* Атланта — сервер как город в разрезе. Клиент: вход, живое небо, город, этажи. */
(() => {
"use strict";

/* ------------------------------------------------------------------ мелочи */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const num = (v, d = 0) => (v == null || isNaN(v)) ? d : +v;
const lerp = (a, b, t) => a + (b - a) * t;
const frac = v => v - Math.floor(v);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const NB = " ";
const fmt = (v, d = 0) => num(v).toFixed(d).replace(".", ",");
const UNITS = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
function scale(v) { v = num(v); let i = 0; while (v >= 1024 && i < 4) { v /= 1024; i++; } return [v, i]; }
function bytes(v) { const [x, i] = scale(v); return fmt(x, x < 10 && i ? 1 : 0) + NB + UNITS[i]; }
const rate = v => bytes(v) + "/с";
function plural(n, one, few, many) {
  n = Math.abs(Math.floor(n)) % 100; const d = n % 10;
  if (n > 10 && n < 20) return many; if (d > 1 && d < 5) return few; if (d === 1) return one; return many;
}
const count = (n, one, few, many) => `${n}${NB}${plural(n, one, few, many)}`;
function uptime(s) {
  s = num(s); const d = s / 86400 | 0, h = (s % 86400) / 3600 | 0, m = (s % 3600) / 60 | 0;
  if (d) return `${count(d, "день", "дня", "дней")} ${count(h, "час", "часа", "часов")}`;
  if (h) return `${count(h, "час", "часа", "часов")} ${count(m, "минуту", "минуты", "минут")}`;
  return count(m, "минуту", "минуты", "минут");
}
function ago(ts) {
  const s = Date.now() / 1000 - num(ts);
  if (s < 90) return "только что";
  if (s < 3600) return `${count(Math.round(s / 60), "минуту", "минуты", "минут")} назад`;
  if (s < 86400) return `${count(Math.round(s / 3600), "час", "часа", "часов")} назад`;
  return `${count(Math.round(s / 86400), "день", "дня", "дней")} назад`;
}
const whenShort = ts => new Date(num(ts) * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function durRu(s) {
  s = String(s || "").toLowerCase().trim();
  if (/less than a second/.test(s)) return "меньше секунды";
  if (/about a minute|^a minute/.test(s)) return "минуту";
  if (/about an hour|^an hour/.test(s)) return "час";
  const m = /(\d+)\s*(second|minute|hour|day|week|month|year)/.exec(s);
  if (!m) return s;
  const n = +m[1], W = { second: ["секунду", "секунды", "секунд"], minute: ["минуту", "минуты", "минут"], hour: ["час", "часа", "часов"], day: ["день", "дня", "дней"], week: ["неделю", "недели", "недель"], month: ["месяц", "месяца", "месяцев"], year: ["год", "года", "лет"] }[m[2]];
  return count(n, W[0], W[1], W[2]);
}
function statusRu(s) {
  s = String(s || ""); let m;
  if ((m = /^Up (.+?)(?: \((.+)\))?$/.exec(s))) return "работает " + durRu(m[1]) + (m[2] === "unhealthy" ? ", нездоров" : "");
  if ((m = /^Exited \((\d+)\) (.+) ago$/.exec(s))) return (m[1] === "0" ? "закончил работу " : `упал с кодом ${m[1]} `) + durRu(m[2]) + " назад";
  if (s === "Created") return "построен, но не запускался";
  return s;
}
function hash32(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
const rnd = (seed, k) => frac(Math.sin((seed % 99991) * 0.0137 + k * 12.9898) * 43758.5453);
const mix = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const rgb = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
const hex = c => "#" + c.map(v => clamp(v | 0, 0, 255).toString(16).padStart(2, "0")).join("");

/* ------------------------------------------------------------------ вход */
const TG = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData ? window.Telegram.WebApp : null;
const SKEY = "atlanta.session", SKEY_OLD = "atlas.session";   // старая иконка на экране не должна разлогиниться
let sessionTok = "";

function storedToken() {
  try {
    const cur = localStorage.getItem(SKEY); if (cur) return cur;
    const old = localStorage.getItem(SKEY_OLD);
    if (old) { localStorage.setItem(SKEY, old); localStorage.removeItem(SKEY_OLD); }
    return old || "";
  } catch (_) { return ""; }
}
function saveToken(t) { try { localStorage.setItem(SKEY, t); } catch (_) {} }
function readFragmentToken() {
  const m = /[#&]s=([A-Za-z0-9._-]+)/.exec(location.hash || "");
  if (!m) return "";
  saveToken(m[1]);
  history.replaceState(null, "", location.pathname + location.search);
  return m[1];
}
function authHeaders() {
  const h = {};
  if (TG && TG.initData) h["X-Telegram-Init-Data"] = TG.initData;
  else if (sessionTok) h["Authorization"] = "Bearer " + sessionTok;
  return h;
}
async function api(path) {
  const r = await fetch(path, { headers: authHeaders(), credentials: "same-origin", cache: "no-store" });
  if (r.status === 403 || r.status === 401) throw { forbidden: true };
  if (!r.ok) {
    let why = ""; try { why = (await r.json()).error || ""; } catch (_) {}
    throw new Error(why || ("ответ " + r.status));
  }
  return r.json();
}
async function ensureSession() {
  try { const j = await api("/api/session"); if (j && j.token) { sessionTok = j.token; saveToken(sessionTok); } } catch (_) {}
}

let authed = false;
async function boot() {
  sessionTok = readFragmentToken() || storedToken();
  if (TG) { try { TG.ready(); TG.expand(); TG.disableVerticalSwipes && TG.disableVerticalSwipes(); } catch (_) {} }
  let ok = false;
  try { await api("/api/overview"); ok = true; } catch (_) {}
  // Продлеваем свой ключ на каждом входе: и из Telegram, и с уже связанного устройства.
  if (ok && (TG || sessionTok)) await ensureSession();
  setTimeout(() => {
    $("#boot").classList.add("gone");
    setTimeout(() => { $("#boot").hidden = true; }, 700);
    if (ok) startApp(); else showGate();
  }, 520);
}
function extractToken(text) {
  text = (text || "").trim();
  const m = /[#?&/]s=([A-Za-z0-9._-]+)/.exec(text);
  if (m) return m[1];
  if (/^\d+\.\d+\.[A-Za-z0-9_-]+\.[a-f0-9]{16,}$/.test(text)) return text;
  return "";
}
function gateSay(text, calm) { const e = $("#gateErr"); e.hidden = false; e.classList.toggle("calm", !!calm); e.textContent = text; }
async function linkFromText(text) {
  const tok = extractToken(text);
  if (!tok) return gateSay("Не нашла ключ в этой ссылке. Скопируй её целиком.");
  sessionTok = tok; saveToken(tok);
  gateSay("Проверяю ключ…", true);
  try { await api("/api/overview"); } catch (_) { return gateSay("Ключ не подошёл или истёк. Запроси свежую ссылку."); }
  $("#gate").hidden = true; startApp();
}
function showGate() {
  if (authed) return;   // транзиентный сбой не роняет уже открытый город
  $("#app").hidden = true; $("#gate").hidden = false;
  const inp = $("#gateLink");
  $("#gateLinkBtn").onclick = () => linkFromText(inp.value);
  inp.onkeydown = e => { if (e.key === "Enter") linkFromText(inp.value); };
  $("#gatePaste").onclick = async () => {
    try { const t = await navigator.clipboard.readText(); inp.value = t; linkFromText(t); }
    catch (_) { gateSay("Буфер обмена недоступен. Вставь ссылку вручную."); }
  };
}

/* ------------------------------------------------------------------ небо и город */
const PAL = [ // час палитры, зенит, середина, горизонт, ночь
  [0,    [6, 10, 34],    [14, 20, 62],    [44, 34, 92],    1],
  [5,    [10, 14, 48],   [34, 32, 96],    [92, 58, 124],   .95],
  [6.5,  [34, 52, 130],  [176, 92, 140],  [255, 170, 120], .45],
  [8.2,  [62, 138, 214], [140, 196, 232], [252, 228, 190], .05],
  [13,   [44, 130, 214], [120, 190, 236], [222, 240, 236], 0],
  [17,   [58, 110, 196], [186, 170, 180], [250, 206, 150], .12],
  [18.7, [38, 30, 100],  [176, 58, 112],  [255, 150, 78],  .55],
  [20.6, [14, 18, 64],   [46, 36, 104],   [110, 48, 112],  .95],
  [24,   [6, 10, 34],    [14, 20, 62],    [44, 34, 92],    1],
];
const LEVELS = {
  host:   { day: [214, 76, 58],   night: [96, 34, 48],  neon: "#ff7a62", name: "без стен",      say: "Дом без стен: живёт почти как сам хост." },
  loose:  { day: [226, 164, 60],  night: [100, 72, 44], neon: "#ffc76a", name: "стены пониже",  say: "Стены пониже: ему разрешили лишнее." },
  normal: { day: [50, 92, 150],   night: [30, 40, 84],  neon: "#8dcbff", name: "обычный дом",   say: "Обычный дом: стены докера на месте." },
  tight:  { day: [58, 146, 114],  night: [22, 66, 64],  neon: "#86f2be", name: "глухие стены",  say: "Глухой дом: его файловая система только для чтения." },
};
const lvl = l => LEVELS[l] || LEVELS.normal;

function solar(d) {
  const start = new Date(d.getFullYear(), 0, 0), doy = (d - start) / 864e5;
  const half = 6 + 2.7 * Math.cos(2 * Math.PI * (doy - 172) / 365), noon = 12.3;
  return { rise: noon - half, set: noon + half };
}
function moonPhase(d) { return frac((d.getTime() / 864e5 - 10962.76) / 29.530588); }
function interp(pts, x) {
  for (let i = 0; i < pts.length - 1; i++) if (x <= pts[i + 1][0]) {
    const [a, b] = [pts[i], pts[i + 1]]; return lerp(a[1], b[1], (x - a[0]) / ((b[0] - a[0]) || 1));
  }
  return pts[pts.length - 1][1];
}

const City = (() => {
  const cv = $("#city"), ctx = cv.getContext("2d", { alpha: false });
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const off = () => document.createElement("canvas");
  const L = { sky: off(), far: off(), near: off(), win: off(), street: off() };
  const forced = new URLSearchParams(location.search).get("h");
  let W = 0, H = 0, DPR = 1, GY = 0;
  const STREET = 68;
  let near = [], ghosts = [], clouds = [], stars = [], cars = [], drops = [], sprites = [];
  let P = null, stamp = "", sig = "", dirty = true, running = false, heroOn = true, last = 0, lastWin = 0, lastPhase = 0;
  let intro = null, warp = null, flash = 0, bolt = null, nextBolt = 0, windX = 0;
  const live = { cpu: 0, mem: 30, wind: .1, rx: 0, tx: 0, write: 0 };
  let handlers = { house() {}, ruins() {} };
  let grain = null;

  function hoursNow() {
    if (warp) {
      const t = clamp((performance.now() - warp.t0) / warp.dur, 0, 1);
      if (t >= 1) { warp = null; dirty = true; }
      else return (warp.base + 24 * (t * t * (3 - 2 * t))) % 24;
    }
    if (forced != null && forced !== "") return clamp(+forced, 0, 23.99);
    const d = new Date(); return d.getHours() + d.getMinutes() / 60;
  }
  function phase() {
    const h = hoursNow(), d = new Date(), { rise, set } = solar(d);
    const pts = [[0, 0], [rise - 1.6, 5], [rise, 6.5], [rise + 1.4, 8.2], [set - 1.6, 17], [set, 18.7], [set + 1.6, 20.6], [24, 24]];
    const u = interp(pts, h);
    let i = 0; while (i < PAL.length - 2 && u >= PAL[i + 1][0]) i++;
    const a = PAL[i], b = PAL[i + 1], t = (u - a[0]) / (b[0] - a[0]);
    const m = Math.min(56, W * .13);
    const dayT = (h - rise) / (set - rise);
    let nh = h - set; if (nh < 0) nh += 24;
    const nightT = nh / (24 - (set - rise));
    const top = H * .17;
    const sun = { x: m + dayT * (W - 2 * m), y: GY - Math.sin(Math.PI * clamp(dayT, -.1, 1.1)) * (GY - top), up: dayT > -.05 && dayT < 1.05, t: dayT };
    const moon = { x: m + nightT * (W - 2 * m), y: GY - Math.sin(Math.PI * clamp(nightT, 0, 1)) * (GY - top - 30), up: !sun.up || nightT < .04 || nightT > .96 };
    const night = lerp(a[4], b[4], t);
    return { h, u, top: mix(a[1], b[1], t), mid: mix(a[2], b[2], t), low: mix(a[3], b[3], t), night, sun, moon,
      lightX: sun.up ? sun.x : moon.x, name: u < 5 || u >= 21 ? "ночь" : u < 11 ? "утро" : u < 17.5 ? "день" : "вечер" };
  }
  function prep(c) {
    c.width = W * DPR; c.height = H * DPR;
    const g = c.getContext("2d"); g.setTransform(DPR, 0, 0, DPR, 0, 0); g.clearRect(0, 0, W, H); return g;
  }
  function ensureGrain() {
    if (grain) return;
    const c = off(); c.width = c.height = 160; const g = c.getContext("2d");
    const im = g.createImageData(160, 160);
    for (let i = 0; i < im.data.length; i += 4) { const v = Math.random() * 255; im.data[i] = im.data[i + 1] = im.data[i + 2] = v; im.data[i + 3] = 11; }
    g.putImageData(im, 0, 0); grain = c;
  }

  /* ---- раскладка ---- */
  function district(name) {
    const m = /^([a-z0-9]+)[-_]/i.exec(name); return (m ? m[1] : name).toLowerCase();
  }
  function shortName(name, d) {
    const s = name.toLowerCase().startsWith(d + "-") ? name.slice(d.length + 1) : name;
    return s || name;
  }
  function pyramid(arr, key) {
    const s = [...arr].sort((a, b) => key(b) - key(a)), out = [];
    s.forEach((x, i) => i % 2 ? out.push(x) : out.unshift(x)); return out;
  }
  function layout(items) {
    const copy = $(".sky-copy");
    const copyH = copy ? copy.getBoundingClientRect().height : 280;
    GY = H - STREET;
    const crown = 74, maxH = clamp(GY - copyH - crown - 8, 110, H * .56), minH = 76;
    const run = items.filter(c => c.state === "running");
    const mems = run.map(c => num(c.stats && c.stats.mem_used, 1 << 20));
    const logMin = Math.log(1 << 20), logMax = Math.max(logMin + 1, Math.log(Math.max(1 << 21, ...mems)));
    const dist = {};
    run.forEach(c => {
      const d = district(c.name), used = num(c.stats && c.stats.mem_used, 1 << 20);
      const norm = clamp((Math.log(Math.max(used, 1 << 20)) - logMin) / (logMax - logMin), 0, 1);
      (dist[d] = dist[d] || []).push({ c, d, used, norm, name: c.name, short: shortName(c.name, d).toUpperCase(), level: c.level || "normal",
        cpu: num(c.stats && c.stats.cpu_pct), memPct: num(c.stats && c.stats.mem_pct), seed: hash32(c.name),
        tower: c.name === "praxis", ports: (c.ports || []).filter(p => p.public) });
    });
    const groups = pyramid(Object.keys(dist).map(k => ({ k, list: pyramid(dist[k], b => b.norm), sum: dist[k].reduce((a, b) => a + b.used, 0) })), g => g.sum);
    const flat = groups.flatMap(g => g.list);
    const gap = 9, left = 8, right = 50, avail = W - left - right - gap * Math.max(0, groups.length - 1);
    const sumW = flat.reduce((a, b) => a + 1 + .85 * b.norm, 0) || 1;
    const unit = avail / sumW;
    let totalW = 0;
    flat.forEach(b => { b.w = Math.round(clamp((1 + .85 * b.norm) * unit, 15, 110)); totalW += b.w; });
    let x = left + Math.max(0, (avail - totalW) / 2);
    near = [];
    groups.forEach((g, gi) => {
      g.x0 = x;
      g.list.forEach(b => {
        b.x = Math.round(x); b.h = Math.round(minH + (maxH - minH) * Math.pow(b.norm, 1.25)); b.i = near.length;
        b.win = windowsOf(b); near.push(b); x += b.w;
      });
      g.x1 = x; x += gap;
    });
    layout.groups = groups;
    const dead = items.filter(c => c.state !== "running");
    ghosts = dead.map((c, i) => {
      const seed = hash32(c.name), row = i % 2;
      return { c, seed, row, x: frac(i * .61803 + rnd(seed, 1) * .1) * (W + 40) - 20, w: 14 + rnd(seed, 2) * 26,
        h: (row ? 40 : 70) + rnd(seed, 3) * (row ? 96 : 130), ruin: rnd(seed, 4) < .45 };
    }).sort((a, b) => a.row - b.row);
    const n = Math.round(1 + live.mem / 100 * 8);
    clouds = Array.from({ length: n }, (_, i) => {
      const s = (i + 1) * 7919;
      return { x: rnd(s, 1) * (W + 240) - 120, y: H * (.05 + rnd(s, 2) * .4), s: .45 + rnd(s, 3) * .75, k: i % sprites.length || 0, v: .5 + rnd(s, 4) };
    });
    if (!stars.length) stars = Array.from({ length: 170 }, (_, i) => ({ x: rnd(i + 3, 1), y: rnd(i + 3, 2) * .78, r: rnd(i + 3, 3) < .12 ? 1.5 : .85, p: rnd(i + 3, 4) * 6.28 }));
  }
  function windowsOf(b) {
    const out = [], y0 = GY - b.h, sign = b.w >= 19 ? 11 : 0;
    const L0 = b.x + (sign ? sign + 2 : 3), R0 = b.x + b.w - 3;
    const cw = clamp(Math.round(b.w * .17), 3, 6), gx = Math.max(2, Math.round(cw * .75));
    const cols = Math.max(1, Math.floor((R0 - L0 + gx) / (cw + gx)));
    const sx = L0 + ((R0 - L0) - (cols * cw + (cols - 1) * gx)) / 2;
    const ch = Math.round(cw * 1.55), gy = Math.max(3, Math.round(ch * .7));
    let row = 0;
    for (let y = y0 + 8; y + ch < GY - 15; y += ch + gy, row++)
      for (let c = 0; c < cols; c++) out.push({ x: Math.round(sx + c * (cw + gx)), y, w: cw, h: ch, s: rnd(b.seed + row * 31, c + 1) });
    return out;
  }

  /* ---- статичные слои ---- */
  function renderSky() {
    const g = prep(L.sky), gr = g.createLinearGradient(0, 0, 0, GY);
    gr.addColorStop(0, rgb(P.top)); gr.addColorStop(.58, rgb(P.mid)); gr.addColorStop(1, rgb(P.low));
    g.fillStyle = gr; g.fillRect(0, 0, W, H);
    ensureGrain();
    g.fillStyle = g.createPattern(grain, "repeat"); g.fillRect(0, 0, W, H);
  }
  function makeSprites() {
    const lit = mix([255, 252, 244], mix(mix(P.low, P.mid, .55), [70, 62, 122], .56), clamp(P.night * 2.1, 0, 1));
    const under = mix(lit, P.mid, .5);
    sprites = [0, 1, 2, 3].map(v => {
      const c = off(), w = 240, h = 86; c.width = w * 2; c.height = h * 2;
      const g = c.getContext("2d"); g.scale(2, 2);
      const blobs = Array.from({ length: 7 }, (_, i) => ({ x: 34 + i * 28 + rnd(v * 9 + 1, i) * 12, r: 13 + Math.sin(i / 6 * Math.PI) * 15 + rnd(v * 7 + 2, i) * 9 }));
      const base = h - 12;
      const draw = (col, dy) => { g.fillStyle = col; g.beginPath(); blobs.forEach(b => { g.moveTo(b.x + b.r, base - b.r * .55 + dy); g.arc(b.x, base - b.r * .55 + dy, b.r, 0, 7); }); g.fill(); g.fillRect(24, base - 15 + dy, 192, 15); };
      draw(rgb(under), 3); draw(rgb(lit), -2);
      return c;
    });
    clouds.forEach((c, i) => c.k = i % sprites.length);
  }
  function renderFar() {
    const g = prep(L.far), ink = mix(P.mid, [8, 8, 26], .45);
    const A = mix(P.low, ink, .42), B = mix(P.low, ink, .68);
    ghosts.forEach(b => {
      const col = b.row ? B : A, y = GY - b.h, x = b.x, w = b.w;
      g.fillStyle = rgb(col);
      g.beginPath(); g.moveTo(x, GY); g.lineTo(x, y);
      if (b.ruin) { let cx = x; const steps = 3 + (b.seed % 3); for (let i = 0; i < steps; i++) { cx += w / steps; g.lineTo(cx - w / steps * .5, y + rnd(b.seed, i + 8) * 16); g.lineTo(cx, y + rnd(b.seed, i + 20) * 9); } }
      else g.lineTo(x + w, y);
      g.lineTo(x + w, GY); g.closePath(); g.fill();
      if (b.row) {
        g.fillStyle = rgb(mix(col, P.night > .5 ? [150, 140, 190] : [20, 20, 50], .26));
        for (let wy = y + 18; wy < GY - 10; wy += 10) for (let wx = x + 4; wx < x + w - 5; wx += 7) if (rnd(b.seed + wy, wx) < .55) g.fillRect(wx, wy, 3, 5);
      }
    });
  }
  function paintBuilding(g, b) {
    const { x, w, h } = b, y = GY - h, L0 = lvl(b.level);
    const v = .93 + rnd(b.seed, 5) * .14;
    const base = mix(L0.day, L0.night, P.night).map(c => clamp(c * v, 0, 255));
    const hi = mix(base, [255, 244, 224], .2 * (1 - P.night) + .05), lo = mix(base, [10, 10, 34], .2);
    const lg = g.createLinearGradient(0, y, 0, GY); lg.addColorStop(0, rgb(hi)); lg.addColorStop(1, rgb(lo));

    const cx = x + w / 2;
    // крыша
    g.fillStyle = rgb(mix(base, [14, 12, 36], .3));
    const kind = b.tower ? 9 : b.seed % 5;
    if (b.tower) {
      g.fillStyle = rgb(hi); g.fillRect(x + w * .14, y - 15, w * .72, 16);
      g.fillStyle = rgb(mix(hi, lo, .4)); g.fillRect(x + w * .3, y - 30, w * .4, 16);
      g.fillStyle = rgb(lo); g.fillRect(cx - 1.5, y - 72, 3, 44);
      g.fillStyle = rgb(hi); g.beginPath(); g.moveTo(cx - 5, y - 30); g.lineTo(cx, y - 44); g.lineTo(cx + 5, y - 30); g.fill();
    } else if (kind === 0) { g.fillRect(cx - 1, y - 21, 2, 22); g.fillRect(cx - 6, y - 15, 12, 1.5); }
    else if (kind === 1) { g.fillRect(x + w * .18, y - 12, 2, 12); g.fillRect(x + w * .18 + 9, y - 12, 2, 12); g.fillRect(x + w * .18 - 3, y - 22, 19, 11); }
    else if (kind === 2) { g.fillRect(x + w * .2, y - 7, w * .6, 8); }
    else if (kind === 3) { g.beginPath(); g.moveTo(x + 1, y); g.lineTo(cx, y - Math.min(20, w * .55)); g.lineTo(x + w - 1, y); g.fill(); }
    else { g.fillRect(x + 3, y - 5, 8, 6); g.fillRect(x + w - 11, y - 8, 7, 9); }

    g.fillStyle = lg; g.fillRect(x, y, w, h);
    // тень от светила
    const sunLeft = P.lightX < cx, sw = Math.max(3, w * .22);
    g.fillStyle = rgb([8, 8, 30], .28 + .1 * (1 - P.night));
    g.fillRect(sunLeft ? x + w - sw : x, y, sw, h);
    g.fillStyle = rgb(mix(base, [255, 255, 255], .34)); g.fillRect(x - 1, y, w + 2, 1.5);

    // окна-заготовки
    const glass = mix(mix(P.mid, [20, 34, 70], .4), [10, 12, 34], P.night * .88);
    g.fillStyle = rgb(glass, .92);
    b.win.forEach(q => g.fillRect(q.x, q.y, q.w, q.h));

    // вертикальная вывеска
    if (b.w >= 19) {
      const text = b.short, maxL = h - 22, fs = 8.5, adv = fs * .62;
      const n = Math.min(text.length, Math.floor(maxL / adv));
      const shown = n < text.length ? text.slice(0, Math.max(1, n - 1)) + "…" : text;
      const sh = shown.length * adv + 8;
      g.fillStyle = rgb(mix([8, 8, 26], base, P.night > .4 ? .05 : .28), .92);
      g.fillRect(x + 2, y + 6, 10, sh);
      g.save(); g.translate(x + 7, y + 6 + sh - 4); g.rotate(-Math.PI / 2);
      g.font = `700 ${fs}px "JetBrains Mono",monospace`; g.textBaseline = "middle";
      if (P.night > .35) { g.shadowColor = L0.neon; g.shadowBlur = 7; }
      g.fillStyle = P.night > .35 ? L0.neon : "#fff6e4"; g.fillText(shown, 0, .5);
      g.restore(); g.shadowBlur = 0;
    }
    // дверь на улицу, если наружу опубликован порт
    if (b.ports.length) {
      const pub = b.ports.some(p => (p.ip || "").startsWith("0.") || p.ip === "::");
      const dw = Math.min(10, w * .34), dx = cx - dw / 2 + (b.w >= 19 ? 4 : 0);
      g.fillStyle = pub ? "#ffcf7a" : rgb(mix(L0.neon === "#000" ? base : [160, 200, 240], base, .35));
      g.fillRect(dx, GY - 13, dw, 13);
      g.fillStyle = rgb(mix(base, [8, 8, 26], .5)); g.fillRect(dx - 2, GY - 15, dw + 4, 2.5);
    }
  }
  function renderNear() { const g = prep(L.near); near.forEach(b => paintBuilding(g, b)); }
  function renderWin(t) {
    const g = prep(L.win);
    if (P.night < .12) return;
    const a = clamp((P.night - .12) / .4, 0, 1);
    near.forEach(b => {
      const p = clamp(.14 + .55 * Math.sqrt(b.cpu / 100) + .22 * b.norm + .16 * P.night, 0, .92);
      b.win.forEach(q => {
        const slot = Math.floor(t / 1000 / (9 + q.s * 26));
        const on = frac(q.s * 997 + slot * .137) < p;
        if (!on) return;
        const warm = q.s * 7 % 1 < .82;
        g.shadowColor = warm ? "rgba(255,190,100,.85)" : "rgba(160,210,255,.8)"; g.shadowBlur = 6;
        g.fillStyle = warm ? `rgba(255,214,130,${a})` : `rgba(190,226,255,${a})`;
        g.fillRect(q.x, q.y, q.w, q.h);
      });
    });
    g.shadowBlur = 0;
  }
  function renderStreet() {
    const g = prep(L.street), n = P.night, y = GY;
    const walk = mix([176, 164, 150], [44, 42, 68], n), curb = mix(walk, [255, 255, 255], .18);
    const road = mix([62, 62, 76], [20, 19, 36], n);
    g.fillStyle = rgb(walk); g.fillRect(0, y, W, 9);
    g.fillStyle = rgb(curb); g.fillRect(0, y + 9, W, 2);
    g.fillStyle = rgb(road); g.fillRect(0, y + 11, W, H - y - 11);
    g.fillStyle = rgb(mix(road, [255, 240, 200], .55));
    for (let x = -10; x < W; x += 34) g.fillRect(x, y + 35, 18, 2);
    if (grain) { g.globalAlpha = .55; g.fillStyle = g.createPattern(grain, "repeat"); g.fillRect(0, y, W, H - y); g.globalAlpha = 1; }
    // фонари
    for (let x = 34; x < W - 40; x += 92) {
      g.fillStyle = rgb(mix([66, 62, 76], [30, 28, 50], n)); g.fillRect(x, y - 26, 2, 27);
      g.fillRect(x, y - 27, 11, 2);
      if (n > .3) {
        const lamp = g.createRadialGradient(x + 11, y - 24, 1, x + 11, y - 24, 24);
        lamp.addColorStop(0, `rgba(255,214,140,${.5 * n})`); lamp.addColorStop(1, "rgba(255,214,140,0)");
        g.fillStyle = lamp; g.fillRect(x - 14, y - 49, 50, 50);
        g.fillStyle = "#ffe6b0"; g.fillRect(x + 8, y - 25, 5, 2.5);
      }
    }
    // таблички кварталов на тротуаре
    g.font = '600 8.5px "JetBrains Mono",monospace'; g.textBaseline = "middle"; g.textAlign = "center";
    g.fillStyle = n > .34 ? "#ece6f4" : "#2a2230";
    (layout.groups || []).forEach(gr => { if (gr.x1 - gr.x0 > 36) g.fillText(gr.k.toUpperCase(), (gr.x0 + gr.x1) / 2, y + 5); });
    g.textAlign = "left";
  }
  function rebuild(t) {
    P = phase();
    GY = H - STREET;
    renderSky(); makeSprites(); renderFar(); renderNear(); renderWin(t); renderStreet();
    stamp = Math.round(P.u * 6) + ":" + (P.sun.up ? 1 : 0);
    dirty = false; lastWin = t; lastPhase = t;
    Sky.apply(P, !!warp);
  }
  function resize() {
    const r = cv.getBoundingClientRect(); if (!r.width || !r.height) return;
    DPR = Math.min(2, window.devicePixelRatio || 1, Math.sqrt(5e6 / (r.width * r.height)));
    if (r.width === W && r.height === H) return;
    W = Math.round(r.width); H = Math.round(r.height);
    cv.width = W * DPR; cv.height = H * DPR;
    if (items_) { P = P || phase(); layout(items_); }
    dirty = true;
  }

  /* ---- кадр ---- */
  function drawSunMoon(t) {
    if (P.sun.up) {
      const s = P.sun, lowT = clamp(1 - (GY - s.y) / (GY * .42), 0, 1);
      const col = mix([255, 244, 204], [255, 128, 76], lowT), R = lerp(26, 38, lowT);
      const gl = ctx.createRadialGradient(s.x, s.y, R * .4, s.x, s.y, R * 5.5);
      gl.addColorStop(0, rgb(col, .55)); gl.addColorStop(1, rgb(col, 0));
      ctx.fillStyle = gl; ctx.fillRect(s.x - R * 6, s.y - R * 6, R * 12, R * 12);
      ctx.fillStyle = rgb(col); ctx.beginPath(); ctx.arc(s.x, s.y, R, 0, 7); ctx.fill();
    }
    if (P.moon.up && P.night > .2) {
      const m = P.moon, R = 20, ph = moonPhase(new Date()), a = clamp((P.night - .2) / .3, 0, 1);
      const k = Math.cos(2 * Math.PI * ph), wax = ph < .5, lit = (1 - k) / 2;
      const gl = ctx.createRadialGradient(m.x, m.y, R, m.x, m.y, R * 5);
      gl.addColorStop(0, `rgba(226,226,255,${(.06 + .24 * lit) * a})`); gl.addColorStop(1, "rgba(226,226,255,0)");
      ctx.fillStyle = gl; ctx.fillRect(m.x - R * 6, m.y - R * 6, R * 12, R * 12);
      ctx.fillStyle = rgb(mix(P.top, [150, 160, 205], .17), a); ctx.beginPath(); ctx.arc(m.x, m.y, R, 0, 7); ctx.fill();
      ctx.strokeStyle = `rgba(214,218,250,${.3 * a})`; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = `rgba(244,240,222,${a})`; ctx.beginPath();
      ctx.arc(m.x, m.y, R, -Math.PI / 2, Math.PI / 2, !wax);
      ctx.ellipse(m.x, m.y, Math.abs(k) * R, R, 0, Math.PI / 2, -Math.PI / 2, wax ? k > 0 : k < 0);
      ctx.fill();
    }
  }
  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    if (now - last < 32) return;
    const dt = Math.min(.1, (now - last) / 1000); last = now;
    draw(now, dt);
  }
  function draw(now, dt) {
    if (!W) { resize(); if (!W) return; }
    if (dirty || warp && now - lastPhase > 90) rebuild(now);
    else if (now - lastPhase > 30000) {
      const p = phase(), s = Math.round(p.u * 6) + ":" + (p.sun.up ? 1 : 0);
      lastPhase = now;
      if (s !== stamp) rebuild(now);
    }
    if (now - lastWin > 1700) { renderWin(now); lastWin = now; }
    const w = world.scrollTop || 0;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.drawImage(L.sky, 0, 0, W, H);
    // звёзды
    if (P.night > .25) {
      const a = clamp((P.night - .25) / .5, 0, 1);
      ctx.fillStyle = "#fff";
      stars.forEach(s => {
        ctx.globalAlpha = a * (.42 + .4 * Math.sin(now / 1100 + s.p));
        ctx.fillRect(s.x * W, s.y * GY, s.r, s.r);
      });
      ctx.globalAlpha = 1;
    }
    drawSunMoon(now);
    // облака
    windX += dt * (4 + 42 * live.wind);
    clouds.forEach(c => {
      const span = W + 300, x = ((c.x + windX * c.v * (.5 + c.s)) % span + span) % span - 150;
      ctx.globalAlpha = .94;
      ctx.drawImage(sprites[c.k], x, c.y, 240 * c.s, 86 * c.s);
    });
    ctx.globalAlpha = 1;
    // жара у горизонта
    const heat = clamp((live.cpu - 40) / 55, 0, 1);
    if (heat > 0) {
      const g = ctx.createLinearGradient(0, GY - H * .5, 0, GY);
      g.addColorStop(0, "rgba(255,120,50,0)"); g.addColorStop(1, `rgba(255,120,50,${.5 * heat})`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, GY);
    }
    ctx.drawImage(L.far, 0, w * .12, W, H);
    // город
    if (intro) {
      const t = now - intro; let done = true;
      ctx.save(); ctx.beginPath(); ctx.rect(0, 0, W, GY); ctx.clip();
      near.forEach(b => {
        const c = Math.abs(b.i - near.length / 2) * 42;
        const k = clamp((t - c) / 720, 0, 1), e = 1 - Math.pow(1 - k, 3);
        if (k < 1) done = false;
        const pad = 12, sx = Math.max(0, b.x - pad);
        ctx.drawImage(L.near, sx * DPR, 0, (b.w + pad * 2) * DPR, GY * DPR, sx, (1 - e) * (b.h + 80), b.w + pad * 2, GY);
      });
      ctx.restore();
      ctx.globalAlpha = clamp((t - 520) / 620, 0, 1); ctx.drawImage(L.win, 0, 0, W, H); ctx.globalAlpha = 1;
      if (done && t > 1250) intro = null;
    } else {
      ctx.drawImage(L.near, 0, 0, W, H);
      ctx.drawImage(L.win, 0, 0, W, H);
    }
    // маяки
    near.forEach(b => {
      if (!(b.tower || b.level === "host")) return;
      const y = GY - b.h, cx = b.x + b.w / 2, ty = b.tower ? y - 72 : y - 4;
      const beat = b.tower ? (.5 + .5 * Math.sin(now / (900 - Math.min(600, b.cpu * 12)))) : (Math.sin(now / 520 + b.seed) > .35 ? 1 : .14);
      const col = b.tower ? "255,210,120" : "255,70,56", R = b.tower ? 14 + beat * 18 : 7;
      const gl = ctx.createRadialGradient(cx, ty, 0, cx, ty, R);
      gl.addColorStop(0, `rgba(${col},${.85 * beat})`); gl.addColorStop(1, `rgba(${col},0)`);
      ctx.fillStyle = gl; ctx.fillRect(cx - R, ty - R, R * 2, R * 2);
      ctx.fillStyle = `rgba(${col},1)`; ctx.fillRect(cx - 1.5, ty - 1.5, 3, 3);
    });
    ctx.drawImage(L.street, 0, 0, W, H);
    drawCars(now, dt);
    drawRain(dt);
    drawStorm(now, dt);
  }
  function carCount(v) { return Math.round(2 + 13 * clamp((Math.log10(v + 1) - 2.5) / 4, 0, 1)); }
  function drawCars(now, dt) {
    const want = [carCount(live.rx), carCount(live.tx)];
    [0, 1].forEach(lane => {
      const have = cars.filter(c => c.lane === lane);
      for (let i = have.length; i < want[lane]; i++) cars.push({ lane, x: Math.random() * W, v: 42 + Math.random() * 54, len: 11 + Math.random() * 7, col: CAR[Math.random() * CAR.length | 0] });
      for (let i = have.length; i > want[lane]; i--) cars.splice(cars.indexOf(have[i - 1]), 1);
    });
    const n = P.night;
    cars.forEach(c => {
      const dir = c.lane ? 1 : -1, y = GY + (c.lane ? 42 : 18);
      c.x += dir * c.v * dt;
      if (c.x > W + 30) c.x = -30; if (c.x < -30) c.x = W + 30;
      ctx.fillStyle = rgb(mix(c.col, [16, 16, 40], n * .62)); ctx.fillRect(c.x, y, c.len, 6);
      ctx.fillRect(c.x + c.len * .2, y - 3, c.len * .55, 3.5);
      if (n > .3) {
        const fx = dir > 0 ? c.x + c.len : c.x;
        const g = ctx.createLinearGradient(fx, y, fx + dir * 26, y);
        g.addColorStop(0, `rgba(255,236,180,${.72 * n})`); g.addColorStop(1, "rgba(255,236,180,0)");
        ctx.fillStyle = g; ctx.fillRect(dir > 0 ? fx : fx - 26, y + 1, 26, 4);
        ctx.fillStyle = "rgba(255,70,60,.9)"; ctx.fillRect(dir > 0 ? c.x - 1 : c.x + c.len, y + 1, 1.5, 3);
      }
    });
  }
  const CAR = [[222, 92, 72], [240, 190, 90], [90, 150, 210], [232, 226, 210], [86, 178, 150]];
  function rainWanted() { const k = clamp((Math.log10(live.write + 1) - 5.85) / 2, 0, 1); return k > 0 ? Math.round(26 + 170 * k) : 0; }
  function drawRain(dt) {
    const want = rainWanted();
    while (drops.length < want) drops.push({ x: Math.random() * W, y: Math.random() * GY, v: 460 + Math.random() * 260, l: 7 + Math.random() * 9 });
    drops.length = Math.min(drops.length, want);
    if (!drops.length) return;
    ctx.strokeStyle = rgb(mix([255, 255, 255], [190, 206, 255], P.night), .46); ctx.lineWidth = 1; ctx.beginPath();
    drops.forEach(d => {
      d.y += d.v * dt; d.x -= 34 * dt * live.wind * 3;
      if (d.y > GY + 26) { d.y = -d.l; d.x = Math.random() * W; }
      if (d.x < 0) d.x += W;
      ctx.moveTo(d.x, d.y); ctx.lineTo(d.x + 1.2, d.y + d.l);
    });
    ctx.stroke();
  }
  function drawStorm(now, dt) {
    if (live.cpu >= 90 && now > nextBolt && !reduced) {
      nextBolt = now + 1500 + Math.random() * 3800; flash = 1;
      let x = W * (.15 + Math.random() * .7), y = H * .1; const pts = [[x, y]];
      while (y < GY - 60) { y += 24 + Math.random() * 34; x += (Math.random() - .5) * 46; pts.push([x, y]); }
      bolt = pts;
    }
    if (flash <= 0) return;
    ctx.fillStyle = `rgba(232,232,255,${flash * .28})`; ctx.fillRect(0, 0, W, GY);
    if (bolt && flash > .4) {
      ctx.strokeStyle = `rgba(255,252,236,${flash})`; ctx.lineWidth = 2.2; ctx.beginPath();
      bolt.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
    }
    flash -= dt * 3.2;
  }

  /* ---- управление ---- */
  let items_ = null;
  function setContainers(items) {
    items_ = items;
    const s = items.map(c => c.name + c.state + (c.level || "") + Math.round(Math.log2(num(c.stats && c.stats.mem_used, 1)))).join("|");
    if (s === sig) {   // город тот же, просто пересчитаем активность окон
      const by = Object.fromEntries(items.map(c => [c.name, c]));
      near.forEach(b => { const c = by[b.name]; if (c) { b.c = c; b.cpu = num(c.stats && c.stats.cpu_pct); } });
      return;
    }
    const first = !sig; sig = s;
    resize(); if (!W) return;
    P = phase(); layout(items); dirty = true;
    if (first && !reduced) intro = performance.now();
    wake();
  }
  function setLive(v) { const was = live.mem; Object.assign(live, v); if (Math.round(1 + was / 100 * 8) !== Math.round(1 + live.mem / 100 * 8) && items_ && W) { layout(items_); dirty = true; } wake(); }
  function wake() {
    if (reduced) { requestAnimationFrame(t => draw(t, .03)); return; }
    if (running || !heroOn || document.hidden) return;
    running = true; last = 0; requestAnimationFrame(frame);
  }
  function pause() { running = false; }
  addEventListener("resize", () => { resize(); wake(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); else wake(); });
  new IntersectionObserver(es => { heroOn = es[0].isIntersecting; if (heroOn) wake(); else pause(); }, { threshold: .01 }).observe($("#f-sky"));
  const world = $("#world");

  let downAt = null;
  cv.addEventListener("pointerdown", e => { downAt = [e.clientX, e.clientY]; });
  cv.addEventListener("pointerup", e => {
    if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 12 || !P) return;
    const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    const body = P.sun.up ? P.sun : P.moon;
    if (Math.hypot(x - body.x, y - body.y) < 44) {
      warp = { t0: performance.now(), dur: 9000, base: hoursNow() };
      haptic("impact"); return;
    }
    if (y <= GY) {
      const b = near.find(q => x >= q.x - 2 && x <= q.x + q.w + 2 && y >= GY - q.h - (q.tower ? 72 : 24));
      if (b) { haptic("impact"); handlers.house(b.c); return; }
      if (ghosts.some(q => x >= q.x && x <= q.x + q.w && y >= GY - q.h)) handlers.ruins();
    }
  });

  return {
    setContainers, setLive, wake, invalidate() { dirty = true; wake(); },
    on(h) { Object.assign(handlers, h); }, name() { return (P || phase()).name; },
    ready() { if (!W) resize(); },
  };
})();

const Sky = {
  last: "",
  apply(P, quick) {
    const dark = P.night > .3, r = document.documentElement.style;
    r.setProperty("--sky-ink", dark ? "#f6edda" : "#10213f");
    r.setProperty("--sky-ink2", dark ? "rgba(246,237,218,.82)" : "rgba(16,33,63,.78)");
    r.setProperty("--sky-halo", dark ? "rgba(12,10,40,.6)" : "rgba(255,255,255,.55)");
    const c = hex(P.top);
    if (quick || c === this.last) return; this.last = c;
    const m = $('meta[name="theme-color"]'); if (m) m.content = c;
    if (TG) { try { TG.setHeaderColor && TG.setHeaderColor(c); TG.setBackgroundColor && TG.setBackgroundColor(c); } catch (_) {} }
  },
};
function haptic(kind) {
  if (!TG || !TG.HapticFeedback) return;
  try { kind === "impact" ? TG.HapticFeedback.impactOccurred("light") : TG.HapticFeedback.selectionChanged(); } catch (_) {}
}

/* ------------------------------------------------------------------ данные */
const S = { live: null, overview: null, containers: null, liveFails: 0 };
const world = $("#world");

function startApp() {
  if (authed) return;
  authed = true;
  $("#gate").hidden = true; $("#app").hidden = false;
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  document.fonts && document.fonts.load('700 9px "JetBrains Mono"', "ATLANTA").then(() => City.invalidate()).catch(() => {});
  initFloors();
  City.ready();
  City.on({ house: openHouse, ruins: () => { goFloor("houses"); needFloor("houses"); setTimeout(() => { const d = $(".ruins"); if (d) d.open = true; }, 500); } });
  pollLive(); pollOverview(); pollContainers();
  setInterval(pollLive, 2000);
  setInterval(pollOverview, 8000);
  setInterval(pollContainers, 15000);
  setInterval(refreshVisible, 4000);
  setInterval(ensureSession, 6 * 3600 * 1000);
  setInterval(paintForecast, 30000);
}
async function pollLive() {
  let j;
  try { j = await api("/api/live"); }
  catch (e) { if (e.forbidden) return showGate(); S.liveFails++; if (S.liveFails >= 3) $("#lost").hidden = false; return; }
  S.liveFails = 0; $("#lost").hidden = true;
  S.live = j;
  const cores = (j.cpu && j.cpu.cores || []).length || 1;
  City.setLive({ cpu: num(j.cpu && j.cpu.total), mem: num(j.mem && j.mem.used_pct), wind: clamp(num(j.load && j.load.m1) / cores, 0, 1.5), rx: num(j.net && j.net.rx), tx: num(j.net && j.net.tx), write: num(j.disk && j.disk.write) });
  paintForecast();
  paintAtlantes(); paintFlow();
}
async function pollOverview() { try { S.overview = await api("/api/overview"); paintForecast(); } catch (_) {} }
async function pollContainers() {
  try {
    const j = await api("/api/containers"); S.containers = j.items || []; S.containersErr = null;
    City.setContainers(S.containers); paintForecast();
    if (loaded.houses) renderHouses();
  } catch (e) {
    if (e.forbidden) return showGate();
    S.containersErr = e;
  }
}

function paintForecast() {
  const j = S.live, o = S.overview, now = new Date();
  const date = now.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" });
  const time = now.toTimeString().slice(0, 5);
  $("#dl").textContent = `${date} · ${time}` + (o && o.host ? ` · ${o.host.hostname}` : "");
  if (!j) return;
  const cpu = num(j.cpu && j.cpu.total), mem = j.mem || {}, ld = num(j.load && j.load.m1), cores = (j.cpu && j.cpu.cores || []).length || 1;
  const w = num(j.disk && j.disk.write), rx = num(j.net && j.net.rx), tx = num(j.net && j.net.tx);
  $("#tempV").textContent = Math.round(cpu);
  const ph = City.name();
  const calm = { ночь: "Тихая ночь", утро: "Спокойное утро", день: "Ясный день", вечер: "Тихий вечер" }[ph];
  $("#headline").textContent = cpu >= 90 ? "Гроза над городом" : cpu >= 65 ? "Жарко на улицах" : cpu >= 30 ? "Город работает" : calm;
  let sub = "";
  if (S.containers) {
    const run = S.containers.filter(c => c.state === "running"), all = S.containers.length;
    sub = `Горит ${count(run.length, "дом", "дома", "домов")} из ${all}.`;
    const p = run.find(c => c.name === "praxis");
    if (p && p.stats) sub += ` Башня Праксис занимает ${bytes(p.stats.mem_used)}.`;
  }
  $("#subline").textContent = sub || NB;
  $("#aMem").textContent = `${fmt(mem.used_pct)}${NB}%`;
  $("#aMemS").textContent = `память ${bytes(mem.used)} из ${bytes(mem.total)}`;
  $("#aLoad").textContent = fmt(ld, 2);
  $("#aLoadS").textContent = `нагрузка на ${count(cores, "ядро", "ядра", "ядер")}`;
  $("#aRain").textContent = w < 1024 ? "сухо" : rate(w);
  const [mx, i] = scale(Math.max(rx, tx)); void mx;
  const d = Math.pow(1024, i), val = v => fmt(v / d, v / d < 10 && i ? 1 : 0);
  $("#aNet").textContent = `↓${val(rx)} ↑${val(tx)}`;
  $("#aNet").nextElementSibling.textContent = `${UNITS[i]}/с · сеть хоста`;
}

/* ------------------------------------------------------------------ этажи и лифт */
const FLOORS = [
  ["sky", "↑", "Небо"], ["houses", "0", "Дома"], ["people", "−1", "Жители"], ["gates", "−2", "Ворота"],
  ["metro", "−3", "Метро"], ["water", "−4", "Водохранилище"], ["rock", "−5", "Фундамент"], ["keys", "−6", "Ключи"],
];
const loaded = {}, floorAt = {}, visible = new Set(), busy = {};
const REFRESH = { houses: 15000, people: 8000, gates: 30000, metro: 30000, water: 20000, rock: 60000, keys: 30000 };
let RENDER = {};

function edgeSvg(seed) {
  let x = 0, k = 0, d = `M0 30L0 ${(12 + rnd(seed, 0) * 14).toFixed(1)}`;
  while (x < 1000) { x += 16 + rnd(seed, ++k) * 48; d += `L${Math.min(1000, x).toFixed(0)} ${(2 + rnd(seed, k + 50) * 25).toFixed(1)}`; }
  return `<svg class="edge" viewBox="0 0 1000 30" preserveAspectRatio="none" aria-hidden="true"><path d="${d}L1000 30Z"/></svg>`;
}
let activeFloor = "sky", sayTimer = 0;
function initFloors() {
  RENDER = { houses: renderHouses, people: renderPeople, gates: renderGates, metro: renderMetro, water: renderWater, rock: renderRock, keys: renderKeys };
  $$(".floor").forEach((f, i) => { if (f.dataset.floor !== "sky") f.insertAdjacentHTML("afterbegin", edgeSvg(i * 131 + 17)); });
  const lift = $("#lift");
  FLOORS.forEach(([id, n, name]) => {
    const b = document.createElement("button");
    b.className = "fl"; b.textContent = n; b.dataset.id = id; b.dataset.name = name;
    b.setAttribute("aria-label", name);
    b.onclick = () => { haptic(); goFloor(id); };
    lift.appendChild(b);
  });
  const io = new IntersectionObserver(es => es.forEach(e => {
    const id = e.target.dataset.floor;
    if (e.isIntersecting) { visible.add(id); needFloor(id); } else visible.delete(id);
  }), { root: world, rootMargin: "320px 0px" });
  $$(".floor").forEach(f => io.observe(f));
  ["touchstart", "wheel"].forEach(ev => world.addEventListener(ev, () => { aim = null; }, { passive: true }));
  if (window.ResizeObserver) { const ro = new ResizeObserver(holdAim); $$(".fb").forEach(b => ro.observe(b)); }
  setTimeout(() => Object.keys(RENDER).forEach(id => needFloor(id)), 1100);   // этажи заранее: лифт едет без ожидания
  let tick = false;
  world.addEventListener("scroll", () => { if (tick) return; tick = true; requestAnimationFrame(() => { tick = false; syncLift(); }); }, { passive: true });
  syncLift(true);
  addEventListener("resize", () => syncLift(true));
}
function floorEl(id) { return $(`.floor[data-floor="${id}"]`); }
let aim = null;
const floorTop = id => floorEl(id).offsetTop - (id === "sky" ? 0 : 6);
function goFloor(id) {
  if (!floorEl(id)) return;
  aim = { id, top: floorTop(id), until: Date.now() + 2600 };
  world.scrollTo({ top: aim.top, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
function holdAim() {
  if (!aim) return;
  if (Date.now() > aim.until) { aim = null; return; }
  const top = floorTop(aim.id);
  if (Math.abs(top - aim.top) > 4) { aim.top = top; world.scrollTo({ top, behavior: "auto" }); }
}
function syncLift(force) {
  const y = world.scrollTop + world.clientHeight * .42;
  let cur = "sky";
  FLOORS.forEach(([id]) => { const el = floorEl(id); if (el && el.offsetTop <= y) cur = id; });
  if (cur === activeFloor && !force) return;
  const changed = cur !== activeFloor; activeFloor = cur;
  $$(".fl").forEach(b => { const on = b.dataset.id === cur; b.classList.toggle("on", on); if (on) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current"); });
  const btn = $(`.fl[data-id="${cur}"]`);
  if (btn) $("#cab").style.transform = `translateY(${btn.offsetTop - 4}px)`;
  if (changed && btn) {
    $$(".fl.say").forEach(b => b.classList.remove("say"));
    btn.classList.add("say"); clearTimeout(sayTimer); sayTimer = setTimeout(() => btn.classList.remove("say"), 1300);
  }
}
async function needFloor(id, force) {
  const fn = RENDER[id]; if (!fn || busy[id]) return;
  if (!force && floorAt[id] && Date.now() - floorAt[id] < (REFRESH[id] || 20000)) return;
  busy[id] = true;
  try { await fn(); } finally { busy[id] = false; floorAt[id] = Date.now(); loaded[id] = true; }
}
function refreshVisible() { if ($("#sheet").hidden) visible.forEach(id => needFloor(id)); }
function oops(box, title, e, retry) {
  if (e && e.forbidden) return showGate();
  if (box.dataset.ok) return;    // уже что-то показано: тихо оставляем последнее
  box.innerHTML = `<div class="oops"><b>${esc(title)}</b>Сейчас не получилось, попробую ещё раз сама.<code>${esc(e && e.message || "")}</code><button class="retry">Повторить сейчас</button></div>`;
  box.querySelector(".retry").onclick = retry;
}

/* ---- 0: дома ---- */
function imageName(s) {
  s = String(s || "");
  if (s.startsWith("sha256:")) return "образ без имени";
  return s.replace(/^.*\//, "");
}
function family(n) { const p = n.split(/[-_]/); return (p.length > 1 ? p.slice(0, 2).join("-") : n).replace(/-[0-9a-f]{8,}$/, ""); }
let ruinsOpen = false;
async function renderHouses() {
  const box = $("#b-houses");
  if (!S.containers) await pollContainers();
  if (!S.containers) return oops(box, "До домов не дойти", S.containersErr, () => needFloor("houses", true));
  const run = S.containers.filter(c => c.state === "running").sort((a, b) => num(b.stats && b.stats.mem_used) - num(a.stats && a.stats.mem_used));
  const dead = S.containers.filter(c => c.state !== "running");
  const max = Math.max(1 << 21, ...run.map(c => num(c.stats && c.stats.mem_used)));
  const p = S.overview && S.overview.praxis;
  const groups = {};
  dead.forEach(c => { (groups[family(c.name)] = groups[family(c.name)] || []).push(c); });
  const gs = Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
  box.innerHTML = `
    ${p && p.subject ? `<div class="tower"><b>Башня Праксис</b><p>${esc(p.subject)}</p><small>последняя запись ${esc(ago(p.ts))} · ${esc(p.head || "")}</small></div>` : ""}
    <div class="legend">
      ${Object.entries(LEVELS).map(([k, v]) => `<span class="lv-${k}"><i></i>${v.name}</span>`).join("")}
    </div>
    <div>${run.map(c => {
      const st = c.stats || {}, h = clamp(Math.log(Math.max(num(st.mem_used), 1 << 20) / (1 << 20)) / Math.log(max / (1 << 20)), .12, 1);
      return `<button class="house lv-${esc(c.level || "normal")}" data-n="${esc(c.name)}">
        <span class="h-pic"><i style="height:${Math.round(h * 42)}px"></i></span>
        <span class="h-name">${esc(c.name)}</span><span class="h-stat">${bytes(st.mem_used)}</span>
        <span class="h-sub">${esc(lvl(c.level).name)} · ${esc(statusRu(c.status))}</span><span class="h-cpu">${fmt(st.cpu_pct, 1)}${NB}%</span>
      </button>`;
    }).join("")}</div>
    ${dead.length ? `<details class="ruins"${ruinsOpen ? " open" : ""}>
      <summary><b>Без света · ${dead.length}</b></summary>
      <p class="ruin-note">Остановленные контейнеры: это задний ряд города. Они ничего не делают, но занимают место на диске.</p>
      ${gs.map(([k, l]) => `<div class="rg"><h4>${esc(k)} <span>× ${l.length}</span></h4>${l.map(c => `<button class="ruin" data-n="${esc(c.name)}">${esc(c.name)}<small>${esc(statusRu(c.status))}</small></button>`).join("")}</div>`).join("")}
    </details>` : ""}`;
  box.dataset.ok = 1;
  $$("[data-n]", box).forEach(b => b.onclick = () => openHouse(S.containers.find(c => c.name === b.dataset.n)));
  const d = $(".ruins", box); if (d) d.ontoggle = () => { ruinsOpen = d.open; };
}

/* ---- −1: жители ---- */
let peopleTab = "cpu", peopleData = null;
const ST = { R: "работают сейчас", S: "спят", D: "застряли на диске", Z: "зомби", T: "приостановлены", I: "служебные потоки ядра" };
async function renderPeople() {
  const box = $("#b-people");
  try { peopleData = await api("/api/processes"); } catch (e) { return oops(box, "Перепись сорвалась", e, () => needFloor("people", true)); }
  const j = peopleData, states = j.states || {}, order = ["R", "D", "Z", "T", "S", "I"];
  const dots = order.flatMap(k => Array.from({ length: Math.min(1400, num(states[k])) }, () => `<i class="${k}"></i>`)).join("");
  box.innerHTML = `<div class="census" role="img" aria-label="Всего ${num(j.total)} процессов">${dots}</div>
    <div class="census-key">${["R", "D", "T", "S", "I", "Z"].filter(k => num(states[k]) || k === "Z").map(k => `<span><i class="census-${k}" style="background:${{ R: "var(--amber)", D: "var(--hot)", Z: "var(--green)", T: "var(--blue)", S: "rgba(246,237,218,.6)", I: "rgba(246,237,218,.2)" }[k]}"></i>${num(states[k])} ${ST[k]}</span>`).join("")}</div>
    <div class="tabs" role="tablist"><button data-t="cpu" role="tab">Кто трудится</button><button data-t="mem" role="tab">Кто занял память</button></div>
    <div id="procList"></div>`;
  box.dataset.ok = 1;
  $$(".tabs button", box).forEach(b => b.onclick = () => { peopleTab = b.dataset.t; paintProcs(); });
  paintProcs();
}
function paintProcs() {
  const j = peopleData; if (!j) return;
  $$("#b-people .tabs button").forEach(b => { const on = b.dataset.t === peopleTab; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  const list = (peopleTab === "cpu" ? j.top_cpu : j.top_mem || []).slice(0, 10);
  const top = Math.max(1, ...list.map(p => peopleTab === "cpu" ? num(p.cpu) : num(p.rss)));
  $("#procList").innerHTML = list.map(p => {
    const v = peopleTab === "cpu" ? num(p.cpu) : num(p.rss);
    return `<div class="proc"><b>${esc(p.comm || p.pid)}</b><em>${peopleTab === "cpu" ? fmt(p.cpu, 1) + NB + "%" : bytes(p.rss)}</em>
      <u><s style="width:${clamp(v / top * 100, 1, 100)}%"></s></u>
      <small>pid ${esc(p.pid)} · ${esc(p.user || "")} · ${peopleTab === "cpu" ? bytes(p.rss) : fmt(p.cpu, 1) + NB + "% ядра"}</small></div>`;
  }).join("");
}

/* ---- −2: ворота ---- */
const PORTNAME = { 22: "SSH, вход на сервер", 53: "DNS", 80: "сайты", 443: "сайты по HTTPS" };
function scopeOf(ips) {
  ips = ips || [];
  if (ips.some(ip => ip === "0.0.0.0" || ip === "::" || ip === "*" || /^\d+\.\d+\.\d+\.\d+$/.test(ip) && !/^(127\.|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/.test(ip))) return "pub";
  return "local";
}
async function renderGates() {
  const box = $("#b-gates"); let j;
  try { if (!S.containers) await pollContainers(); j = await api("/api/ports"); } catch (e) { return oops(box, "К стене не подойти", e, () => needFloor("gates", true)); }
  const items = j.items || [];
  const pub = items.filter(p => scopeOf(p.ips) === "pub"), loc = items.filter(p => scopeOf(p.ips) !== "pub");
  const byIp = {};
  (S.containers || []).forEach(c => Object.values(c.networks || {}).forEach(ip => { if (ip) byIp[ip] = c.name; }));
  const house = p => byIp[String(p.target || "").split(":")[0]];
  const who = p => PORTNAME[p.port] || house(p) || (p.comm === "docker-proxy" ? "докер" : (p.comm || "—"));
  box.innerHTML = `<p class="sub">Ворота в стене · ${pub.length}</p>
    <div class="wall">${pub.map(p => `<div class="arch"><b>${esc(p.port)}</b><em>${esc(p.proto)}</em><span>${esc(who(p))}</span></div>`).join("") || `<p class="note">Наружу не открыто ничего.</p>`}</div>
    <p class="sub">Калитки изнутри · ${loc.length}</p>
    <div class="wickets">${loc.map(p => `<div class="wicket"><b>${esc(p.port)}</b><span>${esc(p.comm || p.proto)}</span></div>`).join("")}</div>`;
  box.dataset.ok = 1;
}

/* ---- −3: метро ---- */
const LINE_C = ["#ff5a5f", "#ffb627", "#2ec4b6", "#4ea8ff", "#c77dff", "#f78fb3", "#9bdc4e", "#ff8c42"];
const LINE_RU = { praxis: "Праксис", "praxis-desk": "Праксис-деск", "amnezia-dns-net": "Амнезия", gate: "Шлюз", bridge: "Кольцевая", host: "Хостовая" };
function lineName(n) {
  const k = n.replace(/_default$/, "");
  return LINE_RU[k] || k;
}
async function renderMetro() {
  const box = $("#b-metro"); let j;
  try { j = await api("/api/netmap"); } catch (e) { return oops(box, "Схема линий не открылась", e, () => needFloor("metro", true)); }
  const nodes = Object.fromEntries((j.nodes || []).map(n => [n.id, n]));
  const nets = (j.nodes || []).filter(n => n.kind === "network");
  const lines = nets.map(n => ({ n, stations: [] })), byId = Object.fromEntries(lines.map(l => [l.n.id, l])), where = {};
  (j.links || []).forEach(l => {
    const line = byId[l.b]; const c = nodes[l.a];
    if (!line || !c || c.kind !== "container") return;
    line.stations.push({ c, ip: l.label });
    (where[c.label] = where[c.label] || []).push(l.b);
  });
  const none = byId["n:none"]; const noneCount = none ? none.stations.length : 0;
  const usable = lines.filter(l => l.n.id !== "n:none" && l.stations.length)
    .sort((a, b) => b.stations.filter(s => s.c.state === "running").length - a.stations.filter(s => s.c.state === "running").length || b.stations.length - a.stations.length);
  const active = usable.filter(l => l.stations.some(s => s.c.state === "running"));
  const closed = usable.filter(l => !l.stations.some(s => s.c.state === "running"));
  const colorOf = {}; active.forEach((l, i) => { colorOf[l.n.id] = LINE_C[i % LINE_C.length]; });
  const net = S.live ? num(S.live.net && S.live.net.rx) + num(S.live.net && S.live.net.tx) : 0;
  const dur = clamp(9 - Math.log10(net + 1) * 1.1, 2.4, 9).toFixed(1);
  let xferCount = 0;
  const html = active.map(l => {
    const st = [...l.stations].sort((a, b) => (a.c.state === "running" ? 0 : 1) - (b.c.state === "running" ? 0 : 1) || a.c.label.localeCompare(b.c.label));
    const on = st.filter(s => s.c.state === "running"), off = st.filter(s => s.c.state !== "running");
    const label = lineName(l.n.label), shown = [...on, ...off.slice(0, 2)], hidden = off.length - Math.min(2, off.length);
    return `<article class="line" style="--c:${colorOf[l.n.id]}">
      <header class="line-h"><span class="badge">${esc(label[0].toUpperCase())}</span><h3>${esc(label)}<small>${esc(l.n.label)} · ${esc(l.n.driver || "")}${l.n.internal ? " · внутренняя" : ""}</small></h3></header>
      <ol class="stations">${shown.map(s => {
        const other = (where[s.c.label] || []).filter(x => x !== l.n.id && x !== "n:none" && colorOf[x]);
        if (other.length) xferCount++;
        return `<li class="st${s.c.state === "running" ? "" : " off"}"><button data-n="${esc(s.c.label)}">${esc(s.c.label)}</button>${s.ip ? `<span class="ip">${esc(s.ip)}</span>` : ""}${other.map(x => `<span class="xfer" style="--x:${colorOf[x]}">⇄ ${esc(lineName(nodes[x].label))}</span>`).join("")}</li>`;
      }).join("")}${on.length > 1 ? `<i class="train" style="--dur:${dur}s" aria-hidden="true"></i>` : ""}</ol>
      ${hidden > 0 ? `<p class="more">и ещё ${count(hidden, "закрытая станция", "закрытые станции", "закрытых станций")}</p>` : ""}
    </article>`;
  }).join("");
  xferCount = Math.round(xferCount / 2);
  box.innerHTML = `<div class="lines">${html}</div>
    <p class="nonet">${xferCount ? `<b>${count(xferCount, "пересадка", "пересадки", "пересадок")}</b>: эти дома подключены сразу к двум линиям.` : "<b>Пересадок нет</b>: каждая линия сама по себе."}
    ${closed.length ? ` Ещё ${count(closed.length, "линия закрыта", "линии закрыты", "линий закрыто")}: на них только тёмные дома.` : ""}
    ${noneCount ? ` <b>${noneCount}</b> ${plural(noneCount, "дом", "дома", "домов")} без метро вообще: сеть им отключена.` : ""}</p>`;
  box.dataset.ok = 1;
  $$("[data-n]", box).forEach(b => b.onclick = () => { const c = (S.containers || []).find(x => x.name === b.dataset.n); if (c) openHouse(c); });
}

/* ---- −4: водохранилище ---- */
const WAVE = `<svg class="wave" viewBox="0 0 1200 14" preserveAspectRatio="none" aria-hidden="true"><path d="M0 14V8C100 0 200 0 300 8S500 16 600 8 800 0 900 8 1100 16 1200 8V14Z"/></svg>`;
async function renderWater() {
  const box = $("#b-water"); let j;
  try { j = await api("/api/disks"); } catch (e) { return oops(box, "Уровень не замерить", e, () => needFloor("water", true)); }
  const list = (j.disks || []).slice().sort((a, b) => num(b.total) - num(a.total));
  const tank = (d, small) => {
    const p = clamp(num(d.used_pct), 0, 100);
    return `<div class="tank${small ? " small" : ""}${p >= 88 ? " high" : ""}">
      <div class="water" style="height:${p}%">${WAVE}</div>
      <div class="t-lab"><b>${fmt(p, p < 10 ? 1 : 0)}<small>${NB}%</small></b>
      <p>${esc(d.mount)}${small ? "" : ` · ${esc(d.fstype)}`} · ${bytes(d.free)} свободно${small ? "" : ` из ${bytes(d.total)}`}</p></div>
    </div>`;
  };
  const D = j.docker || {};
  const parts = [["Образы", D.images_size], ["Кэш сборки", D.build_cache], ["Тома", D.volumes_size], ["Слои контейнеров", D.containers_size]].filter(p => p[1] != null);
  const dtotal = parts.reduce((a, p) => a + num(p[1]), 0) || 1;
  box.innerHTML = `${list[0] ? tank(list[0]) : ""}
    ${list.length > 1 ? `<div class="tanks-small">${list.slice(1).map(d => tank(d, true)).join("")}</div>` : ""}
    <dl class="flow"><div><dt>Дождь · запись</dt><dd id="fW">—</dd></div><div><dt>Насосы · чтение</dt><dd id="fR">—</dd></div></dl>
    <p class="sub">Что отстоялось на дне</p>
    ${parts.length ? `<div class="sediment">${parts.map(([k, v]) => `<div class="sed"><span>${k}</span><b>${bytes(v)}</b><u><s style="width:${clamp(num(v) / dtotal * 100, 1, 100)}%"></s></u></div>`).join("")}</div>` : `<p class="note">${esc(D.note || "Докер пока не сообщил размеры.")}</p>`}`;
  box.dataset.ok = 1;
  paintFlow();
  if (D.pending) setTimeout(() => { if (visible.has("water")) needFloor("water", true); }, 12000);
}
function paintFlow() {
  const w = $("#fW"); if (!w || !S.live) return;
  w.textContent = rate(S.live.disk && S.live.disk.write);
  $("#fR").textContent = rate(S.live.disk && S.live.disk.read);
}

/* ---- −5: фундамент ---- */
function stratum(o) {
  return `<div class="stratum" style="--c:${o.c};--bg:${o.c};--p:var(${o.p});${o.dark ? "--t:#1c120a;--ts:rgba(28,18,10,.78);" : ""}">
    ${edgeSvg(hash32(o.t))}
    <h3>${esc(o.t)}</h3><p>${esc(o.v)}</p>${o.s ? `<small>${esc(o.s)}</small>` : ""}${o.tags ? `<div class="tags">${o.tags.map(x => `<span>${esc(x)}</span>`).join("")}</div>` : ""}${o.extra || ""}
  </div>`;
}
async function renderRock() {
  const box = $("#b-rock"); let st, sys;
  try { [st, sys] = await Promise.all([api("/api/stack"), api("/api/system")]); } catch (e) { return oops(box, "Скважина не пробурилась", e, () => needFloor("rock", true)); }
  const v = st.virt || {}, k = st.kernel || {}, d = st.docker || {};
  const sec = (d.security || []).map(x => String(x).replace(/^name=/, ""));
  const up = num(st.uptime);
  box.innerHTML = `<div class="column">
    ${stratum({ t: "Дома", v: `${d.running || 0} работают, всего ${d.containers || 0}`, s: `Образов на складе: ${d.images || 0}.`, c: "#574567", p: "--dots" })}
    ${stratum({ t: "Докер", v: `движок ${d.engine || "—"}`, s: "Строит дома и ставит между ними стены.", c: "#2d6a7b", p: "--vee", tags: [`containerd ${d.containerd || "—"}`, d.driver, `cgroup v${d.cgroup || "?"}`, ...sec].filter(Boolean) })}
    ${stratum({ t: "Ядро Linux", v: k.kernel || "—", s: "Одно на весь город: все дома работают на нём напрямую.", c: "#cf8040", p: "--diag", dark: true, tags: [`потоков ${k.threads || "—"}`] })}
    ${stratum({ t: "Система", v: k.os_pretty || "—", s: `Без перезагрузки ${uptime(up)}.`, c: "#62673c", p: "--gravel" })}
    ${stratum({ t: "Виртуальная машина", v: v.kindname || (v.virtual ? "да" : "нет, живое железо"), s: v.virtual ? `Сам сервер тоже гость: ${v.product || v.vendor || ""}` : "", c: "#5e5345", p: "--cross" })}
    ${stratum({ t: "Железо", v: sys.cpu_model || k.cpu_model || "—", s: `Память ${bytes(sys.mem && sys.mem.total)}. Каждая колонна внизу это одно ядро: ${num(sys.cpu_cores)}${NB}${plural(num(sys.cpu_cores), "атлант держит", "атланта держат", "атлантов держат")} город.`, c: "#37353f", p: "--ore", extra: `<div class="atlantes" id="atlantes"></div><div class="atlas-cap"><span>${sys.cpu_mhz ? fmt(sys.cpu_mhz / 1000, 1) + NB + "ГГц" : ""}</span><span id="atlasAvg"></span></div>` })}
  </div>`;
  box.dataset.ok = 1;
  paintAtlantes();
}
function paintAtlantes() {
  const box = $("#atlantes"); if (!box || !S.live) return;
  const cores = (S.live.cpu && S.live.cpu.cores) || [];
  if (box.children.length !== cores.length) box.innerHTML = cores.map(() => `<div class="atlas"><i></i></div>`).join("");
  cores.forEach((v, i) => { const a = box.children[i]; a.className = "atlas" + (v >= 85 ? " hot" : v >= 55 ? " warm" : ""); a.firstChild.style.height = clamp(v, 1.5, 100) + "%"; });
  const av = $("#atlasAvg"); if (av) av.textContent = `в среднем ${fmt(S.live.cpu.total)}${NB}%`;
}

/* ---- −6: ключи ---- */
const LOCK = (open) => `<svg viewBox="0 0 28 34" aria-hidden="true"><rect x="3" y="14" width="22" height="18" rx="2" fill="currentColor"/><path d="${open ? "M8 14V9a6 6 0 0 1 11.6-2" : "M8 14V9a6 6 0 0 1 12 0v5"}" fill="none" stroke="currentColor" stroke-width="3"/><rect x="12.5" y="20" width="3" height="7" fill="#0d0b09"/></svg>`;
async function renderKeys() {
  const box = $("#b-keys"); let j;
  try { j = await api("/api/access"); } catch (e) { return oops(box, "Хранилище не открылось", e, () => needFloor("keys", true)); }
  const sh = j.sshd || {}, g = j.groups || {};
  const dflt = v => String(v || "").endsWith("*") ? " · по умолчанию" : "";
  const clean = v => String(v || "").replace("*", "");
  const rootV = clean(sh.permit_root_login), passV = clean(sh.password_auth), keyV = clean(sh.pubkey_auth);
  const rootKeyOnly = rootV === "prohibit-password" || rootV === "without-password";
  const root = rootV === "no" ? ["shut", "Root заперт", "Войти под root нельзя никак."]
    : rootKeyOnly ? ["key", "Root только по ключу", "Пароль для root не примут."]
    : rootV === "forced-commands-only" ? ["key", "Root только для команд", "По ключу и только заранее заданные команды."]
    : ["open", "Root входит и по паролю", "Самая широкая дверь на сервере."];
  const pass = passV === "no" ? ["shut", "Пароли не принимаются", "Войти можно только ключом."]
    : ["open", "Пароли принимаются", rootKeyOnly ? "Root так не войдёт, остальные учётки могут." : "Пароль можно попытаться подобрать."];
  const key = keyV === "no" ? ["shut", "Ключи отключены", "Вход по ключам выключен."] : ["key", "Ключи работают", "Обычный способ войти."];
  const door = (d, code) => `<div class="door ${d[0]}">${LOCK(d[0] === "open")}<div><b>${esc(d[1])}</b><span>${esc(d[2])}</span><code>${esc(code)}</code></div></div>`;
  const logins = (j.logins || []).filter(l => l.user !== "(загрузка)").slice(0, 8);
  const boots = (j.logins || []).filter(l => l.user === "(загрузка)");
  const active = j.active || [];
  box.innerHTML = `<div class="doors">
      ${door(root, `PermitRootLogin ${rootV}${dflt(sh.permit_root_login)}`)}
      ${door(pass, `PasswordAuthentication ${passV}${dflt(sh.password_auth)}`)}
      ${door(key, `PubkeyAuthentication ${keyV}${dflt(sh.pubkey_auth)}`)}
    </div>
    <div class="cols"><div>
      <p class="sub">Кто живёт на сервере</p>
      ${(j.humans || []).map(h => `<div class="person"><b>${esc(h.name)}</b><em>${count(num(h.keys), "ключ", "ключа", "ключей")}</em><small>${esc(h.home)} · ${esc(h.shell)}</small></div>`).join("")}
      <p class="note">Ещё ${count(num(j.system_count), "служебная учётка", "служебные учётки", "служебных учёток")}. В группе sudo: ${esc((g.sudo || []).join(", ") || "никого")}. В группе docker: ${esc((g.docker || []).join(", ") || "никого")}.</p>
    </div><div>
      <p class="sub">Журнал входов</p>
      <p class="note" style="margin:0 0 8px">${active.length ? `Сейчас на сервере: ${active.map(a => esc(a.user)).join(", ")}.` : "Сейчас на сервере никого."} SSH-порт ${esc(sh.port || "22")}.</p>
      ${logins.map(l => `<div class="log"><span>${esc(l.user)} · ${esc(l.host || l.line || "")}</span><time>${esc(whenShort(l.ts))}</time></div>`).join("") || `<p class="note">Записей о входах нет.</p>`}
      ${boots.length ? `<p class="note">Последняя загрузка сервера: ${esc(whenShort(boots[0].ts))}.</p>` : ""}
    </div></div>`;
  box.dataset.ok = 1;
  const o = S.overview;
  $("#colophon").textContent = o && o.host ? `${o.host.hostname} · ${o.host.os} · ${o.host.kernel}` : NB;
}

/* ------------------------------------------------------------------ лист дома */
let sheetBack = null, lastFocus = null;
function closeSheet() {
  $("#sheet").hidden = true;
  if (TG && TG.BackButton) { try { TG.BackButton.offClick(closeSheet); TG.BackButton.hide(); } catch (_) {} }
  if (lastFocus) { try { lastFocus.focus({ preventScroll: true }); } catch (_) {} lastFocus = null; }
}
function openSheet(html) {
  lastFocus = document.activeElement;
  $("#panelBody").innerHTML = html;
  $("#sheet").hidden = false;
  $("#panel").scrollTop = 0; $("#panel").focus({ preventScroll: true });
  if (TG && TG.BackButton) { try { TG.BackButton.onClick(closeSheet); TG.BackButton.show(); } catch (_) {} }
}
async function openHouse(c) {
  if (!c) return;
  const st = c.stats || {}, L0 = lvl(c.level), level = esc(c.level || "normal");
  const up = c.state === "running";
  const head = `<header class="s-head lv-${level}"><span class="s-pic"><i style="height:${up ? 74 : 42}px${up ? "" : ";filter:grayscale(.7) brightness(.6)"}"></i></span>
    <div><h3 id="sTitle">${esc(c.name)}</h3><p>${esc(statusRu(c.status))} · ${esc(imageName(c.image))}</p></div></header>`;
  openSheet(`${head}<div class="wait">Стучусь в дверь…</div>`);
  $("#panel").setAttribute("aria-labelledby", "sTitle");
  let d = null, err = null;
  try { d = await api("/api/container/" + c.id); } catch (e) { err = e; }
  if ($("#sheet").hidden || !$("#sTitle") || $("#sTitle").textContent !== c.name) return;
  if (err) {
    $("#panelBody").innerHTML = head + `<div class="oops"><b>Дверь не открыли</b>${esc(err.message || "")}</div>`;
    return;
  }
  const iso = d.isolation || {}, reach = d.reach || {}, s2 = d.stats || st, reasons = iso.reasons || c.iso_reasons || [];
  const peers = reach.peers || [], paths = reach.host_paths || [], ports = d.ports || c.ports || [];
  const published = ports.filter(p => p.public);
  const rows = [
    ["Запущен", d.started ? new Date(d.started).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : ""],
    ["Перезапусков", d.restarts], ["Политика", d.restart_policy], ["Здоровье", d.health],
    ["Команда", d.cmd], ["Переменных окружения", d.env_count],
  ].filter(r => r[1] !== "" && r[1] != null);
  $("#panelBody").innerHTML = `${head}
    <div class="walls lv-${level}"><b>${esc(L0.say)}</b>${reasons.length ? `<ul>${reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}</div>
    ${up ? `<div class="s-sec"><dl class="gauges">
      <div class="gauge"><dt>Процессор</dt><dd>${fmt(s2.cpu_pct, 1)}${NB}%</dd></div>
      <div class="gauge"><dt>Память</dt><dd>${bytes(s2.mem_used)}</dd><small>${fmt(s2.mem_pct, 1)}${NB}% от лимита ${bytes(s2.mem_limit)}</small></div>
    </dl></div>` : ""}
    ${published.length ? `<div class="s-sec"><h4>Двери на улицу</h4><div class="chips">${published.map(p => `<span class="chip${(p.ip || "").startsWith("127") ? "" : " warn"}">${esc(p.ip || "*")}:${esc(p.public)} → ${esc(p.private)}/${esc(p.proto || "tcp")}</span>`).join("")}</div></div>` : ""}
    ${peers.length ? `<div class="s-sec"><h4>Соседи по линии</h4><div class="chips">${peers.map(p => `<span class="chip">${esc(p.name)}</span>`).join("")}</div></div>` : ""}
    ${paths.length ? `<div class="s-sec"><h4>Что видит с хоста</h4><div class="chips">${paths.map(p => `<span class="chip${p.rw ? " warn" : ""}">${esc(p.src)} ${p.rw ? "можно менять" : "только читать"}</span>`).join("")}</div></div>` : ""}
    ${reach.docker_control ? `<div class="s-sec"><p class="note" style="color:var(--amber)">Может управлять докером: а значит, и всем городом.</p></div>` : ""}
    ${d.top && d.top.length ? `<div class="s-sec"><h4>Кто внутри</h4>${d.top.slice(0, 8).map(p => `<div class="kv-row"><span>${esc(p.pid)}</span><b>${esc(String(p.cmd || "").slice(0, 90))}</b></div>`).join("")}</div>` : ""}
    <div class="s-sec">${rows.map(r => `<div class="kv-row"><span>${esc(r[0])}</span><b>${esc(r[1])}</b></div>`).join("")}</div>
    <details class="logs s-sec" id="logs"><summary>Последние записи журнала</summary><pre>Читаю…</pre></details>`;
  const lg = $("#logs");
  lg.ontoggle = async () => {
    if (!lg.open || lg.dataset.ok) return;
    lg.dataset.ok = 1;
    const pre = lg.querySelector("pre");
    try { const l = await api("/api/container/" + c.id + "/logs"); pre.textContent = l.text || "Журнал пуст."; pre.scrollTop = pre.scrollHeight; }
    catch (e) { pre.textContent = "Журнал не открылся: " + (e.message || "нет ответа"); lg.dataset.ok = ""; }
  };
}
$("#sheetX").onclick = closeSheet;
$("#scrim").onclick = closeSheet;
addEventListener("keydown", e => { if (e.key === "Escape" && !$("#sheet").hidden) closeSheet(); });

document.addEventListener("DOMContentLoaded", boot);
})();
