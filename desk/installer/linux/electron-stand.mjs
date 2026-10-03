// Run as an ordinary disposable Linux user after installing the candidate package.
// HELENE_PLAYWRIGHT_MODULE points to the existing Playwright runtime, not a browser download.
import { strict as assert } from 'node:assert';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

assert.equal(process.platform, 'linux');
assert.notEqual(process.getuid(), 0, 'window must run as an ordinary owner');
const { _electron } = await import(pathToFileURL(process.env.HELENE_PLAYWRIGHT_MODULE).href);
const output = process.env.HELENE_EVIDENCE || '/test/evidence';
mkdirSync(output, { recursive: true });
const owner = join(process.env.HOME, '.local/share/helene');
assert.ok(!existsSync(join(owner, 'helene.json')), 'first-run stand requires a new disposable owner');
const passport = JSON.parse(readFileSync('/opt/helene/helene-build.json', 'utf8'));
const evidence = { source: passport.git, version: passport.version, hardwareAccepted: false, sandboxDisabled: false, checks: [] };
let app;
try {
  // No --no-sandbox: inability to start the sandbox must be reported, never hidden.
  // Playwright's own default (chromiumSandbox:false) silently injects --no-sandbox —
  // opt out, and prove it by reading the real command line of the browser process.
  app = await _electron.launch({ executablePath: '/opt/helene/electron/helene-window', args: [],
    chromiumSandbox: true, env: { ...process.env, XDG_SESSION_TYPE: 'x11' }, timeout: 60_000 });
  const page = await app.firstWindow();
  const native = await app.evaluate(({ app, BrowserWindow, process }) => ({
    packaged: app.isPackaged, preferences: BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
    argv: process.argv,
  }));
  assert.equal(native.packaged, true, 'stand must exercise the installed package');
  assert.ok(!native.argv.includes('--no-sandbox'), 'browser process must not run with --no-sandbox: ' + native.argv.join(' '));
  assert.equal(native.preferences.sandbox, true);
  assert.equal(native.preferences.contextIsolation, true);
  assert.equal(native.preferences.nodeIntegration, false);
  evidence.checks.push('packaged entry', 'sandbox configured', 'chromium sandbox on (no --no-sandbox in argv)');
  await page.waitForLoadState('domcontentloaded');
  await page.getByText('Настройки', { exact: true }).first().waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Сохранить', exact: true }).first().waitFor({ timeout: 30_000 });
  const renderer = await page.evaluate(async () => ({
    info: await window.__HELENE__.invoke('app_info'),
    config: await window.__HELENE__.invoke('config_load'),
    support: window.__HELENE_SCROLL_SUPPORT,
    node: typeof window.require,
    firstRun: window.DESK_CONFIG_OVERRIDE.needs_local_setup,
  }));
  assert.equal(renderer.info.platform, 'linux');
  assert.equal(renderer.info.root, owner);
  assert.equal(renderer.config.path, join(owner, 'helene.json'));
  assert.equal(renderer.node, 'undefined');
  assert.equal(renderer.firstRun, true);
  assert.equal(renderer.support.hold, 'unavailable');
  evidence.checks.push('first-run settings', 'owner home', 'Rust IPC', 'renderer isolation', 'honest hold limit');
  assert.match(await page.evaluate(async () => {
    try { await window.__HELENE__.invoke('host_shutdown'); return 'accepted'; }
    catch (e) { return String(e); }
  }), /Недоступный вызов/);
  evidence.checks.push('internal host commands rejected');
  await page.screenshot({ path: join(output, 'linux-electron-first-run.png') });
  // Restore the exact config after an IPC edit; this is an isolated disposable home.
  const save = await page.evaluate(async (loaded) => {
    const draft = { ...loaded.config, lang: 'ru' };
    const saved = await window.__HELENE__.invoke('config_save', { config: JSON.stringify(draft), mtimeNs: loaded.mtime_ns });
    const stale = await window.__HELENE__.invoke('config_save', { config: JSON.stringify(loaded.config), mtimeNs: loaded.mtime_ns });
    const current = await window.__HELENE__.invoke('config_load');
    const restored = await window.__HELENE__.invoke('config_save', { config: JSON.stringify(loaded.config), mtimeNs: current.mtime_ns });
    return { saved: saved.ok, stale: stale.code, restored: restored.ok };
  }, renderer.config);
  assert.deepEqual(save, { saved: true, stale: 'stale', restored: true });
  evidence.checks.push('configuration save and stale protection');
  await app.close(); app = undefined;
  evidence.ok = true;
} catch (error) {
  evidence.ok = false; evidence.error = String(error); process.exitCode = 1;
} finally {
  if (app) await app.close().catch(() => undefined);
  writeFileSync(join(output, 'linux-electron.json'), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify(evidence));
}
