// Тонкая настройка окна (слово Егора 28.09: «а может, добавить всё-всё-всё? кастомизация»).
// Пресеты — точка старта; каждый ползунок правит живое окно сразу. Выбранное хранится в
// localStorage и уходит строкой PREFS в консоль — оболочка лаборатории пишет её в
// prefs.json, оттуда берутся значения по умолчанию для настоящего окна.
import { FEELS, type Feel, type FeelName, type Scroller } from "../../ui-kit/feed/scroller";
import { growTiming } from "../../ui-kit/feed/keyed";
import type { Zoom } from "../../ui-kit/feed/zoom";

interface Knob {
  id: string;
  label: string;
  min: number;
  max: number;
  step: number;
  def: number;
  fmt: (v: number) => string;
  apply: (v: number) => void;
}

interface Swatch {
  id: string;
  label: string;
  options: Array<{ name: string; color: string; vars: Record<string, string> }>;
}

const root = document.documentElement;
const setVar = (k: string, v: string) => root.style.setProperty(k, v);

const ms = (v: number) => `${Math.round(v)} мс`;
const px = (v: number) => `${v % 1 ? v.toFixed(1) : v} px`;
const pct = (v: number) => `${Math.round(v * 100)}%`;

export function mountTuning(box: HTMLElement, scroller: Scroller, zoom: Zoom, feelName: () => FeelName) {
  const saved = load();
  const values: Record<string, number | string> = { ...saved };

  const feelKnob = (id: keyof Feel, label: string, min: number, max: number, step: number, fmt: (v: number) => string): Knob => ({
    id: "feel." + id, label, min, max, step, def: FEELS.syrup[id], fmt,
    apply: (v) => { scroller.feel = { ...scroller.feel, [id]: v }; },
  });

  const MOTION: Knob[] = [
    feelKnob("rubberD", "Размах резинки (предел, доля окна)", 0.08, 0.6, 0.01, pct),
    feelKnob("rubberC", "Податливость резинки", 0.2, 1.2, 0.05, (v) => v.toFixed(2)),
    feelKnob("holdMs", "Держать оттяжку тачпадом", 0, 700, 10, ms),
    { id: "feel.springMs", label: "Возврат с края (мягкость)", min: 200, max: 1400, step: 10, def: Math.round(4 / FEELS.syrup.springW), fmt: ms,
      apply: (v) => { scroller.feel = { ...scroller.feel, springW: 4 / v }; } },
    feelKnob("flingTau", "Накат после броска", 150, 1000, 10, ms),
    feelKnob("maxFling", "Предел скорости броска", 3, 14, 0.5, (v) => `${v} px/мс`),
    feelKnob("wheelTau", "Плавность колеса мыши", 0, 350, 5, ms),
    feelKnob("padTau", "Плавность тачпада", 0, 100, 1, ms),
    feelKnob("followTau", "Езда за новыми сообщениями", 40, 450, 5, ms),
    { id: "grow", label: "Появление сообщения", min: 0, max: 1000, step: 10, def: 560, fmt: ms, apply: (v) => { growTiming.ms = v; } },
    { id: "zoomTau", label: "Плавность масштаба", min: 0, max: 250, step: 5, def: 95, fmt: ms, apply: (v) => { zoom.tau = v; } },
  ];

  const LOOK: Knob[] = [
    { id: "feedBase", label: "Размер текста ленты", min: 13, max: 21, step: 0.5, def: 16, fmt: px, apply: (v) => setVar("--u-feed-base", v + "px") },
    { id: "lh", label: "Межстрочный", min: 1.3, max: 2, step: 0.05, def: 1.6, fmt: (v) => v.toFixed(2), apply: (v) => setVar("--u-lh", String(v)) },
    { id: "col", label: "Ширина колонки", min: 32, max: 80, step: 1, def: 52, fmt: (v) => `${v} em`, apply: (v) => setVar("--u-col", v + "em") },
    { id: "lw", label: "Толщина линий", min: 0.75, max: 3, step: 0.25, def: 1.5, fmt: px, apply: (v) => setVar("--u-lw", v + "px") },
    { id: "rBubble", label: "Скругление пузырей", min: 0.2, max: 2, step: 0.05, def: 1.3, fmt: (v) => `${v.toFixed(2)} em`, apply: (v) => setVar("--u-r-bubble", v + "em") },
    { id: "rBox", label: "Скругление поля ввода", min: 4, max: 32, step: 1, def: 22, fmt: px, apply: (v) => setVar("--u-r-box", v + "px") },
    { id: "title", label: "Заголовок почерком", min: 24, max: 64, step: 1, def: 40, fmt: px, apply: (v) => setVar("--u-title", v + "px") },
    { id: "name", label: "Имя агента почерком", min: 20, max: 56, step: 1, def: 34, fmt: px, apply: (v) => setVar("--u-name", v + "px") },
    { id: "sun", label: "Большой кружок", min: 0, max: 90, step: 1, def: 50, fmt: px, apply: (v) => setVar("--u-sun", v + "px") },
    { id: "sunSmall", label: "Маленький кружок", min: 0, max: 30, step: 1, def: 14, fmt: px, apply: (v) => setVar("--u-sun-small", v + "px") },
    { id: "grainL", label: "Зерно бумаги днём", min: 0, max: 0.3, step: 0.01, def: 0.1, fmt: pct, apply: (v) => setVar("--u-grain-l", String(v)) },
    { id: "grainD", label: "Зерно бумаги ночью", min: 0, max: 0.3, step: 0.01, def: 0.07, fmt: pct, apply: (v) => setVar("--u-grain-d", String(v)) },
  ];

  const SWATCHES: Swatch[] = [
    { id: "accent", label: "Акцент", options: [
      { name: "терракота", color: "#e0915a", vars: { "--u-acc": "#e0915a", "--u-acc-ink-l": "#9a4a1e", "--u-acc-ink-d": "#eda676" } },
      { name: "охра", color: "#d9a441", vars: { "--u-acc": "#d9a441", "--u-acc-ink-l": "#7d5a12", "--u-acc-ink-d": "#e8c170" } },
      { name: "шалфей", color: "#8fae8b", vars: { "--u-acc": "#8fae8b", "--u-acc-ink-l": "#3f6a3b", "--u-acc-ink-d": "#a9c9a4" } },
      { name: "слива", color: "#b07a9e", vars: { "--u-acc": "#b07a9e", "--u-acc-ink-l": "#7a3f68", "--u-acc-ink-d": "#d3a3c4" } },
      { name: "море", color: "#6f9fb5", vars: { "--u-acc": "#6f9fb5", "--u-acc-ink-l": "#2f5f75", "--u-acc-ink-d": "#9cc3d6" } },
      { name: "киноварь", color: "#d9674e", vars: { "--u-acc": "#d9674e", "--u-acc-ink-l": "#9a3420", "--u-acc-ink-d": "#ee9a86" } },
    ] },
    { id: "paperL", label: "Бумага днём", options: [
      { name: "как в референсе", color: "#f4e4cf", vars: { "--u-paper-l": "#f4e4cf" } },
      { name: "светлее", color: "#f8eee0", vars: { "--u-paper-l": "#f8eee0" } },
      { name: "почти белая", color: "#fbf7f0", vars: { "--u-paper-l": "#fbf7f0" } },
      { name: "теплее", color: "#efd8bb", vars: { "--u-paper-l": "#efd8bb" } },
      { name: "серая", color: "#ece8e1", vars: { "--u-paper-l": "#ece8e1" } },
    ] },
    { id: "paperD", label: "Бумага ночью", options: [
      { name: "тёплый уголь", color: "#1d1914", vars: { "--u-paper-d": "#1d1914" } },
      { name: "глубже", color: "#15120f", vars: { "--u-paper-d": "#15120f" } },
      { name: "мягче", color: "#26211b", vars: { "--u-paper-d": "#26211b" } },
      { name: "холоднее", color: "#1a1b1d", vars: { "--u-paper-d": "#1a1b1d" } },
    ] },
  ];

  const section = (title: string, inner: string, open = false) =>
    `<details class="tune"${open ? " open" : ""}><summary>${title}</summary><div class="tune-body">${inner}</div></details>`;
  const knobHTML = (k: Knob) =>
    `<div class="knob"><label for="k-${k.id}">${k.label}</label><output id="o-${k.id}"></output>` +
    `<input id="k-${k.id}" type="range" min="${k.min}" max="${k.max}" step="${k.step}" data-knob="${k.id}"></div>`;
  const swatchHTML = (w: Swatch) =>
    `<div class="swatches" data-swatch="${w.id}"><span>${w.label}</span>${w.options
      .map((o, i) => `<button type="button" class="swatch" data-i="${i}" title="${o.name}" style="background:${o.color}"></button>`)
      .join("")}</div>`;

  box.innerHTML =
    section("Движение", MOTION.map(knobHTML).join("") + `<button type="button" class="tune-reset" data-reset="motion">вернуть пресет</button>`, true) +
    section("Вид", SWATCHES.map(swatchHTML).join("") + LOOK.map(knobHTML).join("") + `<button type="button" class="tune-reset" data-reset="look">вернуть как было</button>`);

  const all = [...MOTION, ...LOOK];
  const show = (k: Knob, v: number) => {
    const inp = box.querySelector<HTMLInputElement>(`[data-knob="${k.id}"]`)!;
    inp.value = String(v);
    box.querySelector<HTMLOutputElement>(`#o-${CSS.escape(k.id)}`)!.textContent = k.fmt(v);
  };
  const setKnob = (k: Knob, v: number, remember: boolean) => {
    k.apply(v);
    show(k, v);
    if (remember) { values[k.id] = v; save(values); }
  };
  const setSwatch = (w: Swatch, i: number, remember: boolean) => {
    const o = w.options[i] ?? w.options[0];
    for (const [k, v] of Object.entries(o.vars)) setVar(k, v);
    for (const b of box.querySelectorAll<HTMLButtonElement>(`[data-swatch="${w.id}"] .swatch`)) b.classList.toggle("on", Number(b.dataset.i) === i);
    if (remember) { values["sw." + w.id] = i; save(values); }
  };

  /** Движение с нуля — от пресета; сохранённые ползунки поверх. */
  const motionFromPreset = (keepSaved: boolean) => {
    scroller.feel = { ...FEELS[feelName()] };
    for (const k of MOTION) {
      const preset = k.id.startsWith("feel.")
        ? k.id === "feel.springMs" ? Math.round(4 / scroller.feel.springW) : (scroller.feel as unknown as Record<string, number>)[k.id.slice(5)]
        : k.def;
      const v = keepSaved && typeof values[k.id] === "number" ? (values[k.id] as number) : preset;
      if (!keepSaved) delete values[k.id];
      setKnob(k, v, false);
    }
    save(values);
  };

  for (const k of all) {
    box.querySelector<HTMLInputElement>(`[data-knob="${k.id}"]`)!.addEventListener("input", (e) => setKnob(k, Number((e.target as HTMLInputElement).value), true));
  }
  for (const w of SWATCHES) {
    for (const b of box.querySelectorAll<HTMLButtonElement>(`[data-swatch="${w.id}"] .swatch`)) b.addEventListener("click", () => setSwatch(w, Number(b.dataset.i), true));
  }
  box.querySelector('[data-reset="motion"]')!.addEventListener("click", () => motionFromPreset(false));
  box.querySelector('[data-reset="look"]')!.addEventListener("click", () => {
    for (const k of LOOK) { delete values[k.id]; setKnob(k, k.def, false); }
    for (const w of SWATCHES) { delete values["sw." + w.id]; setSwatch(w, 0, false); }
    save(values);
  });

  motionFromPreset(true);
  for (const k of LOOK) setKnob(k, typeof values[k.id] === "number" ? (values[k.id] as number) : k.def, false);
  for (const w of SWATCHES) setSwatch(w, typeof values["sw." + w.id] === "number" ? (values["sw." + w.id] as number) : 0, false);

  return {
    /** Пресет характера сменился — ползунки движения встают на него. */
    presetChanged: () => motionFromPreset(false),
  };
}

function load(): Record<string, number | string> {
  try { return JSON.parse(localStorage.getItem("lab.tune") || "{}"); } catch { return {}; }
}

function save(v: Record<string, number | string>) {
  try { localStorage.setItem("lab.tune", JSON.stringify(v)); } catch { /* без хранилища — только до перезапуска */ }
  clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => console.log("PREFS " + JSON.stringify(snapshot())), 400);
}
let saveTimer = 0;

/** Всё выбранное — одним снимком для prefs.json. */
function snapshot(): Record<string, unknown> {
  const out: Record<string, unknown> = { at: new Date().toISOString() };
  try {
    for (const k of ["over", "feel", "theme", "hand", "text"]) out[k] = localStorage.getItem("lab." + k);
    out.tune = JSON.parse(localStorage.getItem("lab.tune") || "{}");
    out.zoom = localStorage.getItem("lab.zoom");
  } catch { /* */ }
  return out;
}

export function reportPrefs() {
  console.log("PREFS " + JSON.stringify(snapshot()));
}
