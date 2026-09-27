// Лаборатория окна Hélène, 28.09: один и тот же прототип ленты в четырёх движках —
// Tauri/WebView2 и Electron на Windows, WebKitGTK и Electron на Linux (WSLg).
// Егор выбирает движок глазами, стенд «Прогон» даёт цифры кадров для сравнения.
import { FEELS, Scroller, type FeelName, type Overscroll } from "../../ui-kit/feed/scroller";
import { Zoom } from "../../ui-kit/feed/zoom";
import { KeyedList } from "../../ui-kit/feed/keyed";
import { md, esc } from "./md";
import { LONG_REPLY, agentLine, nextId, ownerLine, seed, type Msg } from "./data";

// ------------------------------------------------------------------ движок

function engineName(): string {
  const ua = navigator.userAgent;
  const host = new URLSearchParams(location.search).get("host") || "";
  const chrome = ua.match(/Chrome\/(\d+)/)?.[1];
  const electron = ua.match(/Electron\/([\d.]+)/)?.[1];
  const w = window as unknown as Record<string, unknown>;
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "?";
  if (electron) return `Electron ${electron} · Chromium ${chrome} · ${os}`;
  if (w.__TAURI_INTERNALS__ || host === "tauri") return `Tauri · ${/Edg\//.test(ua) ? "WebView2" : "WebView"} (Chromium ${chrome}) · ${os}`;
  if (host.startsWith("webkitgtk")) return `WebKitGTK ${host.split(":")[1] || ""} · ${os}`;
  if (chrome) return `${/Edg\//.test(ua) ? "Edge" : "Chrome"} ${chrome} · ${os}`;
  const wk = ua.match(/AppleWebKit\/([\d.]+)/)?.[1];
  return `WebKit ${wk || ""} · ${os}`;
}

// ------------------------------------------------------------------ разметка

const ICON: Record<string, string> = {
  now: '<path d="M3.5 10h3l2-5 3 10 2-5h3"/>',
  chat: '<path d="M4 4.5h12v8H9l-3.5 3v-3H4z"/>',
  tasks: '<rect x="4" y="3.5" width="12" height="13" rx="2"/><path d="M7 8h6M7 11h4"/>',
  files: '<path d="M5.5 3.5h6l3 3v10h-9z"/><path d="M11.5 3.5v3h3"/>',
  journal: '<path d="M10 3.5 3.5 16h13z"/><path d="M10 8.5v3.5M10 14v.01"/>',
  settings: '<circle cx="10" cy="10" r="2.5"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>',
};
const NAV: Array<[string, string]> = [["now", "Сейчас"], ["chat", "Чат"], ["tasks", "Задачи"], ["files", "Файлы"], ["journal", "Журнал"], ["settings", "Настройки"]];

