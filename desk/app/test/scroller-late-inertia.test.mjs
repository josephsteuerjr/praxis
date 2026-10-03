// 03.10, живой случай: «не отпуская резко оттянул — отпустил: дёргано и висит».
// После сильного рывка Windows стартует инерцию с опозданием > INERTIA_GAP:
// первая дельта не имеет права объявляться «пальцами» и срывать возврат.
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

// Сильный рывок у края без отпускания, потом отпуск и ОПОЗДАВШАЯ инерция.
s.scrollTo(0, false); advance(64);
zero(400);
wheel(-30); wheel(-50); wheel(-90);
wheel(-120); wheel(-160); wheel(-140); wheel(-90); // рывок и торможение пальцами
assert.ok(shift() > 20, `оттяжка от рывка видна (${shift().toFixed(1)})`);
zero();
advance(80); // инерция опаздывает: Windows после сильного жеста
wheel(-120);
wheel(-90); wheel(-60); wheel(-40); wheel(-20); wheel(-8); wheel(-3);
advance(600);
assert.ok(shift() < 8, `опоздавшая инерция не сорвала возврат (осталось ${shift().toFixed(1)})`);
advance(2400);
assert.equal(shift(), 0, "возврат дошёл до конца — ничего не висит");
assert.equal(view.scrollTop, 0, "лента стоит у края");

// Тот же рывок, но пальцы ВЕРНУЛИСЬ через паузу и тянут дальше: подхват ловится
// со второго события (пальцы держат скорость — инерция так не умеет).
s.scrollTo(0, false); advance(64);
zero(400);
wheel(-30); wheel(-50); wheel(-90); wheel(-120); wheel(-90);
zero();
advance(120);
wheel(-90, 130); // опоздавшее событие — кандидат (инерция или пальцы?)
wheel(-95); // держит скорость — это пальцы
for (let i = 0; i < 30; i++) wheel(-40);
advance(200);
assert.ok(shift() > 30, `подхват после паузы тянет дальше (${shift().toFixed(1)})`);
zero();
advance(2000);
assert.equal(shift(), 0, "отпустили — вернулось до конца");

console.log("scroller: опоздавшая инерция не срывает возврат, подхват ловится — ok");
