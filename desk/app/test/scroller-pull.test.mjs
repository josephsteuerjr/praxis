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

// --- 02.10, Егор: «после оттягивания содержимое зависает» -------------------------------
// Два живых кейса, которых стенд не касался:
//
//  A) Обратный ход до смены знака съедает pull в ноль, и когда пальцы подняли,
//     release() не зовёт мягкий возврат (он под `if (this.pull)`): видимая
//     оттяжка остаётся в «тугом» пальцевом пространстве и висит почти без
//     движения — снаружи это «лента зависла после оттяжки».
//  B) Устройство, которое РАНЬШЕ присылало нулевое «пальцы подняли» (sawLift),
//     потеряло ноль после жеста: страховка HOLD_SAFETY держит оттяжку 8 секунд.
//
// Кейс A: жест вверх за край, обратный ход через ноль, подъём пальцев (ноль).
view.scrollTop = 0;
s.scrollTo(0, false);
wheel(0); // научили: это устройство присылает нулевое «подняли»
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const aPeak = shift();
assert.ok(aPeak > 60, `кейс A: оттяжка видна (${aPeak.toFixed(1)} px)`);
for (let i = 0; i < 40; i++) { wheel(12.5); advance(16); } // вернули за ноль и дальше
wheel(0); advance(16); // пальцы подняли
advance(300);
const aAfter = Math.abs(shift());
assert.ok(aAfter < 8, `кейс A: через 300 мс после подъёма пальцев оттяжки нет (осталось ${aAfter.toFixed(1)} px)`);
advance(1500);
assert.equal(shift(), 0, "кейс A: возврат дошёл до конца");

// Кейс B: ноль после жеста ПОТЕРЯН — тишина сама значит «отпустили» (02.10,
// слово владельца: висеть при отпущенных пальцах нельзя ни секунды).
view.scrollTop = 0;
s.scrollTo(0, false);
wheel(0); // sawLift=true
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const bPeak = shift();
assert.ok(bPeak > 60, `кейс B: оттяжка видна (${bPeak.toFixed(1)} px)`);
advance(700);
assert.ok(Math.abs(shift()) < bPeak * 0.5, `кейс B: тишина 700 мс — оттяжка уже тает (${Math.abs(shift()).toFixed(1)} из ${bPeak.toFixed(0)})`);
advance(800);
const bAfter = Math.abs(shift());
assert.ok(bAfter < 8, `кейс B: потерянный ноль не держит оттяжку (осталось ${bAfter.toFixed(1)} px)`);
advance(800);
assert.equal(shift(), 0, "кейс B: лента вернулась");

// Кейс C (02.10, слово владельца): контроль пальцами вернулся во время возврата —
// движение продолжается с места подхвата, а не начинается заново: лента не доедает
// до нуля и не скачет, оттяжка дальше растёт от пойманного положения.
view.scrollTop = 0;
s.scrollTo(0, false);
wheel(0);
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const cPeak = shift();
assert.ok(cPeak > 60, `кейс C: оттяжка видна (${cPeak.toFixed(1)} px)`);
advance(380); // отпустили: возврат только начался, лента ещё на середине пути
const cMid = Math.abs(shift());
assert.ok(cMid > cPeak * 0.3 && cMid < cPeak, `кейс C: возврат в пути (${cMid.toFixed(1)} из ${cPeak.toFixed(0)})`);
// пальцы вернулись и тянут дальше — с МЕСТА, не с нуля
for (let i = 0; i < 4; i++) { wheel(-12.5); advance(16); }
const cGrab = Math.abs(shift());
assert.ok(cGrab > cMid, `кейс C: подхват не уронил ленту к нулю (${cGrab.toFixed(1)} > ${cMid.toFixed(1)})`);
for (let i = 0; i < 60; i++) { wheel(-12.5); advance(16); }
assert.ok(Math.abs(shift()) >= cPeak, `кейс C: оттяжка продолжила расти от места подхвата (${Math.abs(shift()).toFixed(1)} >= ${cPeak.toFixed(0)})`);
advance(2000);
assert.equal(shift(), 0, "кейс C: финальный возврат дошёл до конца");
console.log(`scroller: подъём после обратного хода и потерянный ноль — ok (A: ${aPeak.toFixed(0)}→${aAfter.toFixed(0)}, B: ${bPeak.toFixed(0)}→${bAfter.toFixed(1)}, C: подхват ${cMid.toFixed(0)}→${cGrab.toFixed(0)}→${Math.abs(shift()).toFixed(0)})`);
