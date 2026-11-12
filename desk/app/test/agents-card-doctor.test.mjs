// Доктор-кнопка (1.4.1): один клик сеет агента-доктора из канона поставки.
// Проверяется контракт карточки по исходнику (паттерн agents-card.test.mjs):
// кнопка зовёт agent_add с soul:{kind:"doctor"} — канон и знания читает
// программа, окно ничего не тащит через argv.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../src/agentscard.ts", import.meta.url), "utf8");

// Кнопка есть и зовёт агент-доктора ровно одним словом вида.
assert.ok(src.includes('"Завести доктора"'), "кнопка «Завести доктора» есть");
assert.ok(src.includes('soul: { kind: "doctor" }'),
  "сид — одно слово doctor, без текстов и доноров через окно");
assert.ok(src.includes('name: "Доктор"'), "имя доктору — «Доктор»");

// 12.11: the Python supervisor re-reads the roster at once (t_desktop_supervisor_1710
// raises a re-enabled neighbour without a restart). The toast says when it appears —
// the same promise as «Завести агента» — and never claims it already works.
assert.ok(src.includes("Через несколько секунд он появится в переключателе на полке"),
  "тост говорит, когда доктор появится, без перезапуска программы");
assert.ok(!src.includes("Поднимется после перезапуска программы"),
  "устаревшее обещание перезапуска вернулось");

// Отказ — словами оболочки тем же shellToast (как у всех глаголов карточки).
const docBlock = src.slice(src.indexOf('"Завести доктора"'));
assert.ok(docBlock.includes("shellToast"), "отказ доктора — словами оболочки");

console.log("agents-card-doctor: кнопка доктора сеет из канона одним словом OK");
