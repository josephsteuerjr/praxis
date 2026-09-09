// Тест против одного класса: окно ругается на чужой харнесс.
//
// Пульт — это то же окно, но агент живёт НА СЕРВЕРЕ (`mode: remote`). Настройки
// же читались из локального файла Пульта и рисовались всегда — и владелец
// 09.09 увидел на одном экране: пустые поля модели и Telegram (их неоткуда
// взять), «модуль режима не нашёлся рядом с каналом», «Про управление
// компьютером харнесс ничего не рассказал», «Снимок устройства пуст — просьб не
// видно», путь к пустой папке данных и «Канал не пустил: только с этой машины
// (403)» на кнопке QR. Ни одно из этого не было поломкой: окно спрашивало у
// чужого харнесса то, чего у него и не должно быть.
//
// Правило, которое здесь закреплено: в `remote` карточки МЕСТНОГО агента не
// рисуются вовсе, а телефон подключается к адресу сервера.
//
// Запуск: node app/test/settings-remote.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "src", "views", "settings.ts"), "utf8");

// 1. Признак считается из конфига: `mode: remote` И непустой адрес. Одного
//    слова "remote" без адреса мало — окно тогда никуда не ходит.
assert.match(src, /const remote = String\(c\.mode \|\| ""\)\.trim\(\) === "remote" && !!remoteBase;/,
  "признак «агент на сервере» пропал или считается иначе");

// 2. Карточки местного агента — только когда агент местный.
for (const card of [
  'card("Модель", model)',
  'card("Telegram", tgBox)',
  "center.append(mode.el)",
  'card("Песочница", sb,',
  "center.append(mounts.el)",
  "center.append(computer.el)",
]) {
  const at = src.indexOf(card);
  assert.ok(at > 0, `карточка пропала из экрана: ${card}`);
  const line = src.slice(src.lastIndexOf("\n", at) + 1, at + card.length);
  assert.match(line, /if \(!remote\)/,
    `карточка рисуется и на чужом харнессе, где её нечем наполнить: ${card}`);
}

// 3. Папка данных в remote пуста: агент живёт на сервере, и показывать её как
//    «данные агента» — врать путём.
assert.match(src, /if \(!remote\) \{\s*\n\s*center\.append\(card\("Данные агента"/,
  "карточка «Данные агента» снова рисуется при агенте на сервере");

// 4. Телефон получает адрес сервера, а не адрес этой машины.
assert.match(src, /phoneCard\(draft, !!c\.phone\?\.enabled, remote \? remoteBase : ""\)/,
  "карточке телефона больше не передают адрес сервера");
assert.match(src, /const link = remoteBase\.replace\(\/\\\/\+\$\/, ""\) \+ pair\.path;/,
  "QR в remote снова строится не из адреса сервера");

// 5. В remote нет ни тумблера «слушать сеть» (это решает сервер), ни правила
//    брандмауэра этой машины: и то и другое к серверному каналу не относится.
const phoneAt = src.indexOf("function phoneCard(");
assert.ok(phoneAt > 0, "карточка телефона пропала");
const phone = src.slice(phoneAt);
assert.match(phone, /if \(remote\) phone\.append\(phoneHint, qrRow, qrOut, devicesBox\);/,
  "тумблер «слушать сеть» снова рисуется там, где он ничего не решает");
const remoteBranch = phone.slice(phone.indexOf("if (remote) {"), phone.indexOf("const port ="));
assert.ok(!remoteBranch.includes("firewall_allow"),
  "в remote снова зовут брандмауэр этой машины");

// 6. Пустой адрес обновлений больше не делает кнопку мёртвой.
assert.match(src, /url: String\(draft\.update\?\.url \|\| ""\)\.trim\(\) \|\| UPDATE_URL_DEFAULT,/,
  "проверка обновлений снова уходит с пустым адресом");

console.log("настройки: на чужом харнессе окно не спрашивает того, чего там нет");
