// Оттяжка тачпада у края: не копится без предела и возвращается один к одному с пальцами.
//
// Егор 29.09: «верхняя граница "Работа завершена" в моменте застряла на середине и
// отматывалась тоже чуть выше середины максимум. Физика». Корень: на Windows тачпад не
// присылает нулевое «пальцы поднялись», инерция после жеста шла в оттяжку, запас рос без
// предела, видимая резинка насыщалась, а обратный ход съедал невидимый запас, почти не
// двигая ленту. Стенд гоняет настоящий ui-kit/feed/scroller.ts на поддельном DOM и сам
// шагает по кадрам (в скрытом окне браузер кадров не даёт — проверять там нечем).
//
// Запуск: node app/test/scroller-pull.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

// --- поддельное окружение ---------------------------------------------------------------
let clock = 0;
const frames = [];
const timers = [];
globalThis.performance = { now: () => clock };
globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
globalThis.cancelAnimationFrame = () => {};
globalThis.window = {
  setTimeout: (fn, ms) => { timers.push({ at: clock + ms, fn }); return timers.length; },
  addEventListener() {}, removeEventListener() {},
};
globalThis.setTimeout = window.setTimeout;
globalThis.clearTimeout = (id) => { if (timers[id - 1]) timers[id - 1].fn = null; };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.getComputedStyle = () => ({ overflowY: "visible" });
globalThis.HTMLElement = class {};
globalThis.document = { createRange: () => ({ selectNodeContents() {}, getClientRects: () => [] }) };

function el(h, sh) {
  const handlers = {};
  return {
    clientHeight: h, scrollHeight: sh, scrollTop: 0, style: {}, isConnected: true,
    addEventListener: (t, fn) => { handlers[t] = fn; }, removeEventListener() {},
    hasPointerCapture: () => false, closest: () => null, handlers,
    classList: { remove() {} },
  };
}

/** Шаг времени: кадры по 16 мс и таймеры, как в живом окне. */
function advance(ms) {
  const end = clock + ms;
  while (clock < end) {
    clock += 16;
    for (const t of timers) if (t.fn && t.at <= clock) { const f = t.fn; t.fn = null; f(); }
    const due = frames.splice(0);
    for (const f of due) f(clock);
  }
}

const src = readFileSync(new URL("../../ui-kit/feed/scroller.ts", import.meta.url), "utf8");
const mod = await import("data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(src)).toString("base64"));

const view = el(688, 3000);
const inner = el(0, 0);
const s = new mod.Scroller(view, inner, { feel: "syrup", overscroll: "rubber", stick: false });
const shift = () => { const m = String(inner.style.transform || "").match(/translate3d\(0(?:px)?, (-?[\d.]+)px/); return m ? parseFloat(m[1]) : 0; };
const wheel = (dy) => view.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: view, preventDefault() {} });

// Пальцы тянут вверх за край и инерция добавляет ещё — 150 событий, нулевого «поднялись» нет.
for (let i = 0; i < 150; i++) { wheel(-12.5); advance(16); }
const limit = 688 * mod.FEELS.syrup.rubberD;
const peak = shift();
assert.ok(peak > 0, "оттяжка видна");
assert.ok(peak <= limit * 0.75 + 1, `оттяжка не выше ¾ предела (${peak.toFixed(1)} из ${limit.toFixed(1)})`);

// Обратный ход: 8 событий по 12,5 — лента откликается сразу, почти на те же 100 px.
for (let i = 0; i < 8; i++) { wheel(12.5); advance(16); }
advance(120);
const back = peak - shift();
assert.ok(back > 80, `обратный ход двигает ленту сразу: ${back.toFixed(1)} px из 100`);

// Отпустили (событий нет) — оттяжка тает к нулю.
advance(3000);
assert.equal(shift(), 0, "оттяжка вернулась");

console.log(`scroller: оттяжка ${peak.toFixed(0)} px (потолок ${(limit * 0.75).toFixed(0)}), обратный ход ${back.toFixed(0)} px из 100 — ok`);

// Returning to a section must discard outgoing motion, not drag its saved
// position toward the old fling target on the next animation frame.
s.scrollTo(700, false);
s.fling(5);
advance(64);
s.scrollTo(310, false);
advance(1200);
assert.equal(view.scrollTop, 310, "navigation cancels the outgoing fling");
assert.equal(s.pinned, false, "reading history does not follow incoming messages");

s.scrollTo(0, false);
for (let i = 0; i < 8; i++) { wheel(-12.5); advance(16); }
assert.ok(shift() > 0, "rubber still works before navigation");
s.scrollTo(500, false);
advance(1200);
assert.equal(view.scrollTop, 500, "navigation clears outgoing rubber motion");
assert.equal(shift(), 0, "new section has no inherited transform");
s.scrollTo(view.scrollHeight, false);
assert.equal(s.pinned, true, "returning to the end resumes following");
console.log("scroller: section restoration during fling and rubber — ok");
