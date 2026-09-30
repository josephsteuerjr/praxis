// Колонка ходов справа от переписки — переделана целиком 28.09 (слово Егора: «правая
// вкладка как была ублюдской неразличимой и с неудобно разворачиваемыми шагами… там
// вообще всё надо менять»).
//
// Что изменилось:
//  - ходы — лентой со временем и точкой состояния: крупно, что агент сделал/сказал;
//    мельче — на что («Егор: …»); строкой — шаги, руки, время, кэш. Различимо с первого
//    взгляда, без раскрытия;
//  - раскрытие — на месте и плавно (высота растёт), без перерисовки всей колонки:
//    раньше щелчок по ходу пересобирал колонку `innerHTML` целиком;
//  - шаги внутри — строками со значками (мысль, компьютер, файл, поиск, ответ, итог);
//    каждая рука раскрывается до параметров и результата;
//  - идущий ход — сверху, шаги появляются по одному и вырастают, а не перерисовываются.
// Всё — ключевыми списками (ui-kit/feed/keyed): узлы создаются один раз и правятся.
import { KeyedList } from "../feed/keyed";
import type { Scroller } from "../feed/scroller";
import { api } from "./api";
import { bindFail, esc, failHTML, fmtDur, fmtTime, q } from "./lib";
import { fmtK } from "../text";
import { renderSteps, ACTIONS, clip, frameStripHTML, lastUsage, pretty, resultText, subject, terminalLabel, textBox, type RunDetail } from "../steps";
export { stepsHTML, type RunDetail } from "../steps";
import * as scroll from "./scroll";
import { LEGACY_WINDOW_KEY, S, WINDOW_ROOM, foreignHarness, isWindowRoom, runIsRecent, type Run } from "./state";

interface Turn {
  run_id: string;
  kind?: string;
  who?: string;
  in?: string;
  out?: string;
  ts?: string | number;
  delivery?: string;
  held?: string;
  note?: string;
}

type Status = "live" | "done" | "failed" | "silent";

interface TurnRow {
  id: string;
  status: Status;
  at: string; // ISO — время хода
  title: string;
  cause: string;
  live: boolean;
}

const EV_KIND: Record<string, string> = {
  chat_turn: "сообщение",
  wake: "пробуждение",
  task_window: "окно",
  moderation: "модерация",
};

// ---------------------------------------------------------------- значки (линия, как облик)

const ICON: Record<string, string> = {
  think: '<path d="M8 13.5c-2.4-.6-4-2.5-4-4.8C4 6 6.2 4 9 4c.6-1.2 2-2 3.5-2C15 2 17 4 17 6.3c1.2.5 2 1.7 2 3 0 1.9-1.6 3.4-3.6 3.4H12l-3 3v-2.2z"/>',
  computer: '<rect x="3" y="4" width="14" height="10" rx="1.5"/><path d="M7.5 17h5M10 14v3"/>',
  shell: '<rect x="3" y="4" width="14" height="12" rx="1.5"/><path d="m6.5 8.5 2.2 1.8-2.2 1.8M10.5 12.5h3"/>',
  file: '<path d="M5.5 3.5h6l3 3v10h-9z"/><path d="M11.5 3.5v3h3"/>',
  edit: '<path d="M4 16l1-4 8.5-8.5 3 3L8 15z"/><path d="M11.5 5.5l3 3"/>',
  search: '<circle cx="9" cy="9" r="4.5"/><path d="m12.5 12.5 4 4"/>',
  web: '<circle cx="10" cy="10" r="6.5"/><path d="M3.5 10h13M10 3.5c2 2 2 11 0 13M10 3.5c-2 2-2 11 0 13"/>',
  memory: '<path d="M6 3.5h8v13l-4-3-4 3z"/>',
  reply: '<path d="M4 4.5h12v8H9l-3.5 3v-3H4z"/>',
  end: '<path d="m5 10.5 3.2 3L15 6.5"/>',
  fail: '<path d="m6 6 8 8M14 6l-8 8"/>',
  hand: '<circle cx="10" cy="10" r="2.6"/><path d="M10 3v2.2M10 14.8V17M3 10h2.2M14.8 10H17M5 5l1.6 1.6M13.4 13.4 15 15M5 15l1.6-1.6M13.4 6.6 15 5"/>',
  origin: '<path d="M4 10h9M10 6l4 4-4 4"/>',
  word: '<path d="M5 6h10M5 10h10M5 14h6"/>',
};
function icon(kind: string): string {
  return `<svg class="ico" viewBox="0 0 20 20" aria-hidden="true">${ICON[kind] || ICON.hand}</svg>`;
}
function toolIcon(tool: string, args: unknown): string {
  const t = tool || "";
  if (t === "reply") return "reply";
  if (t === "end_turn") return "end";
  if (t === "computer") {
    const a = (args || {}) as { action?: string };
    return a.action === "run" || a.action === "poll" ? "shell" : "computer";
  }
  if (t === "shell" || t === "coding_process") return "shell";
  if (/read|coding_read/.test(t)) return "file";
  if (/write|edit|patch|write_skill/.test(t)) return "edit";
  if (/web|fetch_url|read_url/.test(t)) return "web";
  if (/search/.test(t)) return "search";
  if (/recall|remember|memory|remind/.test(t)) return "memory";
  return "hand";
}

