// Весь режим оттяжки живыми сериями Windows PTad: кратные 120 дельты на разгоне,
// ноль на подъёме, инерция с дребезгом и пачками, запинки между событиями.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

let clock = 0;
const timers = [];
const frames = [];
globalThis.window = { setTimeout: (fn, ms) => { const t = { fn, at: clock + ms }; timers.push(t); return t; }, clearTimeout: (t) => { if (t) t.fn = null; } };
// scroller зовёт и глобальный clearTimeout — подменять оба (ловушка 02.10).
globalThis.clearTimeout = (t) => { if (t) t.fn = null; };
globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return 1; };
globalThis.cancelAnimationFrame = () => {};
globalThis.performance = { now: () => clock };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.getComputedStyle = () => ({ overflowY: "visible" });
globalThis.HTMLElement = class {};
globalThis.document = { createRange: () => ({ selectNodeContents() {}, getClientRects: () => [] }) };

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

function el(h, sh) {
  const handlers = {};
  return {
    clientHeight: h, scrollHeight: sh, scrollTop: 0, style: {}, isConnected: true,
    addEventListener: (t, fn) => { handlers[t] = fn; }, removeEventListener() {},
    hasPointerCapture: () => false, closest: () => null, handlers,
    classList: { remove() {}, add() {}, toggle() {} },
  };
}

const view = el(688, 3000);
const inner = el(0, 0);
const s = new mod.Scroller(view, inner, { feel: "syrup", overscroll: "rubber", stick: false });
const shift = () => { const m = String(inner.style.transform || "").match(/translate3d\(0(?:px)?, (-?[\d.]+)px/); return m ? parseFloat(m[1]) : 0; };
const wheel = (dy, dt = 16) => { advance(dt); view.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: view, preventDefault() {} }); };
const zero = (dt = 16) => wheel(0, dt);

function fresh(top = 688) {
  s.scrollTo(top, false);
  advance(64);
}

// --- 1. Быстрый флинг вверх: разгон весь из кратных 120, ноль, инерция убывает
// с ЖИВЫМ дребезгом (ступени роста до 11 — живой журнал 03.10; больше инерция
// не даёт: покадровый прыжок 14+ — это продолжение тяги, не инерция).
fresh();
wheel(-113, 400);
for (let i = 0; i < 11; i++) wheel(-120);
zero();
wheel(-90); wheel(-70); wheel(-81); wheel(-78); wheel(-69); wheel(-58); wheel(-45); wheel(-26); wheel(-15); wheel(-15); wheel(-26); wheel(-13); wheel(-12); wheel(-10); wheel(-7);
advance(1600);
assert.equal(view.scrollTop, 0, "1) флинг с дребезгом паркуется у верхнего края");
assert.equal(shift(), 0, "1) оттяжки не осталось");

// --- 2. Та же парковка, но в инерции запинка 150 мс и следующая дельта кратна 120.
fresh();
wheel(-113, 400);
for (let i = 0; i < 11; i++) wheel(-120);
zero();
wheel(-90); wheel(-70);
wheel(-120, 150); // запинка дольше STREAM_GAP, дельта кратная — не должна стать «щелчком мыши»
wheel(-80); wheel(-50); wheel(-30); wheel(-10); wheel(-2);
advance(900);
assert.equal(view.scrollTop, 0, "2) запинка в инерции не ломает парковку");
assert.equal(shift(), 0, "2) оттяжка не зависла");

// --- 3. Инерция с пачкой: после запинки одна дельта выше последней — Windows
// слил кадры; пачка идёт ПОСЛЕ паузы, и слитая дельта не выше двух кадров прошлой.
fresh();
wheel(-113, 400);
for (let i = 0; i < 8; i++) wheel(-120);
zero();
wheel(-90); wheel(-150, 60); wheel(-100); wheel(-70); wheel(-45); wheel(-25); wheel(-12); wheel(-5); wheel(-1);
advance(900);
assert.equal(view.scrollTop, 0, "3) пачка в инерции паркуется");
assert.equal(shift(), 0, "3) пачка не оставляет оттяжку");

