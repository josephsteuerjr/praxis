// Exact event times, cancellable frames/timers. No browser or child processes.
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
const source = readFileSync(process.env.SCROLLER_SOURCE || new URL("../../../ui-kit/feed/scroller.ts", import.meta.url), "utf8");
const { Scroller } = await import("data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64"));

export function fixture(options = {}) {
  const { frameMs = 16, ...scrollerOptions } = options;
  let clock = 1000, id = 0;
  const jobs = new Map();
  const schedule = (fn, delay, frame = false) => {
    const key = ++id;
    jobs.set(key, { fn, at: clock + delay, frame });
    return key;
  };
  globalThis.performance = { now: () => clock };
  globalThis.requestAnimationFrame = fn => schedule(fn, frameMs, true);
  globalThis.cancelAnimationFrame = key => jobs.delete(key);
  globalThis.setTimeout = (fn, ms) => schedule(fn, ms);
  globalThis.clearTimeout = key => jobs.delete(key);
  const windowHandlers = {};
  globalThis.window = { setTimeout, clearTimeout,
    addEventListener: (type, fn) => { windowHandlers[type] = fn; },
    removeEventListener: type => { delete windowHandlers[type]; } };
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.getComputedStyle = () => ({ overflowY: "visible" });
  globalThis.HTMLElement = class {};
  function el(height, scrollHeight) {
    const handlers = {};
    return { clientHeight: height, scrollHeight, scrollTop: 0, style: {}, isConnected: true, handlers,
      addEventListener: (name, fn) => { handlers[name] = fn; }, removeEventListener() {},
      closest: () => null, hasPointerCapture: () => false, setPointerCapture() {}, releasePointerCapture() {},
      clientWidth: 800, getBoundingClientRect: () => ({ left: 0, top: 0 }), focus() {},
      classList: { add() {}, remove() {}, toggle() {} },
    };
  }
  const view = el(688, 3000), inner = el(0, 0);
  const s = new Scroller(view, inner, { feel: "syrup", overscroll: "rubber", stick: false, canGrab: () => true, ...scrollerOptions });
  function advance(ms) {
    const end = clock + ms;
    for (;;) {
      const next = [...jobs].filter(([, j]) => j.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      clock = next[1].at;
      jobs.delete(next[0]);
      next[1].fn(next[1].frame ? clock : undefined);
    }
    clock = end;
  }
  function wheel(dy, dt = 16, extra = {}) {
    advance(dt);
    view.handlers.wheel({ deltaY: dy, deltaX: 0, deltaMode: 0, ctrlKey: false,
      defaultPrevented: false, target: view, preventDefault() {}, ...extra });
  }
  function pointer(type, y = 100, extra = {}) {
    view.handlers[type]?.({type,pointerId:1,pointerType:'mouse',button:0,buttons:type==='pointerup'?0:1,
      clientX:100,clientY:y,timeStamp:clock,target:view,preventDefault(){},...extra});
  }
  function contact(contacts, available = true) {
    windowHandlers['helene-touchpad-contact']?.({detail:{contacts,available}});
  }
  return { s, view, inner, advance, wheel, pointer, contact, windowHandlers, shown: () => Math.abs(s.debug().shown) };
}