// ---------------------------------------------------------------- подписи

/** Скобки кадра (`[thread #898 …]`) и подпись «Имя: » — адрес, а не слова. */
const clean = (s: string) =>
  s
    .replace(/^\s*…?\s*\[[^\]]{0,200}\]\s*/, "")
    .replace(/^[^:\n]{1,40}:\s*/, "")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();

function noteWords(note: string): string {
  const n = String(note || "").trim();
  const m = n.match(/^(done|wait|blocked|silence)\s*:\s*(.*)$/i);
  const body = m ? m[2] : n;
  const head = m ? (m[1].toLowerCase() === "wait" ? "Жду: " : m[1].toLowerCase() === "blocked" ? "Препятствие: " : "") : "";
  return body.trim() ? head + clean(body) : "";
}

function turnAt(t: Turn, run?: Run): string {
  if (run?.created_at) return run.created_at;
  const s = parseFloat(String(t.ts ?? ""));
  return Number.isFinite(s) ? new Date(s * 1000).toISOString() : "";
}

function rowOf(t: Turn, run: Run | undefined, liveId: string): TurnRow {
  const out = clean(t.out || "");
  const inn = clean(t.in || "");
  const note = noteWords(t.note || "");
  const who = (t.who || "").trim();
  const live = t.run_id === liveId;
  const failed = run?.terminal_status === "failed" || run?.status === "failed" || t.delivery === "failed";
  const status: Status = live ? "live" : failed ? "failed" : t.held === "unspoken" ? "silent" : "done";
  const title = out || note || (t.held === "unspoken" ? "Решила промолчать" : "") || `${EV_KIND[run?.kind || t.kind || ""] || "сообщение"} · ${fmtTime(turnAt(t, run))}`;
  const cause = inn ? `${who || "Кто-то"}: «${clip(inn, 140)}»` : who ? `${EV_KIND[run?.kind || t.kind || ""] || "сообщение"} · ${who}` : "";
  return { id: t.run_id, status, at: turnAt(t, run), title: clip(title, 220), cause, live };
}

/** Строка «3 шага · компьютер ×2 · 24 с · кэш 97%» — из подробностей хода. */
function metaOf(d: RunDetail | undefined): string {
  if (!d) return "";
  const its = d.iterations || [];
  const tools = new Map<string, number>();
  let ms = 0;
  for (const it of its) {
    ms += it.ms || 0;
    for (const t of it.tools || []) {
      if (["reply", "end_turn", "telegram.deliver"].includes(t.tool || "")) continue;
      const name = (ACTIONS[t.tool || ""] || t.tool || "рука").toLowerCase();
      tools.set(name, (tools.get(name) || 0) + 1);
    }
  }
  const parts: string[] = [];
  if (its.length) parts.push(`${its.length} ${plural(its.length, "шаг", "шага", "шагов")}`);
  const top = [...tools.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2);
  for (const [name, n] of top) parts.push(n > 1 ? `${name} ×${n}` : name);
  if (ms) parts.push(fmtDur(ms / 1000));
  const u = lastUsage(d);
  if (u && u.in + u.cached) parts.push(`кэш ${Math.round((100 * u.cached) / (u.in + u.cached))}%`);
  return parts.join(" · ");
}

