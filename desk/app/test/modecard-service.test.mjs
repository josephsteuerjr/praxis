// Тест против живой регрессии Windows: карточка «Режим» говорила владельцу,
// что «код агента старее окна», при совершенно свежем коде агента.
//
// Как это вышло. В секции службы стояла честная пара: есть описание — печатаем
// описание, НЕТ описания — печатаем красным, что харнесс старее окна и своих
// слов окно не выдумывает. Порт на macOS добавил между ними ОГОВОРКУ («чего
// служба не даёт»), и `else` прилип к ней:
//
//     if (svcText) …;                       // описание
//     const svcWarn = …;
//     if (svcWarn) …; else { «код агента старее окна» }   // ⚠ не тот if
//
// На macOS оговорка есть всегда, и там это было незаметно. А на Windows
// оговорки нет по построению (`modes.service_texts` отдаёт ""), и красная
// строка вылезала на КАЖДОМ открытии Настроек — при живом описании прямо над
// ней. Класс ошибки: «условие пришили не к тому условию», и ловится он только
// прогоном обеих веток сразу.
//
// Почему не импорт компонента: `modecard.ts` тянет DOM, Tauri и канал, и ради
// одной пары if-ов поднимать их незачем. Берём ИСХОДНЫЙ кусок секции из файла
// и исполняем его с подставными `el` и `svcBox` — то есть проверяем тот самый
// код, а не его пересказ. Уедет кусок из файла — стенд покраснеет, а не
// замолчит.
//
// Запуск: node app/test/modecard-service.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";
import { stripTypeScriptTypes } from "node:module";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "modecard.ts"), "utf8");

// --- вырезаем секцию службы: от чтения `live.service` до первого, что за ней.
const FROM = "const option = live.service;";
const TILL = "/** Заперта ли установка службы";
const from = source.indexOf(FROM);
const till = source.indexOf(TILL, from);
assert.ok(from > 0 && till > from, "секция службы не нашлась в modecard.ts — стенд отстал от файла");
const section = source.slice(from, till);

/** Исполнить секцию с подставными окном и коробкой; вернуть напечатанные строки. */
function render({ service, service_title = "", service_text = "", service_warning = "", mac = false, linux = false }) {
  const printed = [];
  const el = (tag, cls, text) => ({ tag, cls, text });
  const svcBox = { append: (node) => printed.push(node) };
  const live = { service, service_title, service_text, service_warning };
  // eslint-disable-next-line no-new-func
  new Function("el", "svcBox", "live", "posix", "linux", section)(el, svcBox, live, mac || linux, linux);
  return printed;
}

const OLD_HARNESS = "код агента ничего не рассказал";
const said = (rows) => rows.map((r) => r.text).join("\n");

// --------------------------------------------------------------------------
// 1. Windows: описание есть, оговорки нет — и ни слова про «старее окна».
// --------------------------------------------------------------------------

const windows = render({
  service: { title: "Служба Windows", text: "Ставится один раз…", warning: "" },
});
assert.ok(said(windows).includes("Ставится один раз…"), "описание службы не напечаталось");
assert.ok(
  !said(windows).includes(OLD_HARNESS),
  "РЕГРЕССИЯ: при живом описании и пустой оговорке окно всё равно ругает код агента",
);
// Красной строки в секции нет вовсе: печатать нечего.
assert.deepEqual(windows.filter((r) => r.cls === "receipt err"), []);

// То же самое, когда описание приехало старым полем `live.service_text`.
const legacy = render({ service: undefined, service_text: "Ставится один раз…" });
assert.ok(!said(legacy).includes(OLD_HARNESS), "старое поле описания — тоже описание");

// --------------------------------------------------------------------------
// 2. macOS: описание и оговорка — обе строки, и обе на месте.
// --------------------------------------------------------------------------

const mac = render({
  service: { title: "Работать без входа в систему", text: "Ставится один раз…", warning: "Окон и экрана нет…" },
  mac: true,
});
assert.ok(said(mac).includes("Ставится один раз…"));
assert.ok(said(mac).includes("Окон и экрана нет…"), "оговорка macOS пропала");
assert.ok(!said(mac).includes(OLD_HARNESS));
assert.equal(mac[0].text, "Работать без входа в систему", "заголовок секции — словами кода агента");

// --------------------------------------------------------------------------
// 3. Старый код агента: описания НЕТ — и только тогда красная строка.
// --------------------------------------------------------------------------

const old = render({ service: undefined });
assert.ok(said(old).includes(OLD_HARNESS), "без описания окно обязано сказать, что своих слов не пишет");
assert.equal(old[0].text, "Служба Windows", "без ответа кода агента заголовок — виндовый");
assert.equal(render({ service: undefined, mac: true })[0].text, "Служба", "на Mac служба не «Windows»");
assert.equal(render({ service: undefined, linux: true })[0].text, "Служба", "Linux не обещает службу Windows");

// Описания нет, а оговорка есть — печатаются ОБЕ строки, не одна вместо другой.
const both = render({ service: { title: "", text: "", warning: "Окон и экрана нет…" }, mac: true });
assert.ok(said(both).includes(OLD_HARNESS));
assert.ok(said(both).includes("Окон и экрана нет…"));

// --------------------------------------------------------------------------
// 4. FileVault — не на экране (план §6, 19.09): его место в документах
//    поставки, а оговорка карточки стоит у КАЖДОГО владельца, включая тех,
//    кто шифрования диска не включал.
// --------------------------------------------------------------------------

