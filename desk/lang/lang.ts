// Язык интерфейса: ключи и каталоги.
//
// Независимые настройки интерфейса и кадра агента:
//   1. ИНТЕРФЕЙС — окно, телефон, установщик, деинсталлятор, трей: выбирается
//      САМ, по языку системы; можно сменить в настройках. Это — здесь.
//   2. КАДР — штатная конституция, инструкции, навыки и описания инструментов:
//      agent.frame_language, отдельная настройка этого агента. frame_language.py
//      и tool_language.py переводят принадлежащие продукту шаблоны. Личные
//      тексты, код, аргументы, результаты и протокольные имена остаются дословными.
//      «Живая речь» включается отдельным agent.living_prose, по умолчанию true.
// Комбинация «интерфейс французский, конституция русская» — законная, а не
// ошибка, и ни одна ось не должна тянуть за собой другую.
//
// ⚠ ПОЧЕМУ КЛЮЧИ, А НЕ НАКЛАДКА НАД DOM. Накладка (был и такой проект) держится
// на чёрном списке селекторов «чего не трогать», и восемь из одиннадцати таких
// селекторов в этом дереве не существуют вовсе: в первой же сборке перевод
// пошёл бы прямо в реплики агента и в английские схемы рук. Белый список ключей
// физически не видит ни того, ни другого.
//
// КАСКАД НЕПЕРЕВЕДЁННОЙ СТРОКИ: выбранный → английский → русский. Русский видит
// только владелец: для немца кириллица — стена, а не запасной вариант.

import ru from "./ru.json";
import en from "./en.json";
import fr from "./fr.json";
import zh from "./zh.json";

export type Lang = "ru" | "en" | "fr" | "zh";

/** Product-authored guidance, safe to display without guessing its language. */
export class UiError extends Error {}

type Catalog = Record<string, string>;

const CATALOGS: Record<Lang, Catalog> = {
  ru: ru as Catalog,
  en: en as Catalog,
  fr: fr as Catalog,
  zh: zh as Catalog,
};

/** Канон — русский: на нём написан исходный текст, с него снимаются отпечатки. */
const CANON: Lang = "ru";

/**
 * Какие языки мы вправе показать.
 *
 * ⚠ `zh-TW`, `zh-HK`, `zh-Hant` падают в английский НАМЕРЕННО: показать
 * тайваньцу упрощённые иероглифы — это заметно чужой текст, а не «почти
 * правильно». Пока традиционного каталога нет, честнее английский.
 */
export function fromTag(tag: string): Lang | "" {
  const t = String(tag || "").trim().toLowerCase().replace("_", "-");
  if (!t) return "";
  if (t === "zh" || t.startsWith("zh-hans") || t.startsWith("zh-cn") || t.startsWith("zh-sg")) return "zh";
  if (t.startsWith("zh")) return ""; // традиционное письмо — в английский
  if (t === "ru" || t.startsWith("ru-")) return "ru";
  if (t === "fr" || t.startsWith("fr-")) return "fr";
  if (t === "en" || t.startsWith("en-")) return "en";
  return "";
}

export const UI_LANGUAGE_KEY = "helene.ui-language";
function savedPreference(): Lang | "" {
  try {
    const value = globalThis.localStorage?.getItem(UI_LANGUAGE_KEY);
    if (value && ["ru", "en", "fr", "zh"].includes(value)) return value as Lang;
  } catch { /* A private browser may deny storage; system selection still works. */ }
  return "";
}
let chosen: Lang | "" = savedPreference();

/**
 * Список предпочитаемых языков, как его назвала система.
 *
 * Оболочка кладёт сюда ответ `GetUserPreferredUILanguages` СПИСКОМ, а не одну
 * строку: у человека с «немецкий, затем английский» первый же понятный язык
 * должен выиграть. В вебе и в PWA списка нет — там `navigator.languages`.
 */
function systemTags(): string[] {
  const shell = (globalThis as { DESK_CONFIG_OVERRIDE?: { locales?: string[] }; DESK_CONFIG?: { locales?: string[] } });
  const fromShell = shell.DESK_CONFIG_OVERRIDE?.locales || shell.DESK_CONFIG?.locales;
  if (fromShell && fromShell.length) return fromShell;
  const nav = (globalThis as { navigator?: { languages?: readonly string[]; language?: string } }).navigator;
  if (nav?.languages?.length) return [...nav.languages];
  return nav?.language ? [nav.language] : [];
}

/**
 * Язык интерфейса: выбор владельца сильнее системы, система сильнее умолчания.
 *
 * Умолчание — английский, а не русский: программу ставит кто угодно, и язык,
 * которого человек не знает, — это не «запасной вариант», а закрытая дверь.
 */
