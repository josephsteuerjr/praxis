// Tim (2.7.2): "input 5.7M, cache 96.9%" read like an arithmetic error. The per-model input
// now names its uncached part under the current counting rules; legacy rows stay unsplit.
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const kit = new URL("../../ui-kit/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const lang = moduleURL(`export const t = (key, values) => key + JSON.stringify(values || {}); export const locale = () => "en";`);
const text = moduleURL(`export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);`);
const source = readFileSync(new URL("usage.ts", kit), "utf8")
  .replace('import "./usage.css";', "")
  .replace('"../lang/lang"', JSON.stringify(lang))
  .replace('"./text"', JSON.stringify(text));
const { freshInput } = await import(moduleURL(source));

const row = (values) => ({ calls: 102, total_tokens: 0, input_tokens: 5_700_000, output_tokens: 56_200,
                           cache_read_tokens: 5_523_300, cache_percent: 96.9, legacy_semantics: false, ...values });
const html = freshInput(row({}));
assert.match(html, /usage-fresh/);
assert.match(html, /ui\.ui\.kit\.usage\.fresh_input/);
assert.ok(html.includes("177K") || html.includes("176.7K"), html);
assert.equal(freshInput(row({ legacy_semantics: true })), "");
assert.equal(freshInput(row({ cache_read_tokens: 0 })), "");
assert.equal(freshInput(row({ input_tokens: null })), "");
assert.match(freshInput(row({ input_tokens: 10, cache_read_tokens: 50 })), /&quot;p0&quot;:&quot;0&quot;/, "never negative");
console.log(JSON.stringify({ ok: true }));
