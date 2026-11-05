import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test } from 'node:test';

const source = readFileSync(new URL('../../ui-kit/window/main.ts', import.meta.url), 'utf8');
const start = source.indexOf('  async function refreshPulse() {');
const end = source.indexOf('  // ---------------------------------------------------------------- композер', start);
assert.ok(start >= 0 && end > start, 'normal pulse renderer is available');
const factory = new Function('api', 'pulseBox', 'S', 'paintPulse', `
  let pulseHTML = '', pulseLast = '', pulseStamp = '';
  const esc = value => String(value);
  const fmtTs = value => String(value);
  const fmtK = value => String(value);
  ${stripTypeScriptTypes(source.slice(start, end))}
  return { refreshPulse, html: () => pulseHTML };
`);

async function render(last) {
  const box = { title: '', textContent: 'previous header' };
  const painted = [];
  const state = { connected: true };
  const view = factory(async path => {
    assert.equal(path, '/api/pulse');
    return { last };
  }, box, state, connected => painted.push(connected));
  await view.refreshPulse();
  return { html: view.html(), box, painted };
}

const row = overrides => ({ ts: Date.now() / 1000, model: 'fixture-model', ...overrides });

test('successful post-delivery and fallback notes preserve the successful header', async () => {
  for (const err of ['end_after_spoken', 'fallback']) {
    const output = await render(row({ ok: true, err }));
    assert.ok(output.html.includes('fixture-model'));
    assert.ok(!output.html.includes('err-msg'), err);
    assert.deepEqual(output.painted, [true]);
  }
});

test('actual failed and legacy error rows keep the error label', async () => {
  for (const failed of [{ ok: false, err: 'upstream refusal' }, { err: 'legacy error' }]) {
    const output = await render(row(failed));
    assert.ok(output.html.includes('err-msg'));
  }
});

test('an absent model call clears the pulse header', async () => {
  const output = await render({});
  assert.equal(output.html, '');
  assert.equal(output.box.textContent, '');
});