export function locale(): Lang {
  if (chosen) return chosen;
  for (const tag of systemTags()) {
    const lang = fromTag(tag);
    if (lang) {
      return lang;
    }
  }
  return "en";
}

/** Сменить язык руками (настройка). Пустая строка — вернуться к системному. */
export function setLocale(lang: Lang | ""): void {
  if (lang && !["ru", "en", "fr", "zh"].includes(lang)) throw new Error("Unsupported interface language");
  chosen = lang;
  if (typeof document !== "undefined" && document.documentElement) document.documentElement.lang = locale() === "zh" ? "zh-Hans" : locale();
}

/** This preference never reads or writes agent.frame_language. */
export function saveLocale(lang: Lang | ""): void {
  setLocale(lang);
  try { globalThis.localStorage?.setItem(UI_LANGUAGE_KEY, lang); } catch { /* Session-only preference. */ }
}

export function localePreference(): Lang | "" { return chosen; }

export function initializeLocale(configured?: unknown): void {
  try {
    const saved = globalThis.localStorage?.getItem(UI_LANGUAGE_KEY);
    if (saved === "" || ["ru", "en", "fr", "zh"].includes(String(saved))) {
      setLocale(saved as Lang | "");return;
    }
  } catch { /* The injected application preference remains usable. */ }
  if (configured === "" || ["ru", "en", "fr", "zh"].includes(String(configured))) setLocale(configured as Lang | "");
  else setLocale(chosen);
}

function lookup(key: string): string {
  const lang = locale();
  const own = CATALOGS[lang][key];
  if (own) return own;
  const eng = CATALOGS.en[key];
  if (eng) return eng;
  return CATALOGS[CANON][key] || "";
}

/**
 * Строка интерфейса по ключу.
 *
 * ⚠ БРОСАЕТ на неизвестном ключе — это опечатка автора, и она обязана падать
 * в разработке, а не показываться человеку пустотой. Все ключи известны на
 * сборке: каталог канона лежит рядом в файле.
 */
export function t(key: string, values?: Record<string, string | number>): string {
  const s = lookup(key);
  if (!s) throw new Error(`нет строки интерфейса: ${key}`);
  if (!values) return s;
  const used = new Set<string>();
  const result = s.replace(/\{([A-Za-z_][A-Za-z_0-9]*)\}/g, (_, name: string) => {
    if (!Object.hasOwn(values, name)) throw new Error(`Missing translation parameter: ${key}:${name}`);
    used.add(name);return String(values[name]);
  });
  if (Object.keys(values).some(name => !used.has(name))) throw new Error(`Unexpected translation parameter: ${key}`);
  return result;
}

/**
 * Строка, которая ПРИЕХАЛА С ПРОВОДА вместе с готовой прозой.
 *
 * Канал шлёт окну фразы состояния текстом (`phrase`), и старая пара «окно
 * новое, харнесс прежний» обязана продолжать работать: если ключа в каталоге
 * нет, показываем присланное как есть, а не падаем и не молчим.
 */
export function tWire(key: string, prose: string): string {
  return lookup(key) || String(prose || "");
}

/**
 * Число со словом. Формы берёт `Intl.PluralRules` — у русского их три, у
 * французского две, у китайского одна, и зашивать это руками нельзя.
 *
 * Ключи: `<key>.one`, `<key>.few`, `<key>.many`, `<key>.other` — те, что
 * назовёт `Intl` для этого языка.
 */
export function tn(key: string, n: number): string {
  const lang = locale();
  let form = "other";
  try {
    form = new Intl.PluralRules(lang).select(n);
  } catch {
    // движок без PluralRules — единственная форма
  }
  const s = lookup(`${key}.${form}`) || lookup(`${key}.other`);
  if (!s) throw new Error(`нет формы числа: ${key}.${form}`);
  return s.replace("{n}", String(n));
}

/** Есть ли у этого языка вообще свой каталог (для настройки выбора). */
export const LANGS: Array<{ id: Lang; label: string }> = [
  { id: "ru", label: "Русский" },
  { id: "en", label: "English" },
  { id: "fr", label: "Français" },
  { id: "zh", label: "中文（简体）" },
];

// Resolve before modules construct their static menus. MiniApp resolves again
// after authentication, before importing the shared application.
const bootstrap = globalThis as { DESK_CONFIG_OVERRIDE?: {lang?: unknown}; DESK_CONFIG?: {lang?: unknown} };
initializeLocale(bootstrap.DESK_CONFIG_OVERRIDE?.lang ?? bootstrap.DESK_CONFIG?.lang);
