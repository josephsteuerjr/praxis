// Тест против одного класса: окно обещает на macOS то, чего там нет.
//
// Порт 0.7.1 на macOS (19.09) — без правила брандмауэра и UAC. Решение
// владельца: не писать «на macOS этого нет», а просто НЕ РИСОВАТЬ карточку,
// строку, кнопку. Значит, у каждого такого места должен стоять затвор по слову
// системы, которое оболочка отдаёт в `app_info.platform`
// (ui-kit/window/host.ts → S.platform), и затвор должен быть ЗАКРЫТ по
// умолчанию: без ответа оболочки (Пульт в браузере, старая оболочка) не
// прячется ничего — иначе на Windows пропала бы карточка службы.
//
// ⚠ Тело тула `computer` с 0.8.0 на macOS ЕСТЬ (Accessibility, CoreGraphics), и
// СЛУЖБА с 0.8.0 тоже есть — демон launchd `app.helene.svc`. Их карточки, слои
// в анатомии и строки состояния больше НЕ за затвором — наоборот, стенд
// стережёт, чтобы затвор туда не вернулся. На Mac меняются только слова: имена
// без `.exe`, две строки про разрешения системы, «служба» вместо «служба
// Windows» — и НЕТ галочек нулевой сессии и брандмауэра: этих механизмов на
// macOS не существует.
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
assert.match(chrome, /S\.platform = platformOf\(i\) \|\| S\.platform/, "окно перестало класть систему хоста в S.platform");
assert.match(state, /platform: "" as string/, "в S нет поля platform (или оно не пустое по умолчанию)");
// До ответа оболочки — слово клиента (в оболочке хост = клиент), и только в
// оболочке: без этого первый рендер «Системы» и «Что поручить» шёл с
// Windows-словами на Mac и не перерисовывался. Ответ, сменивший затвор,
// перерисовывает открытый раздел.
assert.match(chrome, /if \(inTauri && clientMac\) S\.platform = "macos";/, "до ответа app_info система хоста не берётся у клиента");
assert.match(chrome, /if \(isMacPlatform\(was\) !== isMacPlatform\(S\.platform\)\) void show\(S\.view, \{ quiet: true \}\);/,
  "смена затвора по ответу app_info не перерисовывает открытый раздел");
assert.match(frame, /const platform = platformOf\(host\) \|\| S\.platform;/, "настройки не берут слово клиента, когда оболочка молчит");
assert.match(agent, /agentsCard\(mac\)/, "карточке агентов не передают систему хоста");
assert.match(read(src, "agentscard.ts"), /Helene\.app\/Contents\/MacOS\/helene --agent/, "подсказка про --agent на Mac зовёт helene.exe");
assert.ok(!/Windows не дал/.test(read(desk, "ui-kit", "text.ts")), "text.ts винит Windows в PermissionError и на macOS");
// Подписи клавиш — по клиенту, и обработчик слушает metaKey.
assert.match(chrome, /kbdLabel\("Ctrl\+" \+ key, clientMac\)/, "подпись клавиши в полке снова захардкожена как Ctrl+");
assert.match(chrome, /e\.ctrlKey \|\| e\.metaKey/, "сочетания перестали слушать ⌘ (metaKey)");
// Перезапуск: службу спрашиваем на ОБЕИХ системах — она есть и там, и там.
assert.match(chrome, /svc = await shell<string>\("service_state"\)/, "restartHarness перестал спрашивать про службу");
assert.ok(
  !/isMacPlatform\(S\.platform\)\) \{[\s\S]{0,120}"service_state"/.test(chrome),
  "затвор по macOS вернулся на вопрос о службе — а служба там есть с 0.8.0 (демон launchd)",
);

