// Тест против одного класса: окно обещает на macOS то, чего там нет.
//
// Порт 0.7.1 на macOS (19.09) — без службы Windows, без тела тула `computer`,
// без правила брандмауэра и UAC. Решение владельца: не писать «на macOS этого
// нет», а просто НЕ РИСОВАТЬ карточку, строку, кнопку. Значит, у каждого такого
// места должен стоять затвор по слову системы, которое оболочка отдаёт в
// `app_info.platform` (ui-kit/window/host.ts → S.platform), и затвор должен
// быть ЗАКРЫТ по умолчанию: без ответа оболочки (Пульт в браузере, старая
// оболочка) не прячется ничего — иначе на Windows пропала бы карточка службы.
//
// Две половины: чистые правила прогоном (ui-kit/platform.ts — без DOM) и
// затворы чтением исходника, как в settings-remote.test.mjs: переезд строки
// в другой файл не должен тихо снимать проверку.
//
// Запуск: node app/test/platform.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

import { PLATFORMS, clientIsMac, isMacPlatform, kbdLabel, platformOf } from "../../ui-kit/platform.ts";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");
const desk = join(here, "..", "..");
const win = join(desk, "ui-kit", "window");

/** Комментарии — не код: в них те же слова разбираются словами. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const read = (...p) => code(readFileSync(join(...p), "utf8"));

// --------------------------------------------------------------------------
// 1. Слово системы: по контракту, и «не знаю» — это "" , а не догадка.
// --------------------------------------------------------------------------

assert.deepEqual([...PLATFORMS], ["windows", "macos", "linux"]);
assert.equal(platformOf({ platform: "macos" }), "macos");
assert.equal(platformOf({ platform: " MacOS " }), "macos", "регистр и пробелы — не повод не узнать систему");
assert.equal(platformOf({ platform: "windows" }), "windows");
assert.equal(platformOf({ platform: "amiga" }), "", "незнакомая система — это «не знаю», прятать по ней нельзя");
assert.equal(platformOf({}), "", "старая оболочка поля не шлёт — ничего не прячем");
assert.equal(platformOf(null), "", "оболочки нет (браузер) — ничего не прячем");
assert.equal(platformOf({ platform: 42 }), "");
assert.ok(isMacPlatform("macos") && !isMacPlatform("windows") && !isMacPlatform(""));

// --------------------------------------------------------------------------
// 2. Клавиатура — свойство клиента, а не хоста: ⌘ на Mac, Ctrl+ везде ещё.
// --------------------------------------------------------------------------

assert.ok(clientIsMac({ platform: "MacIntel" }));
assert.ok(clientIsMac({ platform: "", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)" }));
assert.ok(!clientIsMac({ platform: "Win32", userAgent: "Mozilla/5.0 (Windows NT 10.0)" }));
assert.ok(!clientIsMac(undefined));
assert.equal(kbdLabel("Полка слева · Ctrl+B", true), "Полка слева · ⌘B");
assert.equal(kbdLabel("Ctrl+,", true), "⌘,");
assert.equal(kbdLabel("Ctrl+1", false), "Ctrl+1", "на Windows подпись не трогаем");

// --------------------------------------------------------------------------
// 3. Затворы в окне: каждое место про службу, тело, брандмауэр стоит за
//    словом системы. Стережём ИМЯ затвора, а не абзац — иначе тест ловил бы
//    перестановку строк, а не исчезновение проверки.
// --------------------------------------------------------------------------

const chrome = read(win, "main.ts");
const host = read(win, "host.ts");
const frame = read(win, "views", "settings-frame.ts");
const learn = read(win, "views", "learn.ts");
const anatomy = read(win, "views", "anatomy.ts");
const modecard = read(src, "modecard.ts");
const agent = read(src, "settings-agent.ts");
const mounts = read(src, "mounts.ts");
const state = read(win, "state.ts");

// Слово системы приезжает из app_info один раз и ложится в S.platform.
assert.match(host, /shell<HostInfo>\("app_info"\)/, "host.ts перестал спрашивать app_info у оболочки");
assert.match(chrome, /S\.platform = platformOf\(i\)/, "окно перестало класть систему хоста в S.platform");
assert.match(state, /platform: "" as string/, "в S нет поля platform (или оно не пустое по умолчанию)");
// Подписи клавиш — по клиенту, и обработчик слушает metaKey.
assert.match(chrome, /kbdLabel\("Ctrl\+" \+ key, clientMac\)/, "подпись клавиши в полке снова захардкожена как Ctrl+");
assert.match(chrome, /e\.ctrlKey \|\| e\.metaKey/, "сочетания перестали слушать ⌘ (metaKey)");
// Перезапуск: службу спрашиваем только не на Mac.
assert.match(chrome, /if \(!isMacPlatform\(S\.platform\)\) \{[\s\S]{0,120}"service_state"/, "restartHarness спрашивает службу и на macOS");

// Каркас настроек: система идёт изданию, автозапуск без слова «Windows», брандмауэр и служба за затвором.
assert.match(frame, /edition\(\{ draft, saved: c, loaded, host, platform \}\)/, "каркас не отдаёт изданию систему хоста");
assert.match(frame, /mac \? "Запускать при входе в систему" : "Запускать при входе в Windows"/, "подпись автозапуска на Mac говорит про Windows");
assert.match(frame, /if \(inTauri && !mac\) \{[\s\S]{0,80}"firewall_allow"/, "правило брандмауэра просится и на macOS");
assert.match(frame, /if \(!mac\) \{[\s\S]{0,120}"service_state"/, "расписка «Сохранено» спрашивает службу и на macOS");
assert.match(frame, /runtime\/bin\/python3 app\/localharness\/carry\.py import/, "подсказка импорта на Mac зовёт python.exe");

// Издание Элен: секция службы и карточка тела — только не на Mac.
assert.match(agent, /const mac = platform === "macos"/, "издание Элен не читает систему хоста");
assert.match(agent, /if \(!mac\) cards\.push\(inGroup\(computer\.el, GROUP\.rights\)\)/, "карточка тела рисуется и на macOS");
assert.match(agent, /\}, mac\);\s*cards\.push\(inGroup\(mode\.el/, "карточке режима не передают систему хоста");
assert.match(modecard, /if \(!mac\) box\.append\(svcBox\)/, "секция службы рисуется и на macOS");
assert.match(modecard, /mac \? "" : ` Служба Windows: \$\{svc\}\.`/, "строка «Сейчас» на Mac говорит про службу Windows");
assert.match(modecard, /if \(!mac\) \{\s*void svcRefresh\(\)/, "карточка режима спрашивает службу у оболочки и на macOS");

// «Что поручить»: задача про управление компьютером — не на Mac.
assert.match(learn, /computer: true,/, "задача про компьютер не помечена как требующая тела");
assert.match(learn, /!\(mac && t\.computer\)/, "задача про компьютер показывается и на macOS");
// «Система»: слой тела и строка службы — не на Mac.
assert.match(anatomy, /mac \? "" : `служба Windows: \$\{svc\}`/, "анатомия на Mac пишет про службу Windows");
assert.match(anatomy, /!\(mac && h\.startsWith\("helene-body\.exe"\)\)/, "анатомия на Mac показывает слой тела");
// Монтирование: форма пути — системы агента.
assert.match(mounts, /pathProblem\(path, S\.platform\)/, "проверка пути к папке не знает систему агента");

// --------------------------------------------------------------------------
// 4. Установщик: те же затворы по `defaults().platform` (сцены строятся до
//    ответа, поэтому спрашивают в beforeEnter, а не в конструкторе).
// --------------------------------------------------------------------------

const setupUi = join(desk, "setup", "ui", "src");
const setupTs = read(setupUi, "setup.ts");
const modeScene = read(setupUi, "scenes", "mode.ts");
const installScene = read(setupUi, "scenes", "install.ts");
const uninstallScene = read(setupUi, "scenes", "uninstall.ts");
const setupMain = read(setupUi, "main.ts");

assert.match(setupTs, /platform\?: string;/, "Defaults установщика без platform");
assert.match(setupTs, /export function isMac\(\)/, "в setup.ts нет isMac()");
assert.match(setupMain, /machine\.platform = String\(d\.platform \|\| ""\)/, "main.ts установщика не берёт platform из defaults");
assert.match(setupMain, /!isMac\(\) && \(await legacy\.look\(home\)\)/, "сцена прежних служб вставляется и на macOS");
assert.match(modeScene, /this\.options\.hidden = mac;/, "ряд опций (служба, тело) показывается и на macOS");
// Атрибут hidden перебивается `display: grid` у ряда — без явного правила ряд
// оставался на экране (нашлось живой пробой в браузере, 19.09).
assert.match(readFileSync(join(setupUi, "styles.css"), "utf8"), /\.options-row\[hidden\]\s*\{\s*display:\s*none;/,
  "styles.css: у .options-row нет правила [hidden] — на macOS опции службы и тела остались бы на экране");
assert.match(modeScene, /setup\.service = false;\s*setup\.session0 = false;\s*setup\.computer = false;/, "на macOS в JSON установки могут уехать служба или тело");
assert.match(modeScene, /this\.syncPlatform\(\);\s*this\.select\(setup\.agent_mode\)/, "syncPlatform не зовётся из beforeEnter");
assert.match(installScene, /if \(!isMac\(\)\) \{\s*this\.row\(\s*"Служба Windows"/, "сводка на macOS показывает строку службы");
assert.match(installScene, /isMac\(\) \? "открой Helene\.app оттуда"/, "на macOS обещают ярлык на рабочем столе");
assert.match(uninstallScene, /isMac\(\) \? LEAD_MACOS : LEAD/, "лид снятия на macOS обещает службу и ярлыки");
assert.match(uninstallScene, /Helene Setup\.app\/Contents\/MacOS\/helene-setup/, "команда экспорта на macOS зовёт helene-setup.exe");

// --------------------------------------------------------------------------
// 5. Secure context. На macOS страница живёт на helene://localhost — кастомная
//    схема, которую wry не объявляет secure: `navigator.clipboard`,
//    `crypto.subtle`, `crypto.randomUUID` там undefined. Голый вызов — TypeError
//    по клику, молча. Копирование идёт только через `copyText` (ui-kit/dom.ts) с
//    запасным путём execCommand; secure-only API в исходниках быть не должно.
// --------------------------------------------------------------------------

import { readdirSync, statSync } from "node:fs";
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist") continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.ts$/.test(name)) out.push(p);
  }
  return out;
}
const sources = [...walk(join(desk, "ui-kit")), ...walk(src), ...walk(setupUi)];
assert.ok(sources.length > 20, "исходники окна не нашлись — стенд устарел, а не код");
for (const file of sources) {
  const text = code(readFileSync(file, "utf8"));
  const rel = file.slice(desk.length + 1);
  if (!rel.endsWith(join("ui-kit", "dom.ts"))) {
    assert.ok(!/navigator\.clipboard/.test(text), `${rel}: голый navigator.clipboard — на macOS это TypeError; зови copyText из ui-kit/dom.ts`);
  }
  assert.ok(!/crypto\.subtle|randomUUID/.test(text), `${rel}: secure-only API (crypto.subtle / randomUUID) — на helene://localhost его нет`);
}
const dom = read(desk, "ui-kit", "dom.ts");
assert.match(dom, /export async function copyText\(text: string\): Promise<boolean>/, "в ui-kit/dom.ts нет copyText");
assert.match(dom, /navigator\.clipboard\?\.writeText/, "copyText зовёт clipboard без ?.");
assert.match(dom, /document\.execCommand\("copy"\)/, "у copyText нет запасного пути через execCommand");
assert.match(installScene, /copyText\(text\)/, "кнопка «Скопировать отчёт» в установщике не через copyText");

// Каталог языков: у каждой новой строки Mac есть ключ во всех четырёх языках.
const lang = join(desk, "lang");
const ru = JSON.parse(readFileSync(join(lang, "ru.json"), "utf8"));
for (const key of ["setup.mode.lead.macos", "setup.mode.install_note.interactive.macos", "setup.install.open_failed.macos",
                   "setup.uninstall.lead.macos", "setup.uninstall.purge_body.macos"]) {
  assert.ok(ru[key], `в lang/ru.json нет ключа ${key}`);
  for (const c of ["en", "fr", "zh"]) {
    const cat = JSON.parse(readFileSync(join(lang, `${c}.json`), "utf8"));
    assert.ok(cat[key], `в lang/${c}.json нет перевода ${key}`);
  }
}
// И слова в сценах — те же, что в каталоге (каталог — реестр строк установщика).
assert.ok(modeScene.includes(ru["setup.mode.lead.macos"]), "лид сцены режима на Mac разошёлся с каталогом");
assert.ok(uninstallScene.includes(ru["setup.uninstall.lead.macos"]), "лид снятия на Mac разошёлся с каталогом");

console.log("платформа: на macOS служба, тело и брандмауэр не рисуются; без ответа оболочки не прячется ничего");