document.getElementById("app")!.innerHTML = `
  <aside class="rail">
    <div class="who">
      <div>
        <div class="who-name">Мира</div>
        <div class="who-state"><span class="dot"></span>на связи</div>
      </div>
    </div>
    <nav class="nav">${NAV.map(([k, t]) => `<button type="button" class="${k === "chat" ? "on" : ""}"><svg viewBox="0 0 20 20">${ICON[k]}</svg>${t}</button>`).join("")}</nav>
    <div class="rail-art"><span class="sun small"></span><span class="sun"></span></div>
  </aside>
  <main class="main">
    <header class="head"><h1>Чат</h1><div class="sub">с Мирой · прототип ленты, текст выдуман</div></header>
    <section class="feed" id="feed" tabindex="0" aria-label="Переписка"><div class="feed-inner" id="feed-inner"><div class="feed-zoom" id="feed-zoom"></div></div></section>
    <footer class="composer">
      <div class="composer-box">
        <textarea id="say" rows="1" placeholder="Написать Мире…"></textarea>
        <button class="send" id="send" type="button" aria-label="Отправить"><svg viewBox="0 0 20 20"><path d="M10 15.5v-11M5.8 8.7 10 4.5l4.2 4.2"/></svg></button>
      </div>
      <div class="composer-hint">Тяни ленту мышью за поля · Alt — откуда угодно · Ctrl+колесо — масштаб</div>
    </footer>
    <button class="news" id="news" type="button">новые <b>↓</b></button>
  </main>
  <aside class="lab" id="lab">
    <div class="lab-head"><span class="t">Лаборатория</span><button id="lab-min" type="button" title="Свернуть">⇥</button></div>
    <div class="lab-body">
      <div class="engine" id="engine"></div>
      <div class="fps" id="fps">кадры: —</div>
      <div class="btns">
        <button type="button" data-act="agent">Слово Миры</button>
        <button type="button" data-act="owner">Моё</button>
        <button type="button" data-act="burst">Поток ×12</button>
        <button type="button" data-act="long">Лента 250</button>
        <button type="button" data-act="bench" class="go">Прогон ▶</button>
      </div>
      <div class="seg" data-seg="over"><span>Край ленты</span><button data-v="rubber">резинка</button><button data-v="stretch">растяжка (Google)</button><button data-v="none">без</button></div>
      <div class="seg" data-seg="feel"><span>Характер движения</span><button data-v="brisk">бодро</button><button data-v="smooth">плавно</button><button data-v="syrup">тягуче</button></div>
      <div class="seg" data-seg="theme"><span>Тема</span><button data-v="light">день</button><button data-v="dark">ночь</button><button data-v="system">как в системе</button></div>
      <div class="seg" data-seg="hand"><span>Почерк (заголовки, имя)</span><button data-v="Zen Kurenaido">Zen Kurenaido</button><button data-v="Neucha">Neucha</button><button data-v="Pangolin">Pangolin</button><button data-v="Klee One">Klee One</button><button data-v="Shantell Sans">Shantell (сейчас)</button></div>
      <div class="seg" data-seg="text"><span>Текст ленты</span><button data-v="Golos Text">Golos</button><button data-v="Source Serif 4">Source Serif</button></div>
      <div class="hint" id="zoom-hint">Масштаб 100% · Ctrl+колесо, щипок, Ctrl+= / Ctrl+− / Ctrl+0</div>
      <div class="result" id="result"></div>
    </div>
  </aside>`;

// ------------------------------------------------------------------ лента

const feedEl = document.getElementById("feed")!;
const inner = document.getElementById("feed-inner")!;
const zbox = document.getElementById("feed-zoom")!;
const news = document.getElementById("news")!;
let unseen = 0;

const scroller = new Scroller(feedEl, inner, {
  overscroll: "rubber",
  onPinnedChange: (p) => { if (p) { unseen = 0; news.classList.remove("on"); } },
});
const zoom = new Zoom(scroller, zbox, {
  storageKey: "lab.zoom",
  onChange: (z) => { const h = document.getElementById("zoom-hint"); if (h) h.textContent = `Масштаб ${Math.round(z * 100)}% · Ctrl+колесо, щипок, Ctrl+= / Ctrl+− / Ctrl+0`; },
});

type Row = { kind: "day"; key: string; label: string } | { kind: "msg"; key: string; m: Msg; v: number };