const ru = JSON.parse(readFileSync(join(here, "..", "..", "lang", "ru.json"), "utf8"));
assert.ok(!ru["settings.service.warning.macos"].includes("FileVault"), "FileVault вернулся в каталог языка");
assert.ok(ru["settings.service.warning.macos"].includes("Окон и экрана"), "оговорка потеряла главное");

// --------------------------------------------------------------------------
// 5. Фикс-волна 06.10 (P2-1): тумблер «Нулевая сессия» следует за ФАКТОМ.
//    Ступень лестницы меняет session0 ПРОГРАММНО (radio-confirm → true,
//    fence-click/second-click → false), и несинхронный aria-checked показывал
//    выкл при включённой ступени — и наоборот. Секция тумблеров исполняется
//    той же вырезкой исходника, что и секция службы выше.
// --------------------------------------------------------------------------

const T_FROM = "// --- галочки службы";
const T_TILL = "// Журнал брокера";
const tFrom = source.indexOf(T_FROM);
const tTill = source.indexOf(T_TILL, tFrom);
assert.ok(tFrom > 0 && tTill > tFrom, "секция тумблеров не нашлась в modecard.ts — стенд отстал от файла");
// Секция содержит TS-типы (Map<string, …>) — вырезку исполняет new Function,
// поэтому типы срезаются тем же инструментом, что компилирует модуль.
const togglesSection = stripTypeScriptTypes(source.slice(tFrom, tTill));

/** Переключатель как в форм-ките (dom.ts toggle): клик сам тумблерит aria-checked. */
const makeSwitch = (label, initial, onChange) => {
  const sw = {
    tag: "button",
    attrs: { "aria-checked": String(initial) },
    listeners: {},
    setAttribute(k, v) {
      this.attrs[k] = String(v);
    },
    getAttribute(k) {
      return this.attrs[k];
    },
    addEventListener(ev, fn) {
      (this.listeners[ev] ||= []).push(fn);
    },
    click() {
      const next = this.getAttribute("aria-checked") !== "true";
      this.setAttribute("aria-checked", String(next));
      onChange(next);
    },
  };
  return sw;
};

function toggles({ initial0 = false, toggles: list }) {
  const btn = (text, kind, onClick) => ({ text, kind, click: onClick });
  const el = (tag, cls, text) => ({ tag, cls, text, children: [], append(...cs) { this.children.push(...cs); } });
  const live = { title: "Песочница", session0_warning: "Оговорка нулевой сессии" };
  const choiceOf = () => ({ title: "Песочница" });
  const picked = "sandbox";
  const togglesBox = { children: [], append(...cs) { this.children.push(...cs); } };
  const factory = new Function(
    "el", "btn", "switchRow", "option", "live", "choiceOf", "picked", "installed", "SESSION0_WARNING_FALLBACK", "initial0", "togglesBox",
    `let session0 = initial0;
     let firewall = false;
     ${togglesSection}
     return { syncToggles, toggleView, get session0() { return session0; }, set session0(v) { session0 = v; } };`,
  );
  return factory(el, btn, makeSwitch, { toggles: list }, live, choiceOf, picked, null, "Оговорка", initial0, togglesBox);
}

{
  const made = toggles({
    initial0: false,
    toggles: [
      { key: "service.session0", title: "Нулевая сессия", text: "Права системы без вопроса", warning: "Оговорка" },
      { key: "service.firewall", title: "Телефон из любой сети", text: "Правило брандмауэра" },
    ],
  });
  const s0 = made.toggleView.get("service.session0");
  const fw = made.toggleView.get("service.firewall");
  assert.ok(s0 && s0.sw, "переключатель session0 не сохранён в карте (P2-1)");

  // Ядро P2-1: программное включение (radio-confirm «Разрешить права СИСТЕМЫ»)
  // меняет факт — тумблер обязан показать его. И выключение — тоже.
  made.session0 = true;
  made.syncToggles();
  assert.equal(s0.sw.getAttribute("aria-checked"), "true", "тумблер не показал программное ВКЛЮЧЕНИЕ");
  made.session0 = false;
  made.syncToggles();
  assert.equal(s0.sw.getAttribute("aria-checked"), "false", "тумблер не показал программное ВЫКЛЮЧЕНИЕ");
  made.session0 = true;
  made.syncToggles();
  assert.equal(s0.sw.getAttribute("aria-checked"), "true", "обратное включение — снова правда на экране");
  made.session0 = false;
  made.syncToggles();

  // Прямой клик по тумблеру С оговоркой — не включает сразу (слово Егора
  // 28.09): сброс в false + диалог подтверждения. Факт не меняется.
  s0.sw.click(); // flips → true внутри клика, но оговорка обязана вернуть
  assert.equal(s0.sw.getAttribute("aria-checked"), "false", "оговорка не вернула тумблер");
  assert.equal(made.session0, false, "клик мимо подтверждения включил ступень");

  // Подтверждение «Понимаю, включить» — единственный путь включения кликом.
  const ask = s0.row.children.find((c) => c.children && c.children.some((x) => x.text === "Понимаю, включить"));
  assert.ok(ask, "диалог подтверждения не собрался");
  ask.children.find((x) => x.text === "Понимаю, включить").click();
  assert.equal(made.session0, true, "подтверждение не включило");
  assert.equal(s0.sw.getAttribute("aria-checked"), "true", "после подтверждения тумблер врёт");

  // Firewall — без оговорки: клик работает напрямую, синхронизация следует.
  fw.sw.click();
  made.syncToggles();
  assert.equal(fw.sw.getAttribute("aria-checked"), "true", "клик по firewall не отразился");
}

console.log("modecard-service: секция службы и тумблеры — OK");