// Каркас настроек: система идёт изданию, автозапуск без слова «Windows», брандмауэр и служба за затвором.
assert.match(frame, /edition\(\{ draft, saved: c, loaded, host, platform \}\)/, "каркас не отдаёт изданию систему хоста");
assert.match(frame, /mac \? "Запускать при входе в систему" : "Запускать при входе в Windows"/, "подпись автозапуска на Mac говорит про Windows");
assert.match(frame, /if \(inTauri && !mac\) \{[\s\S]{0,80}"firewall_allow"/, "правило брандмауэра просится и на macOS");
assert.ok(
  !/if \(!mac\) \{[\s\S]{0,120}"service_state"/.test(frame),
  "расписка «Сохранено» снова не спрашивает службу на macOS — а она там есть",
);
assert.match(frame, /svc = await shell<string>\("service_state"\)/, "расписка «Сохранено» перестала спрашивать про службу");
assert.match(frame, /runtime\/bin\/python3 app\/localharness\/carry\.py import/, "подсказка импорта на Mac зовёт python.exe");

// Издание Элен: секция службы — только не на Mac; карточка тела — везде, и ей
// идёт система хоста (имена без `.exe`, строки про разрешения).
assert.match(agent, /const mac = platform === "macos"/, "издание Элен не читает систему хоста");
assert.match(agent, /computerCard\(modeLive, storedComputer\(draft\.computer\), platform\)/, "карточке тела не передают систему хоста");
assert.match(agent, /^\s*cards\.push\(inGroup\(computer\.el, GROUP\.rights\)\);/m, "карточка тела снова за затвором — а тело на macOS есть с 0.8.0");
const computerTs = read(src, "computer.ts");
assert.match(computerTs, /const mac = platform === "macos"/, "карточка тела не различает систему агента");
assert.match(computerTs, /shell\("open_privacy_pane", \{ kind \}\)/, "кнопки «Открыть настройки» не зовут open_privacy_pane");
// ⚠ Имя пункта — ровно то, что написано в Системных настройках Sequoia:
// «Запись экрана» там больше нет, есть «Запись экрана и системного звука».
assert.match(computerTs, /\["screen_recording", "screen", "Запись экрана и системного звука"\]/,
             "строка разрешения зовёт раздел не так, как его зовёт сама система");
assert.ok(computerTs.includes("«Запись экрана и системного звука» и «Универсальный доступ»"),
          "примечание карточки не называет разделы системы полными именами");
assert.match(computerTs, /\["accessibility", "accessibility", "Универсальный доступ"\]/, "строки про «Универсальный доступ» нет");
assert.match(computerTs, /tccBox\.hidden = !tcc \|\| typeof tcc !== "object"/, "строки про разрешения рисуются без слова тела (по догадке)");
assert.ok(!/helene-body\.exe и helene-bridge\.exe рядом/.test(computerTs), "имена тела в карточке снова захардкожены с .exe");
assert.match(agent, /\}, mac\);\s*cards\.push\(inGroup\(mode\.el/, "карточке режима не передают систему хоста");
// Секция службы рисуется на обеих системах; на Mac у неё свои слова, и
// галочек там нет — их список приходит от харнесса (`service.toggles` пуст).
assert.ok(!/if \(!mac\) box\.append\(svcBox\)/.test(modecard), "секция службы снова спрятана на macOS — а служба там есть");
assert.match(modecard, /^\s*box\.append\(svcBox\);/m, "секция службы не добавляется в карточку вовсе");
assert.match(modecard, /mac \? "Служба" : "Служба Windows"/, "строка «Сейчас» на Mac снова говорит «служба Windows»");
assert.ok(!/if \(!mac\) \{\s*void svcRefresh\(\)/.test(modecard), "карточка режима снова не спрашивает службу на macOS");
assert.match(modecard, /^\s*void svcRefresh\(\);/m, "карточка режима перестала спрашивать состояние службы");
assert.match(modecard, /st === "unknown"/, "четвёртый ответ о службе («спросить не вышло») снова слит с «Службы нет»");
assert.match(modecard, /option\?\.warning \|\| live\.service_warning/, "оговорка опции службы не берётся у харнесса");
// Мина порта 8094: под живой службой окно — клиент, и сказать это оно обязано
// на экране, а не только в helene.log.
assert.match(modecard, /работает клиентом/, "карточка молчит о том, кто держит движок под живой службой");

// «Что поручить»: задача про управление компьютером — на любой системе (тело
// есть и на Mac); затвора по mac у неё быть не должно.
assert.match(learn, /computer: true,/, "задача про компьютер не помечена как требующая тела");
assert.ok(!/mac && t\.computer/.test(learn), "задача про компьютер снова спрятана на macOS — тело там есть с 0.8.0");
// «Система»: строка службы — не на Mac; слой тела — везде, на Mac без `.exe`.
assert.match(anatomy, /\$\{mac \? "служба \(демон launchd\)" : "служба Windows"\}: \$\{svc\}/, "анатомия на Mac молчит про службу — а она там есть");
assert.match(anatomy, /function layerNameOnMac/, "в анатомии нет переименования слоёв для Mac");
assert.match(anatomy, /helene-\(body\|bridge\)\\\.exe/, "слой тела на Mac называется с .exe");
assert.ok(!/!\(mac && h\.startsWith\("helene-body\.exe"\)\)/.test(anatomy), "анатомия на Mac прячет слой тела — а тело там есть");
assert.ok(!/a\.computer && !mac/.test(anatomy), "строка о теле в анатомии спрятана на Mac");
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
// Опция службы — на обеих системах, своими словами; ряд из одной карточки
// больше не нужен (их снова две).
assert.ok(!/serviceBoxEl\.hidden = mac/.test(modeScene), "опция службы снова спрятана на macOS — а служба там есть");
assert.match(modeScene, /mac && SERVICE_OPTION_MACOS \? SERVICE_OPTION_MACOS : SERVICE_OPTION/, "опция службы на Mac не берёт слова macOS из modes.py");
assert.match(modeScene, /mac \? SERVICE_NOTE_MACOS : SERVICE_NOTE/, "примечание про пароль администратора на Mac не показывается");
assert.match(modeScene, /this\.extra\.hidden = !setup\.service \|\| isMac\(\);/, "галочка нулевой сессии на macOS открывается — а этого механизма там нет");
assert.match(modeScene, /mac && COMPUTER_OPTION_MACOS \? COMPUTER_OPTION_MACOS : COMPUTER_OPTION/, "опция тела на Mac не берёт слова macOS из modes.py");
assert.match(modeScene, /mac \? COMPUTER_NOTE_MACOS : COMPUTER_NOTE/, "примечание про два разрешения на Mac не показывается");
// Атрибут hidden перебивается `display: grid` у карточки — без явного правила
// карточка службы оставалась бы на экране (тот же класс, что у ряда 19.09).
const setupCss = readFileSync(join(setupUi, "styles.css"), "utf8");
assert.match(setupCss, /\.options-row\[hidden\],\s*\.service-card\[hidden\]\s*\{\s*display:\s*none;/,
  "styles.css: у .service-card нет правила [hidden] — на macOS опция службы осталась бы на экране");
assert.ok(!/\.options-row\.one\s*\{/.test(setupCss), "styles.css: правило ряда из одной карточки осталось мёртвым — опций снова две на обеих системах");
// `setup.service = false` остаётся ровно в одном месте — в `syncAdmin`, где
// Windows внятно ответила «прав администратора нет». Затвор по системе снят.
assert.ok(!/setup\.service = false;\s*setup\.session0 = false;/.test(modeScene),
  "на macOS служба выключается принудительно — а она там есть с 0.8.0");
assert.equal((modeScene.match(/setup\.service = false;/g) || []).length, 1,
  "решение «службы не будет» принимается не только по ответу про права администратора");
assert.match(modeScene, /if \(mac\) \{\s*setup\.session0 = false;\s*\}/, "на macOS в JSON установки может уехать нулевая сессия");
assert.ok(!/setup\.computer = false;/.test(modeScene), "на macOS тело выключается принудительно — а оно там есть с 0.8.0");
assert.match(modeScene, /this\.syncPlatform\(\);\s*this\.select\(setup\.agent_mode\)/, "syncPlatform не зовётся из beforeEnter");
assert.match(installScene, /this\.row\(\s*isMac\(\) \? "Служба" : "Служба Windows"/, "строка службы в сводке снова за затвором или говорит про Windows на Mac");
assert.match(installScene, /this\.row\(\s*"Управление компьютером"/, "строки тела в сводке нет");
const viteCfg = read(desk, "setup", "ui", "vite.config.ts");
assert.match(viteCfg, /COMPUTER_TEXT_MACOS/, "vite.config.ts не читает COMPUTER_TEXT_MACOS из modes.py");
assert.match(viteCfg, /SERVICE_TEXT_MACOS/, "vite.config.ts не читает SERVICE_TEXT_MACOS из modes.py");
assert.match(viteCfg, /SERVICE_WARNING_MACOS/, "vite.config.ts не читает SERVICE_WARNING_MACOS из modes.py");
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
                   "setup.uninstall.lead.macos", "setup.uninstall.purge_body.macos", "setup.mode.computer_note.macos",
                   "setup.mode.service_note.macos",
                   "settings.service.title.macos", "settings.service.text.macos", "settings.service.warning.macos",
                   "settings.computer.screen.macos", "settings.computer.accessibility.macos", "settings.computer.granted.macos",
                   "settings.computer.missing.macos", "settings.computer.open_settings.macos", "settings.computer.tcc_note.macos",
                   "settings.computer.no_body.macos"]) {
  assert.ok(ru[key], `в lang/ru.json нет ключа ${key}`);
  for (const c of ["en", "fr", "zh"]) {
    const cat = JSON.parse(readFileSync(join(lang, `${c}.json`), "utf8"));
    assert.ok(cat[key], `в lang/${c}.json нет перевода ${key}`);
  }
}
// И слова в сценах — те же, что в каталоге (каталог — реестр строк установщика).
// Лиды собраны из двух литералов — сверяем по кускам склейки, как и раньше целиком.
const joined = (text) => text.replace(/"\s*\+\s*"/g, "");
assert.ok(joined(modeScene).includes(ru["setup.mode.lead.macos"]), "лид сцены режима на Mac разошёлся с каталогом");
assert.ok(joined(modeScene).includes(ru["setup.mode.computer_note.macos"]), "примечание про два разрешения на Mac разошлось с каталогом");
assert.ok(joined(modeScene).includes(ru["setup.mode.service_note.macos"]), "примечание про пароль администратора на Mac разошлось с каталогом");
// Тексты самой опции службы живут в modes.py (их читает и окно, и сборка
// установщика): каталог обязан повторять их слово в слово, иначе перевод
// рассказывал бы про другую службу.
const modesPy = readFileSync(join(desk, "localharness", "modes.py"), "utf8")
  .replace(/"\s*\n\s*"/g, "").replace(/"\s*$/gm, "\"");
for (const [key, constant] of [["settings.service.title.macos", "SERVICE_TITLE_MACOS"],
                               ["settings.service.text.macos", "SERVICE_TEXT_MACOS"],
                               ["settings.service.warning.macos", "SERVICE_WARNING_MACOS"]]) {
  assert.ok(new RegExp(`^${constant}`, "m").test(modesPy), `в modes.py нет ${constant} — стенд устарел, а не код`);
  assert.ok(modesPy.includes(ru[key]), `${key} в lang/ru.json разошёлся с modes.${constant}`);
}
assert.ok(uninstallScene.includes(ru["setup.uninstall.lead.macos"]), "лид снятия на Mac разошёлся с каталогом");
assert.ok(joined(computerTs).includes(ru["settings.computer.tcc_note.macos"]), "записка про разрешения в карточке тела разошлась с каталогом");
assert.ok(computerTs.includes(ru["settings.computer.open_settings.macos"]), "кнопка «Открыть настройки» разошлась с каталогом");

console.log("платформа: на macOS служба и тело рисуются словами Mac, брандмауэр и нулевая сессия — не рисуются; без ответа оболочки не прячется ничего");
