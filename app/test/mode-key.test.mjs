// Тест окна против двух ловушек режима.
//
// Ловушка первая — ИМЯ КЛЮЧА. В ТЗ режим назван словом `mode`, но ключ `mode` в
// helene.json уже занят и означает совсем другое: где живёт харнесс, "local" или
// "remote". Его читают shell/src/main.rs (иначе окно не поднимает ни трубу, ни
// руннер) и svc/src/main.rs (иначе служба отказывается стартовать). Экран
// настроек, записавший туда "sandbox", выключил бы продукт целиком и молча.
// Поэтому режим живёт в `agent_mode`, а галочка нулевой сессии — в
// `service.session0`, ровно там, где её читает служба.
//
// ⚠ Служба режимом НЕ является (04.09): она опция поверх любой из двух оград и
// ограду не снимает. Что в `agent_mode` не должно попадать слово "service" и что
// блоки конфига обязаны сливаться, проверяет соседний тест —
// app/test/config-blocks.test.mjs.
//
// Ловушка вторая — ОПИСАНИЯ РЕЖИМОВ. Их источник один: localharness/modes.py.
// Если окно начнёт писать свои, владелец будет выбирать по одному тексту в
// установщике и читать другой в настройках. Единственная копия, которая здесь
// разрешена, — оговорка нулевой сессии (труба отдаёт её только когда галочка
// уже включена, а прочитать её надо ДО); эта копия сверяется с оригиналом
// побайтно.
//
// Запуск: node app/test/mode-key.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");
const desk = join(here, "..", "..");

/** Комментарии — не код: в них те же ключи объясняются словами. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const modeTs = code(readFileSync(join(src, "mode.ts"), "utf8"));
const settingsTs = code(readFileSync(join(src, "views", "settings.ts"), "utf8"));

// --- 1. ключ режима
assert.ok(
  /export const MODE_KEY = "agent_mode";/.test(modeTs),
  "ключ режима обязан быть agent_mode: `mode` занят под местожительство харнесса",
);
assert.ok(/MODE_KEY/.test(settingsTs), "settings.ts обязан писать режим через MODE_KEY, а не строкой");
for (const bad of [/out\["mode"\]\s*=/, /out\['mode'\]\s*=/]) {
  assert.ok(
    !bad.test(settingsTs),
    "settings.ts пишет в ключ `mode` — окно останется без харнесса, а служба не стартует",
  );
}
// В `mode` окно пишет ТОЛЬКО местожительство харнесса — карточка «Перенос»
// (0.3.1): `local` или `remote`, и ничего из оград. Любая другая правая часть —
// тот самый P0, ради которого тест написан.
const modeWrites = [...settingsTs.matchAll(/out\.mode\s*=\s*([^;]+);/g)].map((m) => m[1]);
for (const rhs of modeWrites) {
  assert.ok(
    /"remote"/.test(rhs) && /"local"/.test(rhs) && !/picked|sandbox|interactive|MODE_KEY|agent_mode/.test(rhs),
    `settings.ts пишет в \`mode\` не местожительство харнесса, а «${rhs.trim()}» — окно останется без харнесса, а служба не стартует`,
  );
}
assert.ok(
  /out\[MODE_KEY\]\s*=/.test(settingsTs),
  "settings.ts обязан класть режим в out[MODE_KEY]",
);

// --- 2. галочки службы — в блоке service, и их ДВЕ, а не одна
//
// `session0` (доступ агента к правам системы) и `firewall` (правило брандмауэра
// для кнопки «Телефон») до 04.09 держал один ключ, и под службой кнопка
// «Телефон» была мертва, пока владелец не отдаст агенту права системы. Это два
// несвязанных вопроса, и разъезжаться им обратно нельзя.
assert.ok(
  /out\.service = keepBlock\(out\.service, \{[\s\S]{0,160}?session0:/.test(settingsTs),
  "session0 обязан ложиться в блок service — оттуда его читает svc/src/main.rs::load_plan",
);
assert.ok(
  /out\.service = keepBlock\(out\.service, \{[\s\S]{0,160}?firewall:/.test(settingsTs),
  "firewall обязан ложиться в тот же блок service отдельным ключом",
);
assert.ok(
  !/mode\.session0\s*[:=]/.test(settingsTs.replace(/mode\.session0\(\)/g, "")),
  "session0 заведён вторым именем внутри mode — служба продолжит читать своё",
);

// --- 3. описания оград и опции службы — из трубы, а не свои
assert.ok(/live\?\.choices|live\.choices/.test(modeTs), "список оград обязан приходить из /api/mode (choices)");
assert.ok(/c\.title/.test(modeTs) && /c\.text/.test(modeTs), "название и описание ограды — из choices, не свои");
assert.ok(/live\.notes/.test(modeTs), "предупреждения харнесса обязаны доезжать до экрана");
assert.ok(
  /option\?\.title \|\| live\.service_title/.test(modeTs) && /option\?\.text \|\| live\.service_text/.test(modeTs),
  "тексты опции службы обязаны приходить из /api/mode (service), а не писаться в окне",
);
assert.ok(
  /option\?\.toggles/.test(modeTs),
  "галочки службы обязаны рисоваться по списку из трубы: там же лежат их умолчания и оговорки",
);

// --- 4. единственная разрешённая копия текста сверяется с оригиналом
const modesPy = readFileSync(join(desk, "localharness", "modes.py"), "utf8");
const pyLiteral = /SESSION0_WARNING = \(([\s\S]*?)\)\n/.exec(modesPy);
assert.ok(pyLiteral, "в modes.py не нашлась SESSION0_WARNING — тест устарел, а не код");
const joinLiterals = (chunk) => (chunk.match(/"([^"]*)"/g) || []).map((s) => s.slice(1, -1)).join("");
const fromPy = joinLiterals(pyLiteral[1]);

const tsLiteral = /SESSION0_WARNING_FALLBACK =([\s\S]*?);/.exec(modeTs);
assert.ok(tsLiteral, "в mode.ts не нашлась SESSION0_WARNING_FALLBACK");
const fromTs = joinLiterals(tsLiteral[1]);

assert.ok(fromPy.length > 40, "оговорка нулевой сессии в modes.py вычиталась пустой");
assert.equal(
  fromTs,
  fromPy,
  "копия оговорки нулевой сессии в окне разошлась с modes.SESSION0_WARNING:\n" +
    `  окно:  ${fromTs}\n  питон: ${fromPy}`,
);
assert.ok(
  /live\.session0_warning \|\| SESSION0_WARNING_FALLBACK/.test(modeTs),
  "живой текст оговорки обязан побеждать копию, а не наоборот",
);

console.log("режим: ключи и тексты на месте");