function plural(n: number, one: string, few: string, many: string): string {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

// ---------------------------------------------------------------- шаги хода

interface StepRow { key: string; html: string; cls: string }

/** Шаги хода строками: повод, мысль и руки каждой итерации, слово, итог. */
function stepRows(d: RunDetail, withFrame = true): StepRow[] {
  const rows: StepRow[] = [];
  const runId = d.id || "";
  const live = d.manifest?.status === "running";
  const origin = clean(d.origin?.text || "");
  if (origin) {
    rows.push({ key: "origin", cls: "st st-origin", html:
      `<div class="st-line">${icon("origin")}<span class="st-label">Повод</span><span class="st-what">${esc(clip(origin, 300))}</span></div>` +
      openLink(d) });
  }
  (d.iterations || []).forEach((it, i) => {
    const key = it.call_id || String(it.seq ?? i + 1);
    const u = it.usage || {};
    const cached = u.cache_read || 0;
    const total = (u.in || 0) + cached;
    const complete = it.status === "completed" || (it.status !== "failed" && it.ms != null);
    const thinking = !complete && it.status !== "failed" && !it.tools?.length && live;
    const cut = complete && it.stop === "max_tokens";
    const meta = [it.ms != null ? `${(it.ms / 1000).toFixed(1)} с` : "", u.in != null ? `${fmtK(total)} → ${fmtK(u.out || 0)}` : ""].filter(Boolean).join(" · ");
    const state = it.status === "failed" ? "ошибка модели" : cut ? "оборван потолком" : thinking ? "думает…" : "";
    rows.push({ key: "it:" + key, cls: `st st-think${thinking ? " is-live" : ""}${it.status === "failed" || cut ? " is-failed" : ""}`, html:
      `<div class="st-line">${icon(it.status === "failed" ? "fail" : "think")}<span class="st-label">Шаг ${i + 1}</span>` +
      `<span class="st-meta">${esc(meta)}</span>${state ? `<span class="st-state">${esc(state)}</span>` : ""}</div>` +
      (it.error ? `<div class="st-err">${esc(clip(it.error, 400))}</div>` : "") });
    (it.tools || []).forEach((t, j) => {
      const tkey = "tool:" + (t.call_id || `${key}:${j}`);
      const received = t.result != null || t.status === "received";
      const failed = t.status === "failed";
      const active = !received && !failed && live;
      const emptyDelivery = t.tool === "telegram.deliver" && (t.args as { text_chars?: number } | null)?.text_chars === 0;
      // Доставка ядра без единого знака — не её действие: слово ушло рукой reply. Не шум в ленте.
      if (emptyDelivery && !(t.args as { media_count?: number } | null)?.media_count) return;
      const title = emptyDelivery ? "Доставка ядра" : ACTIONS[t.tool || ""] || t.tool || "Действие";
      const what = t.tool === "reply" ? String((t.args as { text?: string } | null)?.text || "") : subject(t.args);
      const head = String(t.result?.head || t.result?.tail || "");
      const result = t.result?.truncated ? [t.result.head, "… пропущена часть результата …", t.result.tail].filter(Boolean).join("\n") : head;
      const receipt = ["reply", "end_turn", "telegram.deliver"].includes(t.tool || "");
      const mark = failed ? "ошибка" : active ? "выполняется…" : emptyDelivery ? "нечего доставлять" : "";
      const detail: string[] = [];
      if (t.args != null && t.tool !== "reply") detail.push(`<div class="st-k">Параметры</div><pre class="st-pre">${esc(clip(pretty(t.args), 4000))}</pre>`);
      if (head && !receipt) detail.push(`<div class="st-k">Результат</div>` + textBox(tkey + ":result", resultText(result), { truncated: t.result?.truncated, ref: t.result?.result_id, run: runId, size: t.result?.size }));
      if (receipt && result) detail.push(`<div class="st-k">Квитанция</div><pre class="st-pre">${esc(clip(result, 2000))}</pre>`);
      if (t.error) detail.push(`<div class="st-k">Ошибка</div><pre class="st-pre">${esc(t.error)}</pre>`);
      const kind = failed ? "fail" : toolIcon(t.tool || "", t.args);
      const isWord = t.tool === "reply";
      rows.push({ key: tkey, cls: `st st-hand${isWord ? " st-word" : ""}${active ? " is-live" : ""}${failed ? " is-failed" : ""}`, html:
        (detail.length ? `<button type="button" class="st-line st-toggle" aria-expanded="false">` : `<div class="st-line">`) +
        `${icon(kind)}<span class="st-label">${esc(title)}</span>` +
        `<span class="st-what">${esc(clip(what.replace(/\s+/g, " "), isWord ? 400 : 160))}</span>` +
        (mark ? `<span class="st-state">${esc(mark)}</span>` : "") +
        (detail.length ? `</button><div class="st-detail" hidden>${detail.join("")}</div>` : `</div>`) });
    });
    if (it.text) {
      rows.push({ key: "text:" + key, cls: "st st-word", html:
        `<div class="st-line">${icon("word")}<span class="st-label">Слово</span></div>` +
        textBox("text:" + key, it.text, { truncated: !!it.text_truncated, ref: it.text_ref, run: runId }) });
    }
  });
  const term = d.manifest?.terminal || {};
  if (term.status) {
    const t = terminalLabel(term.status, term.reason || "");
    rows.push({ key: "end", cls: `st st-end${t.failed ? " is-failed" : ""}`, html:
      `<div class="st-line">${icon(t.failed ? "fail" : "end")}<span class="st-label">${esc(t.label)}</span>` +
      `${term.reason ? `<span class="st-what">${esc(clip(term.reason, 160))}</span>` : ""}</div>` });
  } else if (!(d.iterations || []).length) {
    rows.push({ key: "wait", cls: "st st-think is-live", html: `<div class="st-line">${icon("think")}<span class="st-label">${live ? "Работа началась — первые шаги ещё не записаны" : "Шаги не записаны"}</span></div>` });
  }
  const strip = withFrame ? frameStripHTML(d, "Кадр этого хода", '<a href="#" data-go="frame">зоны K·E·A·T →</a>') : "";
  if (strip) rows.push({ key: "frame", cls: "st st-frame", html: strip });
  return rows;
}

function openLink(d: RunDetail): string {
  const raw = String(d.manifest?.chat_id ?? "");
  const room = raw === LEGACY_WINDOW_KEY ? "window" : raw;
  if (!room) return "";
  return `<a href="#" class="st-open" data-open-room="${esc(room)}" data-at="${esc(d.manifest?.created_at || "")}">открыть в чате</a>`;
}

// ---------------------------------------------------------------- колонка

interface Col {
  box: HTMLElement;
  room: string;
  inner: HTMLElement;
  head: HTMLElement;
  strip: HTMLElement;
  list: KeyedList<TurnRow>;
  empty: HTMLElement;
  scroller: Scroller;
  stripHTML: string;
  painted: boolean;
}
let col: Col | null = null;
/** Раскрытые ходы и раскрытые руки внутри них (переживают перерисовку). */
const openSteps = new Map<string, KeyedList<StepRow>>();
const openHands = new Set<string>();
/** Раскрыты окном, а не владельцем: идущий ход. Закончился — сворачиваем сами. */
const autoOpen = new Set<string>();
let liveTimer = 0;
let liveBusy = false;
let metaQueue: string[] = [];
let metaBusy = false;

function panelBox(): HTMLElement {
  return q<HTMLElement>("#panel");
}

function mountCol(): Col {
  const box = panelBox();
  col?.scroller.destroy();
  box.innerHTML = `<div class="turns-inner">
    <div class="turns-head"><span class="turns-title">Ходы</span><span class="turns-room"></span><span class="turns-n"></span></div>
    <details class="turns-context" hidden><summary>Контекст и расход</summary><div class="turns-strip"></div></details>
    <div class="turns" role="list"></div>
    <div class="turns-empty" hidden></div>
  </div>`;
  const inner = box.querySelector<HTMLElement>(".turns-inner")!;
  const c: Col = {
    box,
    room: S.room,
    inner,
    head: box.querySelector<HTMLElement>(".turns-head")!,
    strip: box.querySelector<HTMLElement>(".turns-strip")!,
    empty: box.querySelector<HTMLElement>(".turns-empty")!,
    list: new KeyedList<TurnRow>(box.querySelector<HTMLElement>(".turns")!, {
      key: (r) => r.id,
      create: turnEl,
      same: (a, b) => a.status === b.status && a.title === b.title && a.cause === b.cause && a.at === b.at,
      update: (el, r, prev) => {
        fillTurn(el, r);
        if (r.live && !el.classList.contains("open")) { autoOpen.add(r.id); void openTurn(el, r.id, true); }
        else if (prev.live && !r.live && autoOpen.has(r.id)) { autoOpen.delete(r.id); closeTurn(el, r.id); }
      },
    }),
    scroller: scroll.mountColumn(box, inner),
    stripHTML: "",
    painted: false,
  };
  if (!box.dataset.turnsBound) {
    box.dataset.turnsBound = "1";
    box.addEventListener("click", onClick);
  }
  return c;
}

function turnEl(r: TurnRow): HTMLElement {
  const el = document.createElement("article");
  el.className = "turn";
  el.setAttribute("role", "listitem");
  el.innerHTML = `<button type="button" class="turn-head" aria-expanded="false">
      <span class="turn-time"></span><span class="turn-dot"></span>
      <span class="turn-text"><span class="turn-title"></span><span class="turn-cause"></span><span class="turn-meta"></span></span>
    </button>
    <div class="turn-body" hidden></div>`;
  fillTurn(el, r);
  const d = S.evCache.get(r.id) as RunDetail | undefined;
  if (d) setMeta(el, d);
  else metaQueue.push(r.id);
  if (r.live && !S.evOpen.has(r.id)) autoOpen.add(r.id);
  if (S.evOpen.has(r.id) || r.live) void openTurn(el, r.id, false);
  return el;
}

function fillTurn(el: HTMLElement, r: TurnRow) {
  el.dataset.run = r.id;
  el.dataset.status = r.status;
  el.querySelector(".turn-time")!.textContent = fmtTime(r.at);
  el.querySelector(".turn-title")!.textContent = r.title;
  const cause = el.querySelector<HTMLElement>(".turn-cause")!;
  cause.textContent = r.cause;
  cause.hidden = !r.cause;
  el.querySelector(".turn-dot")!.setAttribute("title", { live: "идёт сейчас", done: "завершён", failed: "не дошёл до конца", silent: "промолчала" }[r.status]);
}

function setMeta(el: HTMLElement, d: RunDetail) {
  const m = el.querySelector<HTMLElement>(".turn-meta");
  if (m) m.textContent = metaOf(d);
}

async function runDetail(runId: string, fresh = false): Promise<RunDetail | undefined> {
  if (!fresh) {
    const cached = S.evCache.get(runId) as RunDetail | undefined;
    if (cached) return cached;
  }
  try {
    const d = await api<RunDetail>("/api/run/" + encodeURIComponent(runId));
    S.evCache.set(runId, d);
    return d;
  } catch {
    return undefined;
  }
}

/** Подписи «шаги · руки · время» — фоном, по одному ходу, чтобы не бить канал залпом. */
async function drainMeta() {
  if (metaBusy) return;
  metaBusy = true;
  try {
    while (metaQueue.length && col) {
      const id = metaQueue.shift()!;
      const el = col.list.element(id);
      if (!el) continue;
      const d = await runDetail(id);
      if (d && el.isConnected) setMeta(el, d);
    }
  } finally {
    metaBusy = false;
  }
}

/** Раскрыть ход: шаги строками, высота растёт плавно. */
async function openTurn(el: HTMLElement, id: string, animate: boolean) {
  const body = el.querySelector<HTMLElement>(".turn-body")!;
  const head = el.querySelector<HTMLElement>(".turn-head")!;
  el.classList.add("open");
  head.setAttribute("aria-expanded", "true");
  S.evOpen.add(id);
  let steps = openSteps.get(id);
  if (!steps) {
    body.innerHTML = `<div class="st-list"></div>`;
    steps = new KeyedList<StepRow>(body.querySelector<HTMLElement>(".st-list")!, {
      key: (s) => s.key,
      create: stepEl,
      same: (a, b) => a.html === b.html && a.cls === b.cls,
      update: (node, s) => {
        node.className = s.cls;
        node.innerHTML = s.html;
        restoreHand(node, id, s.key);
      },
    });
    openSteps.set(id, steps);
  }
  body.hidden = false;
  if (animate) slide(body, true);
  const cached = S.evCache.get(id) as RunDetail | undefined;
  const first = el.parentElement?.firstElementChild === el;
  if (cached) steps.set(stepRows(cached, !first), { animate: false });
  else body.classList.add("loading");
  const live = el.dataset.status === "live";
  const d = await runDetail(id, live || !cached);
  body.classList.remove("loading");
  if (!el.isConnected || !S.evOpen.has(id)) return;
  if (!d) {
    if (!cached) { openSteps.delete(id); body.innerHTML = `<div class="muted st-none">Шаги не прочитались.</div>`; }
    return;
  }
  setMeta(el, d);
  col?.scroller.preserve(() => steps!.set(stepRows(d, !first), { animate: !!cached }));
  let full = body.querySelector<HTMLDetailsElement>(".turn-full");
  if (!full) {
    full = document.createElement("details"); full.className = "turn-full";
    full.innerHTML = '<summary>Полный ход</summary><div class="turn-full-body"></div>';
    body.append(full);
    full.addEventListener("toggle", () => { if (full!.open) renderSteps(full!.querySelector<HTMLElement>(".turn-full-body")!, S.evCache.get(id) as RunDetail || d); });
  }
}

function closeTurn(el: HTMLElement, id: string) {
  const body = el.querySelector<HTMLElement>(".turn-body")!;
  el.classList.remove("open");
  el.querySelector(".turn-head")!.setAttribute("aria-expanded", "false");
  S.evOpen.delete(id);
  slide(body, false, () => {
    if (!S.evOpen.has(id)) { body.hidden = true; body.innerHTML = ""; openSteps.delete(id); }
  });
}

function stepEl(s: StepRow): HTMLElement {
  const node = document.createElement("div");
  node.className = s.cls;
  node.innerHTML = s.html;
  return node;
}

/** Раскрытая рука остаётся раскрытой, даже если её строка перерисовалась (статус сменился). */
function restoreHand(node: HTMLElement, runId: string, key: string) {
  if (!openHands.has(runId + "/" + key)) return;
  const btn = node.querySelector<HTMLElement>(".st-toggle");
  const det = node.querySelector<HTMLElement>(".st-detail");
  if (btn && det) { btn.setAttribute("aria-expanded", "true"); det.hidden = false; node.classList.add("open"); }
}

/** Плавно раскрыть/свернуть по высоте (WAAPI); при reduced-motion — сразу. */
function slide(el: HTMLElement, open: boolean, done?: () => void) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches || typeof el.animate !== "function";
  if (still) { done?.(); return; }
  const h = el.scrollHeight;
  el.style.overflow = "clip";
  const a = el.animate(
    open ? [{ height: "0px", opacity: 0 }, { height: h + "px", opacity: 1 }] : [{ height: h + "px", opacity: 1 }, { height: "0px", opacity: 0 }],
    { duration: open ? 380 : 260, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
  );
  a.onfinish = () => { el.style.overflow = ""; done?.(); };
  a.oncancel = () => { el.style.overflow = ""; };
}

function onClick(e: MouseEvent) {
  const t = e.target as HTMLElement;
  const go = t.closest<HTMLElement>("[data-go]");
  if (go) {
    e.preventDefault();
    dispatchEvent(new CustomEvent("frame-go", { detail: go.dataset.go }));
    return;
  }
  const hand = t.closest<HTMLElement>(".st-toggle");
  if (hand) {
    const node = hand.closest<HTMLElement>(".st")!;
    const det = node.querySelector<HTMLElement>(".st-detail");
    const runId = hand.closest<HTMLElement>(".turn")?.dataset.run || "";
    const key = node.dataset.key || "";
    if (!det) return;
    const open = det.hidden === true;
    hand.setAttribute("aria-expanded", String(open));
    node.classList.toggle("open", open);
    if (open) { det.hidden = false; slide(det, true); openHands.add(runId + "/" + key); }
    else { openHands.delete(runId + "/" + key); slide(det, false, () => { if (!openHands.has(runId + "/" + key)) det.hidden = true; }); }
    return;
  }
  const head = t.closest<HTMLElement>(".turn-head");
  if (head) {
    const el = head.closest<HTMLElement>(".turn")!;
    const id = el.dataset.run || "";
    autoOpen.delete(id); // владелец решил сам — окно больше не сворачивает за него
    if (el.classList.contains("open")) closeTurn(el, id);
    else void openTurn(el, id, true);
  }
}

// ---------------------------------------------------------------- живой ход

/** Идёт ли сейчас ход в текущей комнате (по состоянию движка и манифесту). */
function liveRunId(): string {
  if (foreignHarness()) {
    // Чужой код агента: сердцебиения нет — свежий запуск в статусе running этой
    // комнаты и есть идущий ход.
    const run = S.runs.find((x) => {
      if (x.status !== "running" || !runIsRecent(x)) return false;
      let key = String(x.chat_id ?? "");
      if (key === LEGACY_WINDOW_KEY) key = WINDOW_ROOM;
      return key === S.room;
    });
    return run ? run.id : "";
  }
  const r = S.agentState?.runner;
  // Сон — не ход (ревью 26.09): у него нет прогона, и «идущего хода» он не рисует.
  if (!r || !r.alive || !r.busy || !r.run || r.run === "sleep") return "";
  const run = S.runs.find((x) => x.id === r.run);
  if (!run) return r.run; // манифеста в списке ещё нет — покажем как есть
  let key = String(run.chat_id ?? "");
  if (key === LEGACY_WINDOW_KEY) key = WINDOW_ROOM;
  if (run.chat_id == null) return r.run;
  return key === S.room || (isWindowRoom(key) && isWindowRoom(S.room) && key === S.room) ? r.run : "";
}

function liveSince(runId: string): string {
  const r = S.agentState?.runner;
  if (r?.since && r.busy) return fmtDur(Date.now() / 1000 - r.since);
  const run = S.runs.find((x) => x.id === runId);
  const at = new Date(run?.created_at ?? "").getTime();
  return isNaN(at) ? "" : fmtDur((Date.now() - at) / 1000);
}

export async function render(): Promise<void> {
  if (!col || !col.box.isConnected || col.box.firstElementChild !== col.inner || col.room !== S.room) {
    openSteps.clear();
    col = mountCol();
  }
  const c = col;
  c.head.querySelector(".turns-room")!.textContent = S.roomName;
  let turns: Turn[];
  try {
    turns = await api<Turn[]>("/api/chat-turns/" + encodeURIComponent(S.room));
  } catch (e) {
    c.empty.hidden = false;
    c.empty.innerHTML = failHTML(e);
    bindFail(c.empty, () => void render());
    return;
  }
  if (col !== c) return;
  const byRun = new Map(S.runs.map((r) => [r.id, r]));
  const liveId = liveRunId();
  const rows = turns.slice().reverse().map((t) => rowOf(t, byRun.get(t.run_id), liveId));
  // Идущий ход, которого ещё нет в chat-turns (слово не сказано), — сверху.
  if (liveId && !rows.some((r) => r.id === liveId)) {
    const run = byRun.get(liveId);
    rows.unshift({ id: liveId, status: "live", at: run?.created_at || new Date().toISOString(), title: "Работает…", cause: clean(run?.goal_head || ""), live: true });
  }
  c.head.querySelector(".turns-n")!.textContent = rows.length ? String(rows.length) : "";
  c.empty.hidden = rows.length > 0;
  if (!rows.length) c.empty.innerHTML = `<div class="empty">Ходов ещё нет</div>`;
  c.scroller.preserve(() => c.list.set(rows, { animate: c.painted }));
  c.painted = true;
  void paintStrip(rows);
  void drainMeta();
  scheduleLive(!!liveId);
}

/** Кадр последнего хода — одной тонкой полосой над лентой ходов. */
async function paintStrip(rows: TurnRow[]) {
  const c = col;
  if (!c) return;
  const first = rows[0];
  if (!first) {
    c.strip.parentElement!.hidden = true;
    return;
  }
  const d = await runDetail(first.id, first.live);
  if (col !== c) return;
  const html = frameStripHTML(d, first.live ? "Кадр сейчас" : "Кадр последнего хода", '<a href="#" data-go="frame">зоны K·E·A·T →</a>');
  c.strip.parentElement!.hidden = !html;
  if (html === c.stripHTML) return;
  c.stripHTML = html;
  c.scroller.preserve(() => { c.strip.innerHTML = html; });
}

/** Пока руннер занят, шаги идущего хода перечитываются раз в полторы секунды. */
function scheduleLive(on: boolean) {
  clearTimeout(liveTimer);
  if (!on) return;
  liveTimer = window.setTimeout(() => void refreshLive(), 1500);
}

async function refreshLive() {
  if (S.view !== "talk" || !col) return;
  if (liveBusy) { scheduleLive(true); return; }
  const id = liveRunId();
  const el = id ? col.list.element(id) : undefined;
  if (!id || !el) {
    void render();
    return;
  }
  liveBusy = true;
  try {
    const d = await runDetail(id, true);
    const meta = el.querySelector<HTMLElement>(".turn-meta");
    if (meta) meta.textContent = [liveSince(id), metaOf(d)].filter(Boolean).join(" · ");
    const steps = openSteps.get(id);
    if (d && el.isConnected) {
      col.scroller.preserve(() => {
        if (steps) steps.set(stepRows(d, false), { animate: true });
        const full = el.querySelector<HTMLDetailsElement>(".turn-full");
        if (full?.open) renderSteps(full.querySelector<HTMLElement>(".turn-full-body")!, d);
      });
      const last = d.iterations?.at(-1);
      const tool = last?.tools?.find(t => !t.result && !t.error);
      const label = tool ? (ACTIONS[tool.tool || ""] || tool.tool || "выполняет действие") : last && !last.text && !last.tools?.length ? "думает" : "ведёт ход";
      dispatchEvent(new CustomEvent("frame-live-action", { detail: label }));
    }
  } catch {
    // Краткий обрыв связи не должен останавливать обновление действий.
  } finally {
    liveBusy = false;
  }
  scheduleLive(true);
}

/** Состояние обновилось: руннер занят или только что освободился. */
export function tick(busy: boolean) {
  if (S.view !== "talk") return;
  const live = !!col && [...col.list.container.children].some((n) => (n as HTMLElement).dataset.status === "live");
  if (busy !== live) void render();
}

/** Событие вызова модели — шаги живого хода могли прибавиться. */
export function onLlm() {
  if (S.view !== "talk") return;
  clearTimeout(liveTimer);
  liveTimer = window.setTimeout(() => void refreshLive(), 300);
}

export async function revealLiveRun() {
  await render();
  const id = liveRunId();
  const el = id ? col?.list.element(id) : undefined;
  if (id && el) { await openTurn(el, id, true); el.querySelector<HTMLElement>(".turn-head")?.focus({ preventScroll: true }); }
}

export function onRunEvent(runId: string) {
  S.evCache.delete(runId);
  onLlm();
}
