// Константы окна — из ui-kit/contract.json, а не литералами (задача A п. 1.12,
// КОНТРАКТ A→B §6). Python (tests/t_contract.py) и Rust сверяют свои копии
// с тем же файлом; здесь — сторона TypeScript: порты, скоупы, комнаты.
//
// Запуск: node app/test/contract.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");
const kit = join(here, "..", "..", "ui-kit");
const win = join(here, "..", "..", "ui-kit", "window");   // общее окно обоих изданий


const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const contract = JSON.parse(readFileSync(join(kit, "contract.json"), "utf8"));

// --- форма файла: то, что читают три языка
assert.equal(contract.ports.desk, 8094);
assert.equal(contract.ports.relay, 5011);
assert.equal(contract.ports.body, 9480);
assert.deepEqual(contract.computer_scopes, ["computer.read", "computer.files", "computer.process", "computer.apps"]);
assert.equal(contract.rooms.default, "window");
assert.ok(new RegExp(contract.rooms.pattern).test("window-0123abcd"), "образец ключа комнаты не подходит под pattern");
assert.ok(!new RegExp(contract.rooms.pattern).test("window"), "комната по умолчанию не должна подходить под pattern новых");

// --- окно читает константы из JSON, а не держит копии
const relayTs = code(readFileSync(join(src, "relay.ts"), "utf8"));
const computerTs = code(readFileSync(join(src, "computer.ts"), "utf8"));
const stateTs = code(readFileSync(join(win, "state.ts"), "utf8"));
const phoneTs = code(readFileSync(join(kit, "phone.ts"), "utf8"));
assert.ok(/RELAY_PORT: number = contract\.ports\.relay/.test(relayTs), "relay.ts: порт не из contract.json");
assert.ok(/COMPUTER_SCOPES: readonly string\[\] = contract\.computer_scopes/.test(computerTs), "computer.ts: скоупы не из contract.json");
assert.ok(!/"computer\.read"/.test(computerTs), "computer.ts держит свою копию списка скоупов");
assert.ok(/WINDOW_ROOM: string = contract\.rooms\.default/.test(stateTs), "state.ts: ключ комнаты окна не из contract.json");
assert.ok(/contract\.rooms\.default/.test(phoneTs), "ui-kit/phone.ts: ключ комнаты окна не из contract.json");
assert.ok(/LEGACY_WINDOW_KEY: string = contract\.rooms\.legacy/.test(stateTs), "state.ts: старый ключ комнаты не из contract.json");
assert.ok(/LEGACY_WINDOW_KEY: string = contract\.rooms\.legacy/.test(phoneTs), "ui-kit/phone.ts: старый ключ комнаты не из contract.json");
// Два общих примитива держат КОПИЮ значения: их грузит actions.test.mjs как data:-модуль,
// а оттуда относительный импорт JSON не резолвится. Копии сверяем здесь.
for (const f of ["activity.ts", "steps.ts"]) {
  const src = readFileSync(join(kit, f), "utf8");
  const m = src.match(/const LEGACY_WINDOW_KEY = "([^"]+)"/);
  assert.ok(m, `ui-kit/${f}: копии старого ключа комнаты нет вовсе`);
  assert.equal(m[1], contract.rooms.legacy, `ui-kit/${f}: копия старого ключа разошлась с contract.json`);
}

// --- порт канала в прокси dev-серверов совпадает с contract.json
for (const cfg of ["app", "mobile", "miniapp"]) {
  const vite = readFileSync(join(here, "..", "..", cfg, "vite.config.ts"), "utf8");
  const ports = [...vite.matchAll(/127\.0\.0\.1:(\d+)/g)].map((m) => Number(m[1]));
  assert.ok(ports.length > 0, `${cfg}/vite.config.ts: прокси на канал не найден`);
  for (const p of ports) assert.equal(p, contract.ports.desk, `${cfg}/vite.config.ts: прокси идёт не на порт канала из contract.json`);
}

// --- префикс новых комнат выводится из pattern одинаково в окне и на телефоне
const prefix = contract.rooms.pattern.replace(/^\^/, "").split("[")[0];
assert.equal(prefix, "window-");

// --- системы продукта (порт на macOS, 19.09): слово std::env::consts::OS, которым
// оболочка (`app_info.platform`) и установщик (`defaults.platform`) называют
// хост. ui-kit/platform.ts держит КОПИЮ списка — единственную разрешённую:
// node не читает JSON без атрибутов импорта, а файл проверяется прогоном.
// Копия обязана совпадать с контрактом буква в букву.
assert.ok(contract.platforms.includes("windows") && contract.platforms.includes("macos"), "в contract.json нет систем продукта");
const { PLATFORMS } = await import("../../ui-kit/platform.ts");
assert.deepEqual([...PLATFORMS], contract.platforms, "ui-kit/platform.ts разошёлся с contract.json: platforms");

console.log("контракт: порты, скоупы и комнаты — из ui-kit/contract.json");
