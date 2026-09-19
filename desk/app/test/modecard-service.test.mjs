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
function render({ service, service_title = "", service_text = "", service_warning = "", mac = false }) {
  const printed = [];
  const el = (tag, cls, text) => ({ tag, cls, text });
  const svcBox = { append: (node) => printed.push(node) };
  const live = { service, service_title, service_text, service_warning };
  // eslint-disable-next-line no-new-func
  new Function("el", "svcBox", "live", "mac", section)(el, svcBox, live, mac);
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
