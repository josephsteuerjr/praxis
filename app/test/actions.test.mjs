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

assert.match(show({ call_id: "m", tools: [] }), /Ожидает ответа модели/);
assert.doesNotMatch(show({ call_id: "m", tools: [] }, "cancelled"), /Ожидает ответа модели/);
assert.match(show({ status: "failed", ms: 30, error: "TimeoutError" }), /Ошибка ответа модели/);
assert.doesNotMatch(show({ status: "failed", ms: 30 }), /Ответ модели получен/);
// Старый канал не отдаёт status: результат уже был, хотя название поля отсутствует.
assert.match(show({ ms: 10, tools: [{ tool: "shell", args: { cmd: "echo OK" }, result: { head: "OK" } }] }), /результат получен/);
assert.match(show({ ms: 10, tools: [{ tool: "shell", args: { cmd: "echo OK" } }] }), /выполняется/);
assert.match(show({ tools: [{ tool: "shell" }] }, "in_doubt"), /результат неизвестен/);
const long = "echo " + "x".repeat(500) + "<script>alert(1)</script>";
const html = show({ ms: 10, tools: [{ call_id: "a", tool: "shell", args: { cmd: long }, result: { head: "head", tail: "tail", truncated: true } }] });
assert.match(html, /Команда/);
assert.ok(html.includes("x".repeat(500)), "полная команда остаётся в подробностях");
assert.match(html, /Сохранённый фрагмент результата/);
assert.match(html, /пропущена часть результата/);
assert.match(html, /data-detail="tool:a"/);
assert.doesNotMatch(html, /<script>/);
assert.match(html, /&lt;script&gt;/);
assert.match(show({ tools: [{ tool: "new_custom_tool" }] }), /new_custom_tool/);
console.log("Действия: состояния, совместимость, подробности и экранирование — OK");