const fmtTime = (t: number) => new Date(t).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
function dayLabel(t: number): string {
  const d = new Date(t), now = new Date();
  const k = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  if (k(d) === k(now)) return "Сегодня";
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (k(d) === k(y)) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

function msgHTML(m: Msg): string {
  if (m.who === "system") return `<div class="plaque">${esc(m.text)}</div>`;
  if (m.who === "owner") return `<div class="msg-meta"><span>${fmtTime(m.at)}</span></div><div class="bubble msg-body">${md(m.text)}</div>`;
  return `<div class="msg-meta"><span class="name">Мира</span><span>${fmtTime(m.at)}</span></div><div class="msg-body">${md(m.text)}</div>`;
}

const list = new KeyedList<Row>(zbox, {
  key: (r) => r.key,
  same: (a, b) => a.kind === "day" || (b.kind === "msg" && a.kind === "msg" && a.v === b.v),
  create(r) {
    const el = document.createElement("div");
    if (r.kind === "day") {
      el.className = "day";
      el.textContent = r.label;
    } else {
      el.className = `row ${r.m.who}${r.m.typing ? " typing" : ""}`;
      el.innerHTML = msgHTML(r.m);
    }
    return el;
  },
  update(el, r) {
    if (r.kind !== "msg") return;
    el.classList.toggle("typing", !!r.m.typing);
    // Правим только тело: шапка и сам узел остаются.
    const body = el.querySelector(".msg-body");
    if (body) body.innerHTML = md(r.m.text);
  },
});

let msgs: Msg[] = [];
const ver = new Map<string, number>();

function rows(): Row[] {
  const out: Row[] = [];
  let day = "";
  for (const m of msgs) {
    const d = dayLabel(m.at);
    if (d !== day) { out.push({ kind: "day", key: "day:" + d, label: d }); day = d; }
    out.push({ kind: "msg", key: m.id, m, v: ver.get(m.id) || 0 });
  }
  return out;
}

function paint(animate = true) {
  scroller.preserve(() =>
    list.set(rows(), {
      animate,
      onAdded: (els) => {
        if (!animate || scroller.pinned) return;
        unseen += els.filter((e) => e.classList.contains("row")).length;
        if (unseen) { news.innerHTML = `новые: ${unseen} <b>↓</b>`; news.classList.add("on"); }
      },
    }),
  );
}

function push(m: Msg) { msgs.push(m); paint(true); }
function touch(m: Msg) { ver.set(m.id, (ver.get(m.id) || 0) + 1); paint(true); }

news.addEventListener("click", () => scroller.toBottom(true));

/** Слово Миры по словам, как идёт поток модели. */
function stream(text: string, gap = 45): Promise<void> {
  const m: Msg = { id: nextId(), who: "agent", at: Date.now(), text: "", typing: true };
  push(m);
  const words = text.split(/(\s+)/);
  let i = 0;
  return new Promise((done) => {
    const t = setInterval(() => {
      for (let k = 0; k < 2 && i < words.length; k++) m.text += words[i++];
      if (i >= words.length) { m.typing = false; clearInterval(t); touch(m); done(); return; }
      touch(m);
    }, gap);
  });
}

let ownerN = 0, agentN = 0;
function sendOwn(text: string) {
  push({ id: nextId(), who: "owner", at: Date.now(), text });
  scroller.toBottom(true);
  setTimeout(() => void stream(agentLine(agentN++), 40), 650);
}

const say = document.getElementById("say") as HTMLTextAreaElement;
const fit = () => { say.style.height = "auto"; say.style.height = Math.min(180, say.scrollHeight) + "px"; };
say.addEventListener("input", fit);
say.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const t = say.value.trim();
    if (!t) return;
    say.value = ""; fit();
    sendOwn(t);
  }
});
document.getElementById("send")!.addEventListener("click", () => {
  const t = say.value.trim() || ownerLine(ownerN++);
  say.value = ""; fit();
  sendOwn(t);
});

// ------------------------------------------------------------------ панель

document.getElementById("lab-min")!.addEventListener("click", () => document.getElementById("app")!.classList.toggle("lab-min"));
document.getElementById("engine")!.textContent = engineName();

function store(k: string, v?: string): string | null {
  try { if (v !== undefined) localStorage.setItem(k, v); return localStorage.getItem(k); } catch { return null; }
}

