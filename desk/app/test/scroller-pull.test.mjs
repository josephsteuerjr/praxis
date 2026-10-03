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
// Ход может уже пересечь край: учитываем и оттяжку, и нативную прокрутку.
const back = peak - shift() + view.scrollTop;
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
advance(200); // пальцы легли заново — с человеческой паузой, не в ноль миллисекунд
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const aPeak = shift();
assert.ok(aPeak > 40, `кейс A: оттяжка видна (${aPeak.toFixed(1)} px)`);
for (let i = 0; i < 40; i++) { wheel(12.5); advance(16); } // вернули за ноль и дальше
wheel(0); advance(16); // пальцы подняли
advance(300);
const aAfter = Math.abs(shift());
assert.ok(aAfter < 8, `кейс A: через 300 мс после подъёма пальцев оттяжки нет (осталось ${aAfter.toFixed(1)} px)`);
advance(1500);
assert.equal(shift(), 0, "кейс A: возврат дошёл до конца");

// 03.10, пересмотр с владельцем: wheel не даёт контакта пальцев. Тишина
// завершает оттяжку, даже если раньше устройство присылало ноль.
view.scrollTop = 0;
s.scrollTo(0, false);
wheel(0); // sawLift=true: это устройство шлёт нули
advance(200); // пауза перекладывания пальцев
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const bPeak = shift();
assert.ok(bPeak > 40, `кейс B: оттяжка видна (${bPeak.toFixed(1)} px)`);
advance(1500); // палец замер БЕЗ отпускания
assert.equal(shift(),0,"кейс B: тишина не оставляет крупную оттяжку");
wheel(0); advance(16); // отпустили — ноль
advance(900);
const bAfter = Math.abs(shift());
assert.ok(bAfter < 8, `кейс B: после нуля — мягкий возврат сразу (осталось ${bAfter.toFixed(1)} px)`);
advance(800);
assert.equal(shift(), 0, "кейс B: лента вернулась");

// Кейс C (02.10, слово владельца): контроль пальцами вернулся во время возврата —
// движение продолжается с места подхвата, а не начинается заново: лента не доедает
// до нуля и не скачет, оттяжка дальше растёт от пойманного положения.
view.scrollTop = 0;
s.scrollTo(0, false);
wheel(0);
advance(200); // пауза перекладывания пальцев
for (let i = 0; i < 40; i++) { wheel(-12.5); advance(16); }
const cPeak = shift();
assert.ok(cPeak > 40, `кейс C: оттяжка видна (${cPeak.toFixed(1)} px)`);
wheel(0); advance(16); // отпустили: возврат пошёл
advance(120); // лента ещё на середине пути
const cMid = Math.abs(shift());
assert.ok(cMid > cPeak * 0.3 && cMid < cPeak, `кейс C: возврат в пути (${cMid.toFixed(1)} из ${cPeak.toFixed(0)})`);
// пальцы вернулись и тянут дальше — с МЕСТА, не с нуля. Первое событие подхвата
// неотличимо от опоздавшей инерции (03.10: различие видно только со второго —
// пальцы держат скорость, инерция убывает), за эти ~32 мс пружина чуть
// спружинит; проверяем «не рухнула», а рост — следующим утверждением.
for (let i = 0; i < 4; i++) { wheel(-12.5); advance(16); }
const cGrab = Math.abs(shift());
assert.ok(cGrab > cMid * 0.75, `кейс C: подхват не уронил ленту к нулю (${cGrab.toFixed(1)} из ${cMid.toFixed(1)})`);
for (let i = 0; i < 60; i++) { wheel(-12.5); advance(16); }
assert.ok(Math.abs(shift()) >= cPeak, `кейс C: оттяжка продолжила расти от места подхвата (${Math.abs(shift()).toFixed(1)} >= ${cPeak.toFixed(0)})`);
wheel(0); advance(16); // отпустили
advance(2000);
assert.equal(shift(), 0, "кейс C: финальный возврат дошёл до конца");

// Кейс D (02.10, слово владельца — ГЛАВНАЯ причина): импульс тачпадом разогнал
// ленту — по достижении текстом границы лента обязана ПАРКОВАТЬСЯ точно в край,
// без остаточного смещения и без «границы выше места остановки».
s.scrollTo(1500, false);
for (let i = 0; i < 20; i++) { wheel(-120); advance(16); } // резкий жест вверх
wheel(0); advance(16); // пальцы подняли — дальше инерция системы
for (const d of [-60, -48, -38, -30, -24, -19, -15, -12, -9, -7, -5, -4, -3, -2, -2, -1]) { wheel(d); advance(16); }
advance(2500);
assert.equal(view.scrollTop, 0, `кейс D: парковка у верхней границы (осталось ${view.scrollTop})`);
assert.equal(shift(), 0, `кейс D: без остаточного смещения (${shift()} px)`);
console.log(`scroller: подъём после обратного хода — ok (A: ${aPeak.toFixed(0)}→${aAfter.toFixed(0)}, B: фиксация ${bPeak.toFixed(0)}, C: подхват ${cMid.toFixed(0)}→${cGrab.toFixed(0)}, D: парковка у края)`);

// Кейс E (02.10, слово владельца): всё то же — фиксация, возврат, парковка —
// в режиме РАСТЯЖКИ (stretch, scaleY), не только оттяжки (rubber).
const v2 = el(688, 3000);
const i2 = el(0, 0);
const s2 = new mod.Scroller(v2, i2, { feel: "syrup", overscroll: "stretch", stick: false });
const scale = () => { const m = String(i2.style.transform || "").match(/scaleY\(([\d.]+)\)/); return m ? parseFloat(m[1]) : 0; };
const wheel2 = (dy) => v2.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: v2, preventDefault() {} });
s2.scrollTo(0, false);
wheel2(0); advance(200);
for (let i = 0; i < 40; i++) { wheel2(-12.5); advance(16); }
assert.ok(scale() > 1.01, `кейс E: растяжка видна (scaleY ${scale().toFixed(3)})`);
advance(1500); // палец замер — фиксация и в растяжке
assert.ok(!scale() || scale() <= 1.001,"кейс E: тишина не оставляет растяжку");
wheel2(0); advance(2000);
assert.ok(!scale() || scale() <= 1.001, `кейс E: после нуля растяжка вернулась (scaleY ${scale()})`);
s2.scrollTo(1500, false);
for (let i = 0; i < 20; i++) { wheel2(-120); advance(16); }
wheel2(0); advance(16);
for (const d of [-60, -48, -38, -30, -24, -19, -15, -12, -9, -7, -5, -4, -3, -2, -2, -1]) { wheel2(d); advance(16); }
advance(2500);
assert.equal(v2.scrollTop, 0, `кейс E: парковка импульса у края (осталось ${v2.scrollTop})`);
assert.ok(!scale() || scale() <= 1.001, `кейс E: без остаточной растяжки (scaleY ${scale()})`);
console.log(`scroller: растяжка — фиксация/возврат/парковка ok`);
