// Живые журналы тачпада владельца (лаборатория, 03.10): фрагменты дословно.
// Три живых факта: после нуля инерция стартует с мелкой дельты и РАЗГОНЯЕТСЯ
// до скорости жеста (−4 → −81 при пике 106) и гладко тает; тяга-дребезг
// (−5 −6 −13 −10 −19…) нарастает неровно; рывок после микропаузы хвоста
// (−2 [50мс] −40 −36 −64…) — это пальцы.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

let clock = 0;
const timers = [];
const frames = [];
globalThis.window = { setTimeout: (fn, ms) => { const t = { fn, at: clock + ms }; timers.push(t); return t; }, clearTimeout: (t) => { if (t) t.fn = null; } };
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
const wheel = (dy, dt = 17) => { advance(dt); view.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: view, preventDefault() {} }); };
const live = (rows) => { for (const [dy, dt] of rows) wheel(dy, dt ?? 17); };

// --- 1. «ОНО ВИСЕЛО, ПОКА Я НЕ ПОДВИГАЛ» (журнал 03.10, вечер, дословно):
// рывок, ноль, разгон инерции −4 → −81 (не выше пика 106!), две секунды
// гладкого затухания до −1 с паузами — и тишина. Ничего не должно висеть.
s.scrollTo(0, false); advance(64);
live([[-2, 400], [-29], [-74], [-106], [-90], [0], [-4], [-81], [-81], [-67], [-61], [-55], [-51], [-47], [-44], [-40], [-37], [-35], [-32], [-31], [-30], [-27], [-27], [-25], [-23], [-23], [-22], [-20], [-20], [-19], [-17], [-34, 50], [-32], [-14], [-14], [-13], [-13], [-13], [-12], [-11], [-11], [-11], [-10], [-10], [-10], [-9], [-17], [-8], [-8], [-7], [-8], [-7], [-7], [-6], [-6], [-6], [-6], [-5], [-6], [-5], [-5], [-5], [-4], [-4], [-5], [-4], [-3], [-4], [-4], [-3], [-3], [-3], [-3], [-3], [-2], [-3], [-2], [-2], [-3], [-2], [-1], [-2], [-2], [-2], [-1], [-2], [-1], [-1], [-2], [-1], [-1], [-1], [-1], [-1], [-1], [-1], [-1], [-1], [-1, 67], [-1, 233]]);
advance(3100); // тишина «висело, пока не подвигал»
assert.equal(shift(), 0, `1) разгон инерции не вкачивается в оттяжку — ничего не висит (${shift().toFixed(1)})`);
assert.equal(view.scrollTop, 0, "1) лента стоит у края");

// --- 2. Микронуль посреди тяги, продолжение НЕ выше пика — тоже инерция
// (первый журнал 03.10: −18 −64 −154 −124 [ноль] −6 −148 −123 −104…): парковка.
s.scrollTo(0, false); advance(64);
live([[-18, 400], [-64], [-154], [-124], [0], [-6], [-148], [-123], [-104], [-92], [-88], [-82], [-78]]);
advance(600);
assert.ok(shift() < 5, `2) микронуль с продолжением ниже пика — инерция, парковка (${shift().toFixed(1)})`);

// --- 3. Живой нарастающий дребезг тяги: −5 −6 −13 −10 −19 −17 −15 −24…
// Неровный рост двух событий подряд — тяга, лента оттягивается.
s.scrollTo(0, false); advance(64);
live([[-4, 400], [-4], [-5, 117], [-6], [-13], [-10], [-19], [-17], [-15], [-24], [-17], [-20], [-32], [-21], [-21], [-34]]);
advance(64); // отклик во время ввода, до нового отпускания по тишине
assert.ok(shift() > 20, `3) нарастающий дребезг — это тяга (${shift().toFixed(1)} px)`);
wheel(0);
advance(2000);
assert.equal(shift(), 0, "3) отпустили — вернулось");

// --- 4. Живой рывок после микропаузы хвоста: −2 [пауза 50] −40 −36 −64 −78…
s.scrollTo(0, false); advance(64);
live([[-1, 400], [-1], [-2], [-2], [-40, 50], [-36], [-64], [-47], [-50], [-78], [-53], [-51], [-63]]);
advance(64);
assert.ok(shift() > 20, `4) рывок после микропаузы ловится пальцами (${shift().toFixed(1)} px)`);
wheel(0);
advance(2000);
assert.equal(shift(), 0, "4) отпустили — вернулось");

// --- 5. Живой «в конце»: тачпад «крутит быстрее» ОДНИМ событием посреди
// гладкой инерции (−46 → −86 → −39…): всплеск — кандидат, просевшее следующее
// гасит его. Инерция глотается, тишина ничего не держит.
s.scrollTo(0, false); advance(64);
live([[-18, 400], [-64], [-106], [-90], [0], [-110], [-122], [-95], [-70], [-46], [-86], [-39], [-38], [-35], [-30], [-24], [-16], [-9], [-5], [-3]]);
advance(3000);
assert.equal(shift(), 0, `5) вспышка тачпада не вкачивается в оттяжку (${shift().toFixed(1)})`);
assert.equal(view.scrollTop, 0, "5) парковка у края");

// --- 6. Живой «висело до подвигал» (журнал медленных тяг): короткий гладкий
// рывок с затуханием и ТИШИНА 1.7 с без нуля — возвращается сама, не висит.
s.scrollTo(3000, false); advance(64); // нижний край, как в живой сцене
const dw = (dy, dt = 17) => { advance(dt); view.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false, defaultPrevented: false, target: view, preventDefault() {} }); };
dw(1, 400); dw(31, 400); dw(52); dw(31); dw(37); dw(20); dw(12); dw(6); dw(1);
advance(1600); // тишина дольше HOLD_LOST
assert.ok(Math.abs(shift()) < 5, `6) рывок с потерянным нулём вернулся сам (${shift().toFixed(1)})`);

console.log("scroller-живой: разгон инерции, микронуль, дребезг тяги, рывок после паузы, вспышка тачпада, потерянный ноль — ok");