const SEG: Record<string, (v: string) => void> = {
  over: (v) => { scroller.overscroll = v as Overscroll; },
  feel: (v) => { scroller.feel = { ...(FEELS[v as FeelName] ?? FEELS.syrup) }; },
  theme: (v) => { if (v === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v; },
  hand: (v) => document.documentElement.style.setProperty("--hand", `"${v}", "Neucha", cursive`),
  text: (v) => document.documentElement.style.setProperty("--text", `"${v}", "Segoe UI", system-ui, sans-serif`),
};
const DEF: Record<string, string> = { over: "rubber", feel: "syrup", theme: "system", hand: "Zen Kurenaido", text: "Golos Text" };
// Прежние выборы лаборатории (до слова Егора 28.09 «резинка нравится») — не держим.
if (store("lab.v") !== "2") { for (const k of ["over", "feel", "hand", "text"]) { try { localStorage.removeItem("lab." + k); } catch { /* */ } } store("lab.v", "2"); }
for (const seg of document.querySelectorAll<HTMLElement>("[data-seg]")) {
  const name = seg.dataset.seg!;
  const pick = (v: string) => {
    for (const b of seg.querySelectorAll<HTMLButtonElement>("button")) b.classList.toggle("on", b.dataset.v === v);
    SEG[name](v);
    store("lab." + name, v);
  };
  for (const b of seg.querySelectorAll<HTMLButtonElement>("button")) b.addEventListener("click", () => pick(b.dataset.v!));
  pick(new URLSearchParams(location.search).get(name) || store("lab." + name) || DEF[name]);
}

// Журнал жестов: как приходят события колеса/тачпада на этой машине (Windows не говорит,
// где пальцы, а где инерция — пороги подбираются по живым записям). Пишется в wheel.log.
{
  let buf: string[] = [];
  let t0 = 0;
  feedEl.addEventListener("wheel", (e) => {
    const now = performance.now();
    if (!t0 || now - t0 > 2000) buf.push("---");
    const d = scroller.debug();
    buf.push([Math.round(now - (t0 || now)), e.deltaY.toFixed(2), e.deltaMode, e.ctrlKey ? "ctrl" : "", `pos=${d.pos}/${d.max}`, `raw=${d.raw}`, `pull=${d.pull}`, d.pad, d.notch ? "notch" : "pad"].join("\t"));
    t0 = now;
  }, { passive: true });
  setInterval(() => { if (buf.length) { console.log("WHEEL\n" + buf.join("\n")); buf = []; } }, 700);
}

const ACT: Record<string, () => void> = {
  agent: () => void stream(LONG_REPLY, 35),
  owner: () => sendOwn(ownerLine(ownerN++)),
  burst: () => { let i = 0; const t = setInterval(() => { push({ id: nextId(), who: i % 2 ? "agent" : "owner", at: Date.now(), text: i % 2 ? agentLine(agentN++) : ownerLine(ownerN++) }); if (++i >= 12) clearInterval(t); }, 140); },
  long: () => { msgs = seed(250); ver.clear(); paint(false); scroller.toBottom(false); },
  bench: () => void bench(),
};
for (const b of document.querySelectorAll<HTMLButtonElement>("[data-act]")) b.addEventListener("click", () => ACT[b.dataset.act!]());

// ------------------------------------------------------------------ кадры

/** Интервалы между кадрами: живой счётчик и запись для «Прогона». */
const frames: number[] = [];
let rec: number[] | null = null;
let lastT = 0;
function tick(t: number) {
  if (lastT) {
    const d = t - lastT;
    frames.push(d);
    if (frames.length > 240) frames.shift();
    rec?.push(d);
  }
  lastT = t;
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

function stats(xs: number[]) {
  const s = xs.slice().sort((a, b) => a - b);
  const q = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))] || 0;
  const med = q(0.5) || 16.7;
  const vs = med; // шаг обновления экрана
  let missed = 0;
  for (const x of xs) missed += Math.max(0, Math.round(x / vs) - 1);
  const expected = xs.length + missed;
  return { n: xs.length, hz: 1000 / vs, avg: xs.reduce((a, b) => a + b, 0) / (xs.length || 1), p95: q(0.95), p99: q(0.99), max: s[s.length - 1] || 0, drop: expected ? (missed / expected) * 100 : 0 };
}
setInterval(() => {
  const st = stats(frames.slice(-120));
  document.getElementById("fps")!.innerHTML = `экран ~${st.hz.toFixed(0)} Гц · p95 <b>${st.p95.toFixed(1)}</b> мс · худший ${st.max.toFixed(0)} мс · пропуски <b>${st.drop.toFixed(1)}%</b>`;
}, 500);

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function bench() {
  const out = document.getElementById("result")!;
  out.textContent = "идёт прогон… не трогай мышь секунд 15";
  document.getElementById("app")!.classList.remove("lab-min");
  const res: Array<[string, ReturnType<typeof stats>]> = [];
  const phase = async (name: string, run: () => Promise<void>) => {
    rec = [];
    await run();
    res.push([name, stats(rec)]);
    rec = null;
  };
  msgs = seed(250); ver.clear(); paint(false); scroller.toBottom(false);
  await wait(500);
  await phase("бросок вверх/вниз", async () => { scroller.fling(-6); await wait(1500); scroller.fling(6.5); await wait(1700); scroller.toBottom(true); await wait(600); });
  await phase("поток ×12", async () => { for (let i = 0; i < 12; i++) { push({ id: nextId(), who: i % 2 ? "agent" : "owner", at: Date.now(), text: i % 2 ? agentLine(i) : ownerLine(i) }); await wait(130); } await wait(700); });
  await phase("печатает", async () => { await stream(LONG_REPLY, 25); await wait(400); });
  await phase("масштаб 100→160→100", async () => { zoom.set(1.6); await wait(900); zoom.set(1); await wait(900); });
  const lines = [engineName(), `экран ~${res[0][1].hz.toFixed(0)} Гц`, ""];
  for (const [n, s] of res) lines.push(`${n.padEnd(22)} p95 ${s.p95.toFixed(1).padStart(5)} мс · худший ${s.max.toFixed(0).padStart(3)} · пропуски ${s.drop.toFixed(1)}%`);
  out.textContent = lines.join("\n");
  (window as unknown as { __bench?: unknown }).__bench = { engine: engineName(), phases: res };
  const json = JSON.stringify({ engine: engineName(), phases: res });
  console.log("BENCH " + json);
  const tauri = (window as unknown as { __TAURI_INTERNALS__?: { invoke(c: string, a: unknown): Promise<unknown> } }).__TAURI_INTERNALS__;
  if (tauri) void tauri.invoke("bench_done", { json });
  const gtk = (window as unknown as { __labBench?: (j: string) => void }).__labBench;
  if (gtk) gtk(json);
}

