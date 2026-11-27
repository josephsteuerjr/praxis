import { t as uiText } from "../../lang/lang";
// «Вид» — облик и физика окна на выбор владельца (владелец 28.09: «добавить в настройки
// визуала и всю палитру цветов фона… и базовые настройки физики (несколько вариантов)»;
// ползунков — нет, «мне не нужно 12 ползунков»: только варианты).
//
// Хранится в localStorage окна: это удобство этого окна на этом компьютере, а не
// настройка агента, и в helene.json ей делать нечего. Применяется сразу, без «Сохранить».
// Тема по-прежнему — как в системе (слово владельца 07.09); палитры — у дня и у ночи свои.
import "./styles/look-variants.css";
import type { FeelName, Overscroll } from "../feed/scroller";
import { scrollSupportText, type ScrollSupport } from "../feed/scroll-support";
import { el } from "./lib";
import { electron } from "./api";
import * as scroll from "./scroll";
import { applyTheme, readTheme } from "../dom";
import { customizationCard } from "./look-customization";

interface Choice { id: string; name: string }
interface Paper extends Choice { color: string }
interface Accent extends Choice { fill: string; inkDay: string; inkNight: string }

/** Бумага днём: тон фона. Первый — референс владельца (`design-refs-2809/cat-paws.jpg`). */
export const PAPER_DAY: Paper[] = [
  { id: "warm", get name() { return uiText("ui.ui.kit.window.look.6938395185"); }, color: "#f4e4cf" },
  { id: "light", get name() { return uiText("ui.ui.kit.window.look.ac7e899bd3"); }, color: "#f8eee0" },
  { id: "white", get name() { return uiText("ui.ui.kit.window.look.598217e88c"); }, color: "#fbf7f0" },
  { id: "kraft", get name() { return uiText("ui.ui.kit.window.look.723ff80038"); }, color: "#ecd4b4" },
  { id: "peach", get name() { return uiText("ui.ui.kit.window.look.48a1368cd3"); }, color: "#f6ded0" },
  { id: "linen", get name() { return uiText("ui.ui.kit.window.look.f2d58c4cc7"); }, color: "#eee8dc" },
  { id: "sage", get name() { return uiText("ui.ui.kit.window.look.b3fa5ae774"); }, color: "#e7ebdf" },
  { id: "sky", get name() { return uiText("ui.ui.kit.window.look.a0fde594ed"); }, color: "#e6ecef" },
];

/** Бумага ночью: та же палитра в темноте, не инверсия. */
export const PAPER_NIGHT: Paper[] = [
  { id: "coal", get name() { return uiText("ui.ui.kit.window.look.c0e2a255fd"); }, color: "#1d1914" },
  { id: "deep", get name() { return uiText("ui.ui.kit.window.look.280f470538"); }, color: "#14110e" },
  { id: "cocoa", get name() { return uiText("ui.ui.kit.window.look.206522bddd"); }, color: "#251d18" },
  { id: "graphite", get name() { return uiText("ui.ui.kit.window.look.4807faef19"); }, color: "#1b1c1e" },
  { id: "navy", get name() { return uiText("ui.ui.kit.window.look.10f77232f9"); }, color: "#151a22" },
  { id: "pine", get name() { return uiText("ui.ui.kit.window.look.e33c218ac0"); }, color: "#161c18" },
];

/** Акцент: заливка (кнопка отправки, точки) и цвет текста-ссылок с контрастом ≥ 4,5 днём и ночью. */
export const ACCENTS: Accent[] = [
  { id: "terra", get name() { return uiText("ui.ui.kit.window.look.dcd6e2b7d9"); }, fill: "#e0915a", inkDay: "#9a4a1e", inkNight: "#eda676" },
  { id: "ochre", get name() { return uiText("ui.ui.kit.window.look.a69b1298db"); }, fill: "#d9a441", inkDay: "#7a5610", inkNight: "#e8c170" },
  { id: "sage", get name() { return uiText("ui.ui.kit.window.look.b3fa5ae774"); }, fill: "#8fae8b", inkDay: "#3f6a3b", inkNight: "#a9c9a4" },
  { id: "plum", get name() { return uiText("ui.ui.kit.window.look.4831c44fb7"); }, fill: "#b07a9e", inkDay: "#7a3f68", inkNight: "#d3a3c4" },
  { id: "sea", get name() { return uiText("ui.ui.kit.window.look.fa8e72e051"); }, fill: "#6f9fb5", inkDay: "#2d5d73", inkNight: "#9cc3d6" },
  { id: "cinnabar", get name() { return uiText("ui.ui.kit.window.look.5187a60ac4"); }, fill: "#d9674e", inkDay: "#9a3420", inkNight: "#ee9a86" },
];

