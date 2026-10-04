// Real packaged Linux Electron: saved ladder, service warning, and engine buttons.
import { strict as assert } from 'node:assert';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { _electron } = await import(pathToFileURL(process.env.HELENE_PLAYWRIGHT_MODULE).href);
assert.equal(process.platform, 'linux');
assert.notEqual(process.getuid(), 0);
const output = process.env.HELENE_EVIDENCE || '/test/evidence';
mkdirSync(output, { recursive: true });
const checks = [];
let app;
try {
  async function launch() {
    app = await _electron.launch({ executablePath: '/opt/helene/electron/helene-window',
      chromiumSandbox: true, env: { ...process.env, XDG_SESSION_TYPE: 'x11' }, timeout: 60_000 });
    const page = await app.firstWindow();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(async () => (await window.__HELENE__.invoke('owner_state')).runner_alive === true, null, { timeout: 30_000 });
    await page.waitForFunction(() => !document.querySelector('[data-engine-restart]').hidden, null, { timeout: 30_000 });
    return page;
  }
  let page = await launch();
  const before = await page.evaluate(() => window.__HELENE__.invoke('owner_state'));
  await page.locator('[data-engine-restart]').click();
  await page.waitForFunction(async pid => {
    const s = await window.__HELENE__.invoke('owner_state'); return s.runner_alive === true && s.pid !== pid;
  }, before.pid, { timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector('[data-engine-power]').disabled, null, { timeout: 30_000 });
  checks.push('restart button changes runner PID and confirms');
  await page.locator('[data-engine-power]').click();
  await page.waitForFunction(async () => {
    const s = await window.__HELENE__.invoke('owner_state'); return s.stopped && s.runner_alive === false;
  }, null, { timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector('[data-engine-power]').disabled, null, { timeout: 30_000 });
  checks.push('stop button confirms dead runner');
  await page.locator('[data-engine-power]').click();
  await page.waitForFunction(async () => {
    const s = await window.__HELENE__.invoke('owner_state'); return !s.stopped && s.runner_alive === true;
  }, null, { timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector('[data-engine-power]').disabled, null, { timeout: 30_000 });
  checks.push('resume button starts runner and confirms');
  const ownerRoot = join(process.env.HOME, '.local/share/helene');
  const hostPid = JSON.parse(readFileSync(join(ownerRoot, 'data/memory/.state/broker-answers.json'))).desk.pid;
  const passwordEvidence = execFileSync('/opt/helene/runtime/bin/python3',
    ['/test/src/desk/installer/linux/broker-password-stand.py', '--window', String(hostPid)],
    { env: { ...process.env, HELENE_BROKER_STAND: '1' }, timeout: 90_000, encoding: 'utf8' });
  assert.equal(JSON.parse(passwordEvidence.trim()).ok, true);
  checks.push('interactive agent tool reaches window and root receipt, asks a password for both commands');
  await page.getByText('Настройки', { exact: true }).first().click();
  await page.getByText('Права на этом ПК', { exact: true }).first().click();
  const rung = page.locator('.choice-item[data-value="session0"]');
  await rung.waitFor({ timeout: 30_000 });
  assert.equal(await rung.getAttribute('aria-checked'), 'true');
  assert.equal(await page.locator('.mode-service .receipt.err:visible').count(), 0);
  checks.push('saved session0 selected', 'service explanation is not a permanent red error');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).first().click();
  const saved = await page.evaluate(() => window.__HELENE__.invoke('config_load'));
  assert.equal(saved.config.service.session0, true);
  assert.equal(saved.config.agent_mode, 'interactive');
  await page.screenshot({ path: join(output, 'broker-linux-settings.png') });
  await app.close(); app = undefined;
  page = await launch();
  await page.getByText('Настройки', { exact: true }).first().click();
  await page.getByText('Права на этом ПК', { exact: true }).first().click();
  await page.locator('.choice-item[data-value="session0"][aria-checked="true"]').waitFor({ timeout: 30_000 });
  checks.push('session0 survives save and full program restart');
  await app.close(); app = undefined;
  writeFileSync(join(output, 'broker-window.json'), JSON.stringify({ ok: true, checks }, null, 2));
  console.log(JSON.stringify({ ok: true, checks }));
} catch (error) {
  writeFileSync(join(output, 'broker-window.json'), JSON.stringify({ ok: false, checks, error: String(error) }, null, 2));
  if (app) {
    const page = await app.firstWindow().catch(() => null);
    if (page) {
      await page.screenshot({ path: join(output, 'broker-failed.png') }).catch(() => {});
      console.log((await page.locator('body').innerText()).slice(0, 4000));
    }
    await app.close().catch(() => {});
  }
  console.error(error); process.exitCode = 1;
}
