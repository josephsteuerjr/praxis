// Стенды по ревью 25.09 (A4/A7/A12): окно и оболочка держат слово исходником.
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");
const desk = join(here, "..", "..");
const win = join(desk, "ui-kit", "window");
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const read = (...p) => code(readFileSync(join(...p), "utf8"));

// A4/A7 F1: имена аргументов команд Tauri — camelCase; snake_case молча становится None.
const frame = read(win, "views", "settings-frame.ts");
assert.match(frame, /shell\("update_install", \{ path: [^}]*forceExtensions: /, "update_install без forceExtensions (camelCase)");
assert.ok(!/force_extensions:/.test(frame), "в вызовах shell() snake_case-ключ force_extensions");
for (const m of frame.matchAll(/shell(?:<[^>]*>)?\("(\w+)",\s*\{([^}]*)\}/g)) {
  const keys = [...m[2].matchAll(/(\w+)\s*:/g)].map((k) => k[1]);
  for (const k of keys) assert.ok(!k.includes("_"), `snake_case-ключ ${k} в shell("${m[1]}")`);
}

// A7 F2: расписка «Сохранено» отличает блоки, которые движок читает только на старте.
assert.match(frame, /blocksNeedingRestart\(c, out\)/, "расписка не сверяет блоки перезапуска");
assert.match(frame, /restartBtn\.hidden = !\(modeNote \|\| restartNote\)/, "кнопка перезапуска не показывается при смене Telegram/тела/голоса");
assert.match(frame, /\["telegram", "Telegram"\]/);
assert.match(frame, /\["sandbox", "ограда"\]/);

// A7 F10: дубля строки forceToggle.hidden больше нет.
assert.ok(!/forceToggle\.hidden = true;\s*\n\s*forceToggle\.hidden = true;/.test(frame), "forceToggle.hidden = true дублируется подряд");

// A7 F5/F6/F7: запасной провайдер — одно значение в двух полях, отказ без модели, чистка при смене протокола.
const agent = read(src, "settings-agent.ts");
assert.match(agent, /setField\(spareModelOther, v\)/, "поле «Запасная модель» не отражается во втором");
assert.match(agent, /setField\(spareModelSame, v\)/, "поле «Модель запасного» не отражается в первом");
assert.match(agent, /Запасному провайдеру нужно имя модели/, "«другой провайдер» без модели сохраняется молча");
assert.match(agent, /spare === "other" && frameworkOf\(provider\) !== before/, "смена основного не чистит адрес/ключ запасного");

// A7 F8/F9: кнопка остановки шлёт /api/interrupt и ждёт квитанцию движка из /api/supervisor.
const talk = read(win, "views", "talk.ts");
assert.match(talk, /post<[^>]*>\("\/api\/interrupt", \{scope: "all"\}\)/, "кнопка не шлёт /api/interrupt");
assert.match(talk, /"\/api\/supervisor"/, "квитанция остановки не читается");
assert.match(talk, /interrupt_receipt/, "квитанция остановки не показывается");
assert.match(talk, /Просьба записана/);
assert.ok(!existsSync(join(here, "interrupt-browser.html")), "мёртвый ручной стенд interrupt-browser.html снова на месте");

// Слова квитанции — чистая функция: проверяем текстом, что ветки на месте.
assert.match(talk, /остановлено ходов: /);
assert.match(talk, /ждут исхода тула: /);
assert.match(talk, /движок не нашёл живых ходов/);

console.log("review-2509: OK");
