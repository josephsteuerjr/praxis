// Поведение реального компонента: старый API, ожидание, ошибка, неполный результат.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const kit = new URL("../../ui-kit/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const text = moduleURL(readFileSync(new URL("text.ts", kit), "utf8"));
const source = readFileSync(new URL("steps.ts", kit), "utf8").replace('"./text"', JSON.stringify(text));
const { stepsHTML } = await import(moduleURL(source));
const show = (iteration, status = "running") => stepsHTML({ manifest: { status }, iterations: [iteration] });

// ⚠ Слова здесь — те же, что показывает компонент СЕЙЧАС. Прежние («Ожидает
// ответа модели», «Ошибка ответа модели») исчезли из ui-kit/steps.ts вместе с
// переделкой ленты шагов (540f45c, до 0.4.8), и тест с тех пор был красным —
// его просто не гоняли: обычный прогон набора это `tests/t_*.py`, а тесты окна
// (app/test/*.mjs) в него не входили. Проверяется по-прежнему поведение, а не
// буква: живой шаг без рук говорит, что модель думает; отменённый прогон этого
// не говорит; упавший шаг называет ошибку.
assert.match(show({ call_id: "m", tools: [] }), /думает/);
assert.doesNotMatch(show({ call_id: "m", tools: [] }, "cancelled"), /думает/);
assert.match(show({ status: "failed", ms: 30, error: "TimeoutError" }), /ошибка модели/);
assert.doesNotMatch(show({ status: "failed", ms: 30 }), /думает/);
// Старый канал не отдаёт status: результат уже был, хотя название поля отсутствует.
assert.match(show({ ms: 10, tools: [{ tool: "shell", args: { cmd: "echo OK" }, result: { head: "OK" } }] }), /результат получен/);
assert.match(show({ ms: 10, tools: [{ tool: "shell", args: { cmd: "echo OK" } }] }), /выполняется/);
assert.match(show({ tools: [{ tool: "shell" }] }, "in_doubt"), /результат неизвестен/);
const long = "echo " + "x".repeat(500) + "<script>alert(1)</script>";
const html = show({ ms: 10, tools: [{ call_id: "a", tool: "shell", args: { cmd: long }, result: { head: "head", tail: "tail", truncated: true } }] });
assert.match(html, /Команда/);
assert.ok(html.includes("x".repeat(500)), "полная команда остаётся в подробностях");
// Обрезанный результат: обе половины на месте, а между ними сказано, что
// пропущено. Прежняя подпись «Сохранённый фрагмент результата» исчезла вместе
// с переделкой ленты — теперь длинный текст раскрывается кнопкой.
assert.match(html, /пропущена часть результата/);
assert.match(html, /head/);
assert.match(html, /tail/);
assert.match(html, /Показать целиком/);
assert.match(html, /data-detail="tool:a"/);
assert.doesNotMatch(html, /<script>/);
assert.match(html, /&lt;script&gt;/);
assert.match(show({ tools: [{ tool: "new_custom_tool" }] }), /new_custom_tool/);
console.log("Действия: состояния, совместимость, подробности и экранирование — OK");
