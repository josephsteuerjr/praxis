// Живой селектор агентов (1.4.1): удалённый агент исчезает из переключателя
// без перезапуска программы — живой случай 06.10 («из селектора не пропал»).
//
// Здесь проверяется контракт модуля по исходнику (паттерном agent-menu-guard):
// mountSwitch обязан (1) звать agents_list и по изменению перерисовывать меню,
// (2) перерисовывать ТОЛЬКО при изменении состава (id-сравнение, не счётчик),
// (3) молча сохранять прошлый список при отказе оболочки. Живой клик по меню —
// за_desktop-стендами; здесь — провода.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../ui-kit/window/agents.ts", import.meta.url), "utf8");

// 1. Живой опрос есть: agents_list спрашивается и на открытии, и по таймеру.
assert.ok(src.includes('"agents_list"'), "селектор спрашивает agents_list");
assert.ok(src.includes("setInterval"), "селектор опрашивает по таймеру");
assert.ok(src.includes("void refreshSwitch()"), "первый опрос — сразу при монтаже");

// 2. Перерисовка только при изменении состава: сравнение по dataset.id строк.
assert.ok(src.includes("row.dataset.id = a.id"), "строка несёт свой id");
assert.ok(src.includes("row?.dataset.id === a.id"), "сравнение по id строк, не по счётчику");

// 3. paintRows — единственный рисовальщик меню (innerHTML меню трогает только он).
const menuWrites = [...src.matchAll(/menu\.innerHTML\s*=/g)].length;
assert.equal(menuWrites, 1, "меню перерисовывает только paintRows");

// 4. Отказ оболочки молча сохраняет прошлый список (не пустеет).
assert.ok(/catch\s*\{\s*\/\* прошлый список остаётся \*\//.test(src),
  "отказ agents_list не очищает меню");

console.log("agent-switch-live: живой селектор обновляет меню по agents_list OK");