export const GRAIN: Array<Choice & { day: number; night: number }> = [
  { id: "none", get name() { return uiText("ui.ui.kit.window.look.66818f25f4"); }, day: 0, night: 0 },
  { id: "soft", get name() { return uiText("ui.ui.kit.window.look.1361e61db2"); }, day: 0.06, night: 0.04 },
  { id: "paper", get name() { return uiText("ui.ui.kit.window.look.9b36d26c5c"); }, day: 0.1, night: 0.06 },
  { id: "rough", get name() { return uiText("ui.ui.kit.window.look.e7f6df3d98"); }, day: 0.16, night: 0.1 },
];

export const TEXT: Array<Choice & { px: number }> = [
  { id: "s", get name() { return uiText("ui.ui.kit.window.look.2a96d20118"); }, px: 14.5 },
  { id: "m", get name() { return uiText("ui.ui.kit.window.look.7c0ef1469b"); }, px: 15.5 },
  { id: "l", get name() { return uiText("ui.ui.kit.window.look.c238cb4a2e"); }, px: 17 },
  { id: "xl", get name() { return uiText("ui.ui.kit.window.look.8e7f1c567e"); }, px: 18.5 },
];

export const FEELS: Array<Choice & { id: FeelName; hint: string }> = [
  { id: "brisk", get name() { return uiText("ui.ui.kit.window.look.696f163be1"); }, get hint() { return uiText("ui.ui.kit.window.look.becc31db10"); } },
  { id: "smooth", get name() { return uiText("ui.ui.kit.window.look.ed204d791a"); }, get hint() { return uiText("ui.ui.kit.window.look.53364359ec"); } },
  { id: "syrup", get name() { return uiText("ui.ui.kit.window.look.1bd05468a1"); }, get hint() { return uiText("ui.ui.kit.window.look.ace3f7647d"); } },
];

export const EDGES: Array<Choice & { id: Overscroll }> = [
  { id: "rubber", get name() { return uiText("ui.ui.kit.window.look.34eeb75e62"); } },
  { id: "stretch", get name() { return uiText("ui.ui.kit.window.look.23032301ab"); } },
  { id: "none", get name() { return uiText("ui.ui.kit.window.look.923d935453"); } },
];

export interface Look {
  paperDay: string;
  paperNight: string;
  accent: string;
  grain: string;
  text: string;
  feel: FeelName;
  edge: Overscroll;
  uiScale: number;
  density: "comfortable" | "compact";
  style: "paper" | "clean" | "studio" | "glass" | "terminal";
  font: "hand" | "sans" | "serif" | "mono";
  shape: "default" | "round" | "square";
  backdrop: "plain" | "grid" | "glow";
  dayColor: string;
  nightColor: string;
  accentColor: string;
}

/** Светлая бумага с лёгкой фактурой; сохранённый выбор владельца имеет приоритет. */
export const DEFAULT_LOOK: Look = { paperDay: "light", paperNight: "coal", accent: "terra", grain: "soft", text: "m", feel: "syrup", edge: "rubber", uiScale: 1, density: "comfortable", style: "paper", font: "hand", shape: "default", backdrop: "plain", dayColor: "", nightColor: "", accentColor: "" };

const KEY = "helene.look";