// --- 4. Висящий случай 02.10 дословно: лента стоит точно в краю, разгон кратными,
// ноль, инерция. Возврат пальцами сразу после нуля — подхват без скачка.
fresh(0);
wheel(-113, 400);
for (let i = 0; i < 12; i++) wheel(-120);
zero();
wheel(-60); wheel(-40);
advance(250); // время до нового контакта, включая прежнюю 50мс паузу перед wheel
const mid = shift();
wheel(-6, 0); wheel(-10); wheel(-16); wheel(-25); // пальцы снова легли: скорость копится с нуля
advance(100);
// Сравниваем с положением при КАСАНИИ, не за 50 мс до него: до контакта
// лента свободно возвращалась. Пружина подхвата сохраняет скорость.
assert.ok(shift() > mid - 10, `4) подхват держит пойманное место (${shift().toFixed(1)} из ${mid.toFixed(1)})`);
for (let i = 0; i < 30; i++) wheel(-20);
advance(64); // измеряем продолжение тяги, до завершения ввода по тишине
assert.ok(shift() > 40, `4) постоянный ввод продолжает оттяжку (${shift().toFixed(1)})`);
zero();
advance(2000);
assert.equal(shift(), 0, "4) отпустили — тягуче вернулось");

// --- 5. После РЫВКА тишина — возврат сам (живой журнал 03.10: гладкое затухание
// +31 +52…+1 и тишина 1.7 с без нуля висела до «подвигал»). По уточнению
// 03.10 после медленной тяги тишина тоже обязана завершать оттяжку.
fresh();
wheel(0, 400);
advance(84);
wheel(-113);
let hitPeak = Math.abs(shift());
for (let i = 0; i < 14; i++) {wheel(-120);hitPeak=Math.max(hitPeak,Math.abs(shift()));}
assert.ok(hitPeak>2,"5) край видимо принял быстрый импульс");
advance(400);
assert.ok(Math.abs(shift())<hitPeak,"5) быстрый импульс поглощается без удержания большого перетяга");
advance(1200); // тишина — ноль потерялся
assert.ok(Math.abs(shift()) < 5, `5) после рывка тишина — вернулось само (${shift().toFixed(1)})`);
// Медленная тяга (пик ниже 30) — тоже возвращается:
s.scrollTo(0, false); advance(64);
wheel(0, 400); advance(84);
for (let i = 0; i < 40; i++) wheel(-12.5);
advance(1500);
assert.equal(shift(),0,"5) медленная тяга тоже завершается по тишине без нуля");
zero();
advance(2000);
assert.equal(shift(), 0, "5) после нуля оттяжка вернулась");

// --- 6. Второй ноль (конец инерции), затем СРАЗУ новый жест вниз — лента едет
// с места остановки, не прыгает и не висит.
fresh(0);
wheel(-113, 400);
for (let i = 0; i < 10; i++) wheel(-120);
zero(); wheel(-90); wheel(-50); wheel(-20); wheel(-5); zero();
advance(100);
wheel(40, 60); wheel(60); wheel(80); wheel(50); wheel(30);
advance(800); // отпуск подтверждается тишиной (90 мс) — возврату нужно время доехать
assert.ok(view.scrollTop > 100, `6) новый жест вниз едет с места остановки (${view.scrollTop})`);
assert.equal(shift(), 0, "6) без оттяжки внизу");

// --- 7. Растяжка: то же самое паркуется и держится.
const v2 = el(688, 3000), i2 = el(0, 0);
const st = new mod.Scroller(v2, i2, { feel: "syrup", overscroll: "stretch", stick: false });
st.scrollTo(688, false); advance(64);
const w2 = (dy, dt = 16) => { advance(dt); v2.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: v2, preventDefault() {} }); };
const scale = () => { const m = String(i2.style.transform || "").match(/scaleY\(([\d.]+)\)/); return m ? parseFloat(m[1]) : 1; };
w2(-113, 400);
for (let i = 0; i < 12; i++) w2(-120);
w2(0);
w2(-90); w2(-70); w2(-81); w2(-55); w2(-30); w2(-10); w2(-3); w2(-1);
advance(900);
assert.equal(v2.scrollTop, 0, "7) растяжка: парковка у края");
assert.equal(scale(), 1, "7) растяжка сложилась обратно");

console.log("scroller-режим: парковка с дребезгом/пачками/запинками, подхват, фиксация, растяжка — ok");
