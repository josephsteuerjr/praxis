// Тест против одного класса: «Сохранить» стирает то, чего экран не знает.
//
// Класс сорвался дважды. Сперва `relay.instructions` — ручка, которой оболочка
// гасит 23 КБ чужого системного промпта Codex CLI: блок relay пересобирался
// заново, ручка исчезала при первом же сохранении, и агент получал обратно
// чужой промпт. Потом `sandbox.mounts` и `sandbox.mounts_denied` — весь список
// смонтированных папок и записанные отказы владельца: вся работа по
// монтированию обнулялась одним кликом.
//
// Поэтому тест ловит НЕ отдельную строку, а правило: каждый объектный блок в
// «Сохранить» пишется слиянием, а не пересборкой.
//
// Вторая половина — про режим. В `agent_mode` едет ТОЛЬКО ограда рук
// (`sandbox` | `interactive`). Слово "service" туда писала первая редакция,
// когда служба считалась третьим режимом, и из этого рос P0: у владельца,
// поставившего службу И песочницу, режим выводился как `service`, а `service`
// означал «ограды нет» — песочница снималась молча.
//
// Запуск: node app/test/config-blocks.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

import { keepBlock } from "../../ui-kit/window/config.ts";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");
const win = join(here, "..", "..", "ui-kit", "window");   // общее окно обоих изданий


// --------------------------------------------------------------------------
// 1. Само слияние: прогоном, а не чтением исходника.
// --------------------------------------------------------------------------

// Ровно тот случай, который стоил монтирования: экран знает про enabled и
// network и ничего не знает про mounts.
const sandboxWas = {
  enabled: true,
  network: false,
  mounts: [{ path: "C:\\Users\\Егор\\Документы", access: "write" }],
  mounts_denied: [{ path: "C:\\Windows", at: "04.09.2026 10:00" }],
};
const sandboxNow = keepBlock(sandboxWas, { enabled: false, network: true });
assert.deepEqual(
  sandboxNow.mounts,
  sandboxWas.mounts,
  "список смонтированных папок не пережил сохранение — это ровно тот P1, ради которого тест написан",
);
assert.deepEqual(sandboxNow.mounts_denied, sandboxWas.mounts_denied, "отказы владельца не пережили сохранение");
assert.equal(sandboxNow.enabled, false, "слияние обязано записать то, что кладёт экран");
assert.equal(sandboxNow.network, true);

// Тот же класс на relay: ручка, которой экран не показывает, но обязан сохранить.
const relayNow = keepBlock({ enabled: true, port: 5011, instructions: "молчи" }, { enabled: false });
assert.equal(relayNow.instructions, "молчи", "relay.instructions не пережил сохранение — рецидив первой беды");

// Блока нет вовсе, лежит строка, лежит массив — во всех трёх случаях слияние не
// падает и не тащит мусор в конфиг.
assert.deepEqual(keepBlock(undefined, { enabled: true }), { enabled: true });
assert.deepEqual(keepBlock("сломано", { enabled: true }), { enabled: true });
assert.deepEqual(
  keepBlock([1, 2], { enabled: true }),
  { enabled: true },
  "массив слился как объект: в блок уехали ключи \"0\" и \"1\", и харнесс прочтёт мусор",
);

// Исходный блок не портим: экран собирает `out` копией черновика, и правка
// «на месте» вернулась бы владельцу на экран как уже сохранённая.
assert.equal(sandboxWas.enabled, true, "keepBlock изменил исходный блок вместо того, чтобы вернуть новый");

// --------------------------------------------------------------------------
// 2. Правило в самом экране: объектные блоки пишутся только слиянием.
// --------------------------------------------------------------------------

/** Комментарии — не код: в них те же присваивания разбираются словами. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const settingsTs = code(readFileSync(join(src, "views", "settings.ts"), "utf8"));

const assignments = [...settingsTs.matchAll(/\bout\.(\w+)\s*=\s*([\s\S]{0,40})/g)];
assert.ok(assignments.length >= 5, "присваиваний блоков в settings.ts не нашлось — тест устарел, а не код");
for (const [, block, tail] of assignments) {
  // Значение-объект — единственное опасное место: строка или число ничего не
  // теряют. Опасен ровно литерал `{ … }` без слияния.
  if (!tail.trimStart().startsWith("{")) continue;
  const merged = /^\s*\{\s*\.\.\./.test(tail);
  assert.ok(
    merged,
    `out.${block} пересобирается литералом вместо слияния: всё, чего экран не знает, ` +
      `исчезнет при первом же «Сохранить». Пиши через keepBlock(out.${block}, { … }).`,
  );
}

// Именно те блоки, на которых класс уже срывался, — поимённо.
for (const block of ["sandbox", "phone", "telegram", "model", "service", "computer"]) {
  assert.ok(
    new RegExp(`out\\.${block} = keepBlock\\(out\\.${block},`).test(settingsTs),
    `out.${block} обязан писаться через keepBlock(out.${block}, …)`,
  );
}
assert.ok(
  /mounts: mounts\.mounts\(\)/.test(settingsTs) && /mounts_denied: mounts\.denied\(\)/.test(settingsTs),
  "карточка монтирования не отдаёт списки в сохранение — экран есть, а записать его нечем",
);

// --------------------------------------------------------------------------
// 3. В `agent_mode` едет только ограда рук, и никогда — служба.
// --------------------------------------------------------------------------

// Код режима с 10.09 лежит в ДВУХ файлах: общая часть (типы и чтение
// `/api/mode`) — в ui-kit/window/mode.ts, карточка выбора — у Элен. Правила ниже
// про режим как таковой, и держать их надо на обоих файлах сразу: иначе переезд
// строки из одного в другой тихо снимал бы проверку.
const modeTs = code(readFileSync(join(win, "mode.ts"), "utf8")
                    + "\n" + readFileSync(join(src, "modecard.ts"), "utf8"));

assert.ok(
  /out\[MODE_KEY\]\s*=\s*picked;/.test(settingsTs),
  "в ключ режима обязано ложиться то, что отдала карточка, и ничего больше",
);
assert.ok(
  !/\[MODE_KEY\]\s*=\s*["'`]service/.test(settingsTs) && !/agent_mode["']?\s*[:=]\s*["'`]service/.test(settingsTs),
  "экран пишет в agent_mode слово service — служба не ограда, и такая запись снимает песочницу молча",
);
// Карточка обязана отдавать имя ТОЛЬКО из списка оград: старый харнесс рядом с
// новым окном ещё присылает "service" третьим пунктом, и без фильтра оно уехало
// бы в файл.
assert.ok(
  /const LEGACY_SERVICE = "service"/.test(modeTs),
  "в mode.ts нет имени старого третьего режима — фильтровать нечего",
);
assert.ok(
  /filter\(\(c\) => c && c\.name && c\.name !== LEGACY_SERVICE\)/.test(modeTs),
  "список оград не фильтруется от старого 'service': окно предложит его как ограду и запишет в agent_mode",
);
assert.ok(
  /name: \(\) => choiceOf\(picked\)\?\.name \|\| ""/.test(modeTs),
  "карточка обязана отдавать имя только из списка оград, а не сырой выбор",
);

// Две галочки службы — два РАЗНЫХ ключа. На одном общем ключе кнопка «Телефон»
// под службой была мертва, пока владелец не отдаст агенту права системы.
assert.ok(
  /session0: mode\.session0\(\), firewall: mode\.firewall\(\)/.test(settingsTs),
  "session0 и firewall обязаны писаться отдельными ключами блока service",
);

console.log("конфиг: блоки сливаются, режим — только ограда");