export function load(): Look {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<Look>;
    const pick = <T extends Choice>(list: T[], v: unknown, def: string) => (list.some((x) => x.id === v) ? String(v) : def);
    return {
      paperDay: pick(PAPER_DAY, raw.paperDay, DEFAULT_LOOK.paperDay),
      paperNight: pick(PAPER_NIGHT, raw.paperNight, DEFAULT_LOOK.paperNight),
      accent: pick(ACCENTS, raw.accent, DEFAULT_LOOK.accent),
      grain: pick(GRAIN, raw.grain, DEFAULT_LOOK.grain),
      text: pick(TEXT, raw.text, DEFAULT_LOOK.text),
      feel: pick(FEELS, raw.feel, DEFAULT_LOOK.feel) as FeelName,
      edge: pick(EDGES, raw.edge, DEFAULT_LOOK.edge) as Overscroll,
      uiScale: typeof raw.uiScale === "number" && Number.isFinite(raw.uiScale) ? Math.max(.7, Math.min(1.5, raw.uiScale)) : 1,
      density: raw.density === "compact" ? "compact" : "comfortable",
      style: (['paper','clean','studio','glass','terminal'].includes(raw.style||'') ? raw.style : 'paper') as Look['style'],
      font: (['hand','sans','serif','mono'].includes(raw.font||'') ? raw.font : 'hand') as Look['font'],
      shape: (['default','round','square'].includes(raw.shape||'') ? raw.shape : 'default') as Look['shape'],
      backdrop: (['plain','grid','glow'].includes(raw.backdrop||'') ? raw.backdrop : 'plain') as Look['backdrop'],
      ...Object.fromEntries((["dayColor", "nightColor", "accentColor"] as const).map(k => [k, /^#[0-9a-f]{6}$/i.test(raw[k] || "") ? raw[k] : ""])) as Pick<Look,"dayColor"|"nightColor"|"accentColor">,
    };
  } catch {
    return { ...DEFAULT_LOOK };
  }
}

export function save(look: Look) {
  try { localStorage.setItem(KEY, JSON.stringify(look)); } catch { /* не запомнится — до перезапуска */ }
}

const byId = <T extends Choice>(list: T[], id: string): T => list.find((x) => x.id === id) ?? list[0];

/** Применить облик и физику к окну — сразу, без перезапуска. */
export function apply(look: Look) {
  const r = document.documentElement.style;
  const day = byId(PAPER_DAY, look.paperDay);
  const night = byId(PAPER_NIGHT, look.paperNight);
  const acc = byId(ACCENTS, look.accent);
  const grain = byId(GRAIN, look.grain);
  const text = byId(TEXT, look.text);
  r.setProperty("--u-paper-day", look.dayColor || day.color);
  r.setProperty("--u-paper-night", look.nightColor || night.color);
  r.setProperty("--u-terra", look.accentColor || acc.fill);
  r.setProperty("--u-accent-day", acc.inkDay);
  r.setProperty("--u-accent-night", acc.inkNight);
  r.setProperty("--u-grain-day", String(grain.day));
  r.setProperty("--u-grain-night", String(grain.night));
  r.setProperty("--u-feed-base", text.px + "px");
  scroll.setPhysics(look.feel, look.edge);
  applyTheme(readTheme());
  document.documentElement.style.zoom = String(look.uiScale);
  r.setProperty('--ui-scale', String(look.uiScale));
  const app=document.getElementById("app");
  if(app) app.style.height=`calc(var(--app-height, 100dvh) / ${look.uiScale})`;
  document.documentElement.dataset.lookStyle = look.style;
  document.documentElement.dataset.lookFont = look.font;
  document.documentElement.dataset.lookShape = look.shape;
  document.documentElement.dataset.lookBackdrop = look.backdrop;
  document.documentElement.dataset.density = look.density;
  const dark = readTheme() === "dark" || (readTheme() === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  const color = dark ? look.nightColor : look.dayColor;
  for (const key of ["--ink", "--ink-2", "--ink-3", "--muted", "--line", "--line-strong", "--accent", "--accent-ink"]) r.removeProperty(key);
  const bright = (hex:string) => {
    const c=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    return c[0]*.2126+c[1]*.7152+c[2]*.0722 > .179;
  };
  if (color) {
    const light=bright(color),ink=light?"#171717":"#ffffff";
    for(const key of ["--ink","--ink-2","--ink-3","--muted","--accent"]) r.setProperty(key,ink);
    r.setProperty("--line",light?"#00000033":"#ffffff44");r.setProperty("--line-strong",light?"#00000066":"#ffffff88");
  }
  if(look.accentColor) r.setProperty("--accent-ink",bright(look.accentColor)?"#171717":"#ffffff");
  // Оболочке — цвет бумаги: окно открывается уже нужного тона, без вспышки чужого фона.
  electron?.look?.({ day: look.dayColor || day.color, night: look.nightColor || night.color });
}

/** Карточка «Вид» для экрана настроек: варианты кружками и плашками, применяются сразу. */
export function lookCard(options:{phone?:boolean}={}): HTMLElement {
  const look = load();
  const c = el("section", "card look-card");
  c.append(el("h3", "", uiText("ui.ui.kit.window.look.9022ba3739")));
  c.append(el("p", "field-hint", uiText("ui.ui.kit.window.look.036602b579")));
  const customization = customizationCard(look, () => c.replaceWith(lookCard(options)),options);
  c.append(customization);

  const set = <K extends keyof Look>(k: K, v: Look[K]) => {
    Object.assign(look,load());
    look[k] = v;
    save(look);
    apply(look);
  };

  const swatches = (title: string, list: Array<Choice & { color?: string; fill?: string }>, key: "paperDay" | "paperNight" | "accent", night = false) => {
    const row = el("div", "look-row");
    row.append(el("div", "look-label", title));
    const box = el("div", "look-swatches");
    box.setAttribute("role", "radiogroup");
    box.setAttribute("aria-label", title);
    for (const it of list) {
      const b = el("button", "look-swatch" + (night ? " night" : "")) as HTMLButtonElement;
      b.type = "button";
      b.title = it.name;
      b.setAttribute("role", "radio");
      b.setAttribute("aria-label", it.name);
      b.style.setProperty("--sw", it.color ?? it.fill ?? "transparent");
      b.dataset.id = it.id;
      b.addEventListener("click", () => {
        // Choosing a palette color replaces an earlier custom override.
        const custom = key === 'paperDay' ? 'dayColor' : key === 'paperNight' ? 'nightColor' : 'accentColor';
        Object.assign(look, load());look[custom] = '';save(look);
        set(key, it.id as never);
        mark(box, it.id);
        name.textContent = it.name;
        const input = customization.querySelector<HTMLInputElement>(`[data-look-color="${custom}"]`);
        if (input) input.value = it.color ?? it.fill ?? '#000000';
      });
      box.append(b);
    }
    const name = el("span", "look-name", byId(list, look[key]).name);
    mark(box, look[key]);
    row.append(box, name);
    return row;
  };

  const pills = <T extends Choice>(title: string, list: T[], key: "grain" | "text" | "feel" | "edge", hint?: (it: T) => string) => {
    const row = el("div", "look-row");
    row.append(el("div", "look-label", title));
    const box = el("div", "look-pills");
    box.setAttribute("role", "radiogroup");
    box.setAttribute("aria-label", title);
    const note = el("span", "look-name");
    for (const it of list) {
      const b = el("button", "look-pill", it.name) as HTMLButtonElement;
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.id = it.id;
      b.addEventListener("click", () => {
        set(key, it.id as never);
        mark(box, it.id);
        note.textContent = hint ? hint(it) : "";
      });
      box.append(b);
    }
    mark(box, String(look[key]));
    note.textContent = hint ? hint(byId(list, String(look[key]))) : "";
    row.append(box, note);
    return row;
  };

  const palette = el('section', 'look-section look-palette');
  palette.append(el('h4', 'look-section-title', uiText("ui.ui.kit.window.look.c5f0893afd")));
  palette.append(
    swatches(uiText("ui.ui.kit.window.look.02ac7d0872"), PAPER_DAY, "paperDay"),
    swatches(uiText("ui.ui.kit.window.look.6d5b70d1f4"), PAPER_NIGHT, "paperNight", true),
    swatches(uiText("ui.ui.kit.window.look.2c1fc844a6"), ACCENTS, "accent"),
  );
  const details = customization.querySelector('.look-details');
  customization.insertBefore(palette, details);
  (details || customization).append(
    pills(uiText("ui.ui.kit.window.look.3532c58cfe"), GRAIN, "grain"),
    pills(uiText("ui.ui.kit.window.look.90c9b9b106"), TEXT, "text"),
    pills(uiText("ui.ui.kit.window.look.731f34e258"), FEELS, "feel", (f) => f.hint),
    pills(uiText("ui.ui.kit.window.look.1d1168318e"), EDGES, "edge"),
  );
  const support = (window as Window & { __HELENE_SCROLL_SUPPORT?: ScrollSupport }).__HELENE_SCROLL_SUPPORT;
  const supportText = scrollSupportText(support);
  if (supportText) {
    const row = el("div", "look-row");
    row.append(el("div", "look-label", uiText("ui.ui.kit.window.look.9956df53e7")), el("span", "look-name", supportText));
    c.append(row);
  }

  return c;
}

function mark(box: HTMLElement, id: string) {
  for (const b of box.querySelectorAll<HTMLElement>("[data-id]")) b.setAttribute("aria-checked", String(b.dataset.id === id));
}
