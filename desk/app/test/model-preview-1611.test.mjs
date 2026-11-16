import './fixtures/ru-localization.mjs';
// Live words of a running model call (Tim, local Gemma at ~6 tokens/s): thinking folds,
// the reply being written and plain text grow in place; nothing shows once the call ends.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const kit = new URL("../../ui-kit/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const text = moduleURL(readFileSync(new URL("text.ts", kit), "utf8"));
const dom = moduleURL(readFileSync(new URL("dom.ts", kit), "utf8").replace('"./text"', JSON.stringify(text)));
const lens = moduleURL(readFileSync(new URL("tool-lens.ts", kit), "utf8").replace('"./text"', JSON.stringify(text)));
const source = readFileSync(new URL("steps.ts", kit), "utf8").replace('"./text"', JSON.stringify(text)).replace('"./dom"', JSON.stringify(dom)).replace('"./tool-lens"', JSON.stringify(lens));
const { stepsHTML } = await import(moduleURL(source));
const show = (iteration, status = "running") => stepsHTML({ manifest: { status }, iterations: [iteration] });

const preview = { thinking: "сначала проверю <b>файл</b>", reply: "Привет! Сейчас", text: "заметка",
                  thinking_chars: 1200, reply_chars: 14, text_chars: 7 };
const live = show({ call_id: "m", status: "running", tools: [], preview });
assert.match(live, /data-detail="thinking:m"/, "thinking is a fold that keeps its open state by key");
assert.match(live, /1200 знаков/);
assert.ok(live.includes("сначала проверю &lt;b&gt;файл&lt;/b&gt;"), "preview text is escaped");
assert.match(live, /data-model-preview="reply"/);
assert.match(live, /Привет! Сейчас/);
assert.match(live, /ещё не отправлено/);
assert.match(live, /data-model-preview="text"/);
// The durable answer replaces the preview; a finished, failed or stopped run shows none.
assert.doesNotMatch(show({ call_id: "m", status: "completed", ms: 5, text: "Готово", preview }), /data-model-preview|thinking:m/);
assert.doesNotMatch(show({ call_id: "m", status: "running", text: "Готово", preview }), /data-model-preview/);
assert.doesNotMatch(show({ call_id: "m", status: "failed", preview }), /data-model-preview/);
assert.doesNotMatch(show({ call_id: "m", status: "running", preview }, "cancelled"), /data-model-preview|thinking:m/);
// Only what the provider streams is shown.
const replyOnly = show({ call_id: "r", status: "running", preview: { reply: "Да" } });
assert.match(replyOnly, /Пишет ответ — уже 2 знаков/);
assert.doesNotMatch(replyOnly, /thinking:r|data-model-preview="text"/);
console.log(JSON.stringify({ ok: true }));
