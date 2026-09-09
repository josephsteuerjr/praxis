// Тест окна против ТРЕТЬЕГО писателя адреса реле.
//
// История: установщик пишет model.base_url БЕЗ «/v1» и накрыт своим юнит-тестом
// (setup/src/install.rs, relay_base_url_has_no_v1), а экран «Настройки» в окне
// писал его С «/v1» — маршрута /v1/chat/completions у реле нет вовсе, и первое
// же «Сохранить» уводило каждый ход в 404. Кнопка «Проверить» рядом при этом
// оставалась зелёной: она бьёт в /v1/models, который у реле ЕСТЬ.
//
// Тест держит три вещи:
//   1) адрес мозга — без «/v1»;
//   2) адрес пробы — тот же адрес плюс «/v1» (а не отдельно набранная строка);
//   3) в settings.ts нет собственных литералов с 127.0.0.1:5011 — только импорт.
//
// Запуск: node app/test/relay-url.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");

/** Комментарии — не код: в них эти же адреса объясняются словами. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const relayTs = code(readFileSync(join(src, "relay.ts"), "utf8"));
const settingsTs = code(readFileSync(join(src, "views", "settings.ts"), "utf8"));

// --- 1. значения самих констант: порт — из ui-kit/contract.json (его же
// сверяют Python и Rust), окно обязано читать его оттуда, а не литералом
const contract = JSON.parse(readFileSync(join(here, "..", "..", "ui-kit", "contract.json"), "utf8"));
assert.ok(/RELAY_PORT: number = contract\.ports\.relay;/.test(relayTs), "RELAY_PORT обязан читаться из ui-kit/contract.json");
const port = Number(contract.ports.relay);
assert.equal(port, 5011, "порт реле разошёлся с install.rs");

const base = `http://127.0.0.1:${port}`;
assert.ok(
  /RELAY_BASE_URL = `http:\/\/127\.0\.0\.1:\$\{RELAY_PORT\}`/.test(relayTs),
  "RELAY_BASE_URL обязан быть http://127.0.0.1:<порт> без хвоста",
);
assert.ok(!/RELAY_BASE_URL = [^\n]*\/v1/.test(relayTs), "в адресе мозга появился /v1 — это 404 на каждый ход");

assert.ok(
  /RELAY_PROBE_URL = `\$\{RELAY_BASE_URL\}\/v1`/.test(relayTs),
  "адрес пробы обязан строиться ИЗ адреса мозга, иначе они разъедутся снова",
);

assert.ok(
  /export function relayBaseUrl\(port: number\): string \{\s*return `http:\/\/127\.0\.0\.1:\$\{port\}`;/.test(relayTs),
  "relayBaseUrl обязан строиться от ПЕРЕДАННОГО порта, а не от константы",
);
assert.ok(
  /export function relayProbeUrl\(port: number\): string \{\s*return `\$\{relayBaseUrl\(port\)\}\/v1`;/.test(relayTs),
  "relayProbeUrl обязан строиться из relayBaseUrl того же порта",
);

// --- 2. ключ реле не из Math.random
assert.ok(/crypto\.getRandomValues/.test(relayTs), "ключ реле обязан идти из crypto.getRandomValues");
assert.ok(!/Math\.random/.test(relayTs), "Math.random для ключа подписки недопустим");
assert.ok(/new Uint8Array\(24\)/.test(relayTs), "длина ключа должна совпадать с random_hex(24) в install.rs");

// --- 3. в экране настроек нет своих литералов адреса реле
const ownLiterals = settingsTs.match(/127\.0\.0\.1:5011[^"'`\s]*/g) || [];
assert.deepEqual(ownLiterals, [], `settings.ts снова набрал адрес реле руками: ${ownLiterals.join(", ")}`);
assert.ok(/relayBaseUrl\(/.test(settingsTs), "settings.ts обязан строить адрес мозга через relayBaseUrl(port)");
assert.ok(/relayProbeUrl\(/.test(settingsTs), "settings.ts обязан строить адрес пробы через relayProbeUrl(port)");
// ⚠ Порт — из конфига, не из константы. Установщик берёт первый свободный из
// RELAY_PORT..+19, когда 5011 занят (install.rs:628); окно, писавшее константу,
// возвращало 5011 и уводило мозг в ЧУЖОЕ реле при первом же «Сохранить».
assert.ok(
  /relay\?\.port|relayBlock\.port/.test(settingsTs),
  "settings.ts обязан брать порт реле из конфига (relay.port), а не из константы",
);
assert.ok(
  !/base_url = RELAY_BASE_URL/.test(settingsTs),
  "адрес мозга снова прибит константой — выбранный установщиком порт будет затёрт",
);
assert.ok(!/Math\.random/.test(settingsTs), "ключ реле снова генерируется Math.random()");

// --- 4. вторая сторона шва: установщик пишет тот же адрес
const installRs = readFileSync(join(here, "..", "..", "setup", "src", "install.rs"), "utf8");
const installBase = /model\["base_url"\] = format!\("(http:\/\/127\.0\.0\.1:\{\w+\})"\)/.exec(installRs);
assert.ok(installBase, "install.rs пишет адрес мозга не так, как окно — шов снова разошёлся");
assert.ok(!installBase[1].includes("/v1"), "в install.rs вернулся /v1");

console.log(`ok: адрес мозга ${base}, проба ${base}/v1, ключ crypto, литералов в settings.ts нет`);
