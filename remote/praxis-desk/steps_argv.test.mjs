// run_command cards: argv shown as one quoted line, each item one argument; shell input unchanged.
// Run: node steps_argv.test.mjs  (bundles desk/ui-kit/steps.ts with the miniapp's rolldown)
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { rolldown } = createRequire(import.meta.url)('./desk/miniapp/node_modules/rolldown');
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const out = join(mkdtempSync(join(tmpdir(), 'steps-argv-')), 'steps.mjs');
const bundle = await rolldown({ input: 'desk/ui-kit/steps.ts', logLevel: 'silent' });
await bundle.write({ file: out, format: 'esm' });
globalThis.document ??= { addEventListener() {}, querySelector() { return null; } };
const s = await import(pathToFileURL(out).href);

const args = { argv: ['git', 'show', 'HEAD:a & b.py', "it's", ''], cwd: '/app' };
assert.equal(s.ACTIONS.run_command, 'Программа');
assert.equal(s.argvLine(args), "git show 'HEAD:a & b.py' 'it'\"'\"'s' ''");
assert.equal(s.subject(args), s.argvLine(args));
assert.equal(s.suppliedCommand(args), s.argvLine(args));
assert.deepEqual(s.toolParameters(args).map(([k]) => k), ['cwd']);
// Shell input keeps its own string; a non-string argv item is not reformatted.
assert.equal(s.subject({ command: 'ls | wc -l', argv: ['x'] }), 'ls | wc -l');
assert.equal(s.argvLine({ argv: ['ok', 3] }), '');
assert.deepEqual(s.toolParameters({ argv: ['ok', 3] }).map(([k]) => k), ['argv']);
// The execution receipt still shows the actual process line.
const shown = s.executionText({ argv: ['git', 'show', 'HEAD:a & b.py'], cwd: '/app', exit_code: 0 }, args);
assert.equal(shown.title, 'Процесс');
assert.equal(shown.code, "$ cd /app\n$ git show 'HEAD:a & b.py'");
writeFileSync(out + '.ok', 'ok');
console.log('steps_argv: 10 assertions OK');