// ------------------------------------------------------------------ старт

msgs = seed(24);
paint(false);
scroller.toBottom(false);
void zoom;

// Самопроверка края (?selftest=1): синтетические жесты тачпада, как их шлёт Windows.
async function selftest() {
  const pad = (dy: number) => feedEl.dispatchEvent(new WheelEvent("wheel", { deltaY: dy, deltaMode: 0, bubbles: true, cancelable: true }));
  const lift = () => pad(0); // так Chromium на Windows говорит «пальцы поднялись»
  const tick = () => wait(16);
  const raw = () => scroller.debug().raw;
  const out: Record<string, unknown> = {};
  msgs = seed(60); ver.clear(); paint(false); scroller.toBottom(false);
  await wait(400);
  // 1) пальцами вверх от низа, подняли
  for (let i = 0; i < 30; i++) { pad(-40); await tick(); }
  lift();
  await wait(900);
  // 2) взмах вниз: пальцы коротко, подняли, инерция доезжает до низа — должна упереться
  for (let i = 0; i < 5; i++) { pad(40); await tick(); }
  lift();
  let v = 40, peak = 0;
  for (let i = 0; i < 160; i++) { pad(Math.round(v)); v = Math.max(1, v * 0.97); peak = Math.max(peak, Math.abs(raw())); await tick(); }
  out.swipeArrivesPeakRaw = peak;
  out.parkedAtBottom = scroller.debug().pos === scroller.debug().max;
  // 3) инерция ещё капает (1–2), а пальцы уже тянут — оттяжка сразу, без «глухоты»
  for (let i = 0; i < 6; i++) { pad(1); await tick(); }
  for (let i = 0; i < 20; i++) { pad(5 + (i % 3)); await tick(); }
  await wait(200);
  out.pullRightAfterPark = raw();
  // 4) пальцы замерли на полторы секунды — оттяжка держится, не сбрасывается
  await wait(1500);
  out.pullAfterHold1500 = raw();
  // 5) подняли — возврат плавный: перетяг только убывает, без рывков и перелёта
  lift();
  let prev = raw(), jerk = 0, overshoot = 0;
  const steps: number[] = [];
  for (let i = 0; i < 90; i++) {
    await tick();
    const r = raw();
    steps.push(prev - r);
    if (r > prev + 0.01) jerk++;
    if (r < -0.5) overshoot++;
    prev = r;
  }
  // Первый шаг возврата не больше соседних: пружина трогается мягко, не срывается.
  const firstStep = steps[0] ?? 0;
  const maxStep = Math.max(...steps);
  out.releaseMonotonic = jerk === 0 && overshoot === 0;
  out.releaseSoftStart = firstStep <= maxStep * 0.6;
  out.finalRaw = raw();
  const ok = out.swipeArrivesPeakRaw === 0 && out.parkedAtBottom === true && (out.pullRightAfterPark as number) > 20 &&
    Math.abs((out.pullAfterHold1500 as number) - (out.pullRightAfterPark as number)) < 1 && out.releaseMonotonic === true && out.finalRaw === 0;
  console.log("SELFTEST " + JSON.stringify({ ok, ...out }));
}
if (new URLSearchParams(location.search).get("selftest") === "1") setTimeout(() => void selftest(), 800);

// Автопрогон для оболочек (--auto): стенд через полторы секунды после старта.
if (new URLSearchParams(location.search).get("auto") === "1") setTimeout(() => void bench(), 1500);
