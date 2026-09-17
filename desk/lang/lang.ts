// Язык интерфейса: ключи и каталоги.
//
// ТРИ ОСИ, И ОНИ НЕЗАВИСИМЫ (слово владельца 17.09):
//   1. ИНТЕРФЕЙС — окно, телефон, установщик, деинсталлятор, трей: выбирается
//      САМ, по языку системы; можно сменить в настройках. Это — здесь.
//   2. КОНСТИТУЦИЯ, VOICE, НАВЫКИ — язык, на котором агент ЖИВЁТ: отдельная
//      настройка, соседние корни `resources/soul.<lang>/`. Этого файла не
//      касается вовсе.
//   3. СХЕМЫ РУК — ВСЕГДА английский, локали не подчиняются (`tool_text_en.py`
//      в дереве агента).
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
function fromTag(tag: string): Lang | "" {
  const t = String(tag || "").trim().toLowerCase().replace("_", "-");
  if (!t) return "";
  if (t === "zh" || t.startsWith("zh-hans") || t.startsWith("zh-cn") || t.startsWith("zh-sg")) return "zh";
  if (t.startsWith("zh")) return ""; // традиционное письмо — в английский
  if (t.startsWith("ru")) return "ru";
  if (t.startsWith("fr")) return "fr";
  if (t.startsWith("en")) return "en";
  return "";
}

let chosen: Lang | "" = "";

/**
 * Список предпочитаемых языков, как его назвала система.
 *
 * Оболочка кладёт сюда ответ `GetUserPreferredUILanguages` СПИСКОМ, а не одну
 * строку: у человека с «немецкий, затем английский» первый же понятный язык
 * должен выиграть. В вебе и в PWA списка нет — там `navigator.languages`.
 */
function systemTags(): string[] {
  const shell = (globalThis as { PULT_CONFIG_OVERRIDE?: { locales?: string[] }; PULT_CONFIG?: { locales?: string[] } });
  const fromShell = shell.PULT_CONFIG_OVERRIDE?.locales || shell.PULT_CONFIG?.locales;
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
      chosen = lang;
      return chosen;
    }
  }
  chosen = "en";
  return chosen;
}

/** Сменить язык руками (настройка). Пустая строка — вернуться к системному. */
export function setLocale(lang: Lang | ""): void {
  chosen = lang;
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
export function t(key: string): string {
  const s = lookup(key);
  if (!s) throw new Error(`нет строки интерфейса: ${key}`);
  return s;
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
  { id: "zh", label: "中文" },
];
