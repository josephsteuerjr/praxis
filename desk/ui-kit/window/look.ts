// «Вид» — облик и физика окна на выбор владельца (Егор 28.09: «добавить в настройки
// визуала и всю палитру цветов фона… и базовые настройки физики (несколько вариантов)»;
// ползунков — нет, «мне не нужно 12 ползунков»: только варианты).
//
// Хранится в localStorage окна: это удобство этого окна на этом компьютере, а не
// настройка агента, и в helene.json ей делать нечего. Применяется сразу, без «Сохранить».
// Тема по-прежнему — как в системе (слово владельца 07.09); палитры — у дня и у ночи свои.
import type { FeelName, Overscroll } from "../feed/scroller";
import { el } from "./lib";
import { electron } from "./api";
import * as scroll from "./scroll";

interface Choice { id: string; name: string }
interface Paper extends Choice { color: string }
interface Accent extends Choice { fill: string; inkDay: string; inkNight: string }

/** Бумага днём: тон фона. Первый — референс Егора (`design-refs-2809/cat-paws.jpg`). */
export const PAPER_DAY: Paper[] = [
  { id: "warm", name: "Тёплая бумага", color: "#f4e4cf" },
  { id: "light", name: "Светлее", color: "#f8eee0" },
  { id: "white", name: "Почти белая", color: "#fbf7f0" },
  { id: "kraft", name: "Крафт", color: "#ecd4b4" },
  { id: "peach", name: "Персик", color: "#f6ded0" },
  { id: "linen", name: "Лён", color: "#eee8dc" },
  { id: "sage", name: "Шалфей", color: "#e7ebdf" },
  { id: "sky", name: "Голубая бумага", color: "#e6ecef" },
];

/** Бумага ночью: та же палитра в темноте, не инверсия. */
export const PAPER_NIGHT: Paper[] = [
  { id: "coal", name: "Тёплый уголь", color: "#1d1914" },
  { id: "deep", name: "Глубже", color: "#14110e" },
  { id: "cocoa", name: "Какао", color: "#251d18" },
  { id: "graphite", name: "Графит", color: "#1b1c1e" },
  { id: "navy", name: "Ночная синь", color: "#151a22" },
  { id: "pine", name: "Хвоя", color: "#161c18" },
];

/** Акцент: заливка (кнопка отправки, точки) и цвет текста-ссылок с контрастом ≥ 4,5 днём и ночью. */
export const ACCENTS: Accent[] = [
  { id: "terra", name: "Терракота", fill: "#e0915a", inkDay: "#9a4a1e", inkNight: "#eda676" },
  { id: "ochre", name: "Охра", fill: "#d9a441", inkDay: "#7a5610", inkNight: "#e8c170" },
  { id: "sage", name: "Шалфей", fill: "#8fae8b", inkDay: "#3f6a3b", inkNight: "#a9c9a4" },
  { id: "plum", name: "Слива", fill: "#b07a9e", inkDay: "#7a3f68", inkNight: "#d3a3c4" },
  { id: "sea", name: "Море", fill: "#6f9fb5", inkDay: "#2d5d73", inkNight: "#9cc3d6" },
  { id: "cinnabar", name: "Киноварь", fill: "#d9674e", inkDay: "#9a3420", inkNight: "#ee9a86" },
];

export const GRAIN: Array<Choice & { day: number; night: number }> = [
  { id: "none", name: "Гладкая", day: 0, night: 0 },
  { id: "soft", name: "Лёгкое зерно", day: 0.06, night: 0.04 },
  { id: "paper", name: "Как бумага", day: 0.1, night: 0.06 },
  { id: "rough", name: "Шершавая", day: 0.16, night: 0.1 },
];

export const TEXT: Array<Choice & { px: number }> = [
  { id: "s", name: "Мельче", px: 14.5 },
  { id: "m", name: "Обычный", px: 15.5 },
  { id: "l", name: "Крупнее", px: 17 },
  { id: "xl", name: "Крупно", px: 18.5 },
];

export const FEELS: Array<Choice & { id: FeelName; hint: string }> = [
  { id: "brisk", name: "Бодро", hint: "быстро останавливается, короткий отскок" },
  { id: "smooth", name: "Плавно", hint: "катится дольше, мягче у края" },
  { id: "syrup", name: "Тягуче", hint: "долгий накат, край держит, как мёд" },
];

export const EDGES: Array<Choice & { id: Overscroll }> = [
  { id: "rubber", name: "Резинка" },
  { id: "stretch", name: "Растяжка" },
  { id: "none", name: "Без" },
];

export interface Look {
  paperDay: string;
  paperNight: string;
  accent: string;
  grain: string;
  text: string;
  feel: FeelName;
  edge: Overscroll;
}

/** То, что Егор одобрил 28.09: тёплая бумага, терракота, «тягуче», резинка. */
export const DEFAULT_LOOK: Look = { paperDay: "warm", paperNight: "coal", accent: "terra", grain: "paper", text: "m", feel: "syrup", edge: "rubber" };

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
    };
  } catch {
    return { ...DEFAULT_LOOK };
  }
}

function save(look: Look) {
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
  r.setProperty("--u-paper-day", day.color);
  r.setProperty("--u-paper-night", night.color);
  r.setProperty("--u-terra", acc.fill);
  r.setProperty("--u-accent-day", acc.inkDay);
  r.setProperty("--u-accent-night", acc.inkNight);
  r.setProperty("--u-grain-day", String(grain.day));
  r.setProperty("--u-grain-night", String(grain.night));
  r.setProperty("--u-feed-base", text.px + "px");
  scroll.setPhysics(look.feel, look.edge);
  // Оболочке — цвет бумаги: окно открывается уже нужного тона, без вспышки чужого фона.
  electron?.look?.({ day: day.color, night: night.color });
}

/** Карточка «Вид» для экрана настроек: варианты кружками и плашками, применяются сразу. */
export function lookCard(): HTMLElement {
  const look = load();
  const c = el("section", "card look-card");
  c.append(el("h3", "", "Вид"));
  c.append(el("p", "field-hint", "Меняется сразу и запоминается в этом окне. Тема — как в системе: днём своя бумага, ночью своя."));

  const set = <K extends keyof Look>(k: K, v: Look[K]) => {
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
        set(key, it.id as never);
        mark(box, it.id);
        name.textContent = it.name;
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

  c.append(
    swatches("Бумага днём", PAPER_DAY, "paperDay"),
    swatches("Бумага ночью", PAPER_NIGHT, "paperNight", true),
    swatches("Акцент", ACCENTS, "accent"),
    pills("Фактура", GRAIN, "grain"),
    pills("Текст ленты", TEXT, "text"),
    pills("Движение", FEELS, "feel", (f) => f.hint),
    pills("Край ленты", EDGES, "edge"),
  );

  const reset = el("button", "look-reset", "Вернуть как было") as HTMLButtonElement;
  reset.type = "button";
  reset.addEventListener("click", () => {
    save({ ...DEFAULT_LOOK });
    apply({ ...DEFAULT_LOOK });
    c.replaceWith(lookCard());
  });
  c.append(reset);
  return c;
}

function mark(box: HTMLElement, id: string) {
  for (const b of box.querySelectorAll<HTMLElement>("[data-id]")) b.setAttribute("aria-checked", String(b.dataset.id === id));
}
