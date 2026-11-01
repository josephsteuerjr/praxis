import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';

// Separate real JS processes make system timezone/DST independent of CI host.
const moduleUrl = new URL('../../ui-kit/window/agenda-time.ts', import.meta.url).href;
function format(zone, value) {
  const code = `import {agendaWhen} from ${JSON.stringify(moduleUrl)};console.log(agendaWhen(${JSON.stringify(value)}));`;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
    env: { ...process.env, TZ: zone }, encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}
test('UTC instant is displayed in the system timezone', () => {
  assert.match(format('Europe/Samara', '2026-10-29T10:00:00+00:00'), /14:00/);
  assert.match(format('America/New_York', '2026-10-29T10:00:00Z'), /06:00/);
});
test('system DST and legacy local wall times retain their meanings', () => {
  assert.match(format('America/New_York', '2026-12-29T10:00:00Z'), /05:00/);
  assert.match(format('Europe/Samara', '2026-10-29T10:00:00'), /10:00/);
  assert.match(format('America/New_York', '2026-10-29T10:00:00'), /10:00/);
});
test('human and invalid dates stay legible', () => {
  assert.equal(format('Europe/Samara', 'после обеда'), 'после обеда');
  assert.equal(format('Europe/Samara', '2026-99-99T99:99:00Z'), '2026-99-99T99:99:00Z');
});
