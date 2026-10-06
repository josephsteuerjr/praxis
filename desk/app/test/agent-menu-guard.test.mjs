// Гвард скрытия меню агентов (живая жалоба 1.4.0: «две плашки агентов закрывают
// опции меню»).
//
// Механизм: переключатель агентов (ui-kit/window/agents.ts) прячет своё меню
// ровно одним способом — атрибутом hidden (menu.hidden = true). Но у
// .agent-menu задан display: flex, а по правилам каскада ЛЮБОЕ значение
// display у правила класса побеждает родное hidden — меню висело открытым
// ВСЕГДА, поверх .rail-nav, и закрывало опции. .agent-row выстроены
// столбцом — две плашки агентов друг под другом — потому и «две плашки».
//
// Фикс — одна строка по готовому паттерну (см. .menu[hidden] ниже по файлу и
// комментарий у composer: «Author display:flex must not override the native
// hidden attribute»). Стенд — тривиальный readFile + includes: уедет строка,
// покраснеет. Ничего сверх гварда в CSS не менялось — это держит код-ревью
// дизайна, а не этот стенд.
//
// Запуск: node app/test/agent-menu-guard.test.mjs (из корня desk/ или app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "..", "ui-kit", "window", "styles", "app.css"), "utf8");

// Сам гвард — ровно в форме готового паттерна .menu[hidden].
assert.ok(css.includes(".agent-menu[hidden] {"),
  "гвард .agent-menu[hidden] пропал из app.css — меню агентов снова висит открытым всегда");
assert.ok(/\.agent-menu\[hidden\]\s*\{\s*display:\s*none;\s*\}/.test(css),
  "гвард .agent-menu[hidden] не глушит display — flex продолжает побеждать hidden");

// Гвард стоит ПОСЛЕ блока .agent-menu (иначе не перебивает его), и паттерн
// .menu[hidden], на который он ссылается, никуда не делся.
const guardAt = css.indexOf(".agent-menu[hidden]");
const blockAt = css.indexOf(".agent-menu {");
assert.ok(blockAt > 0 && guardAt > blockAt,
  "гвард .agent-menu[hidden] стоит до своего блока — каскад отдаёт победу flex");
assert.ok(/\.menu\[hidden\]\s*\{\s*display:\s*none;\s*\}/.test(css),
  "пропал образец .menu[hidden] — гвард жил по готовому паттерну, не по выдумке");

console.log("agent-menu-guard: OK — hidden снова сильнее display:flex у меню агентов");
