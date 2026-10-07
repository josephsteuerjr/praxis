// Обязательный шаг Linux-гейта: полный ПЕРВЫЙ запуск установленного пакета.
//
// Родилось из живой беды 03.10: стенд electron-stand.mjs проверял ненастроенное
// окно (настройки, IPC, isolation), но НЕ путь «ключ модели → Сохранить →
// перезапуск → движок поднялся». deb уехал в релиз с этой дырой, и первый же
// живой человек увидел «нет связи с ядром агента». Этот стенд закрывает её.
//
// Среда (как у electron-stand.mjs): контейнер helene-linux-window (+Xvfb, +dbus),
// пакет установлен в /opt/helene, HELENE_PLAYWRIGHT_MODULE — существующий
// playwright-модуль, домашняя папка пользователя ДОЛЖНА быть чистой.
//
// Стенд: окно → форма настроек → подсказка про ключ (1.3.5) → имя/ключ →
// Сохранить → «Перезапустить сейчас» → перезапущенное окно обязано поднять
// движок (runner.py) — проверяем процессами, не обещаниями интерфейса.
import { strict as assert } from 'node:assert';
import { execFile } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { pathToFileURL } from 'node:url';

const run = promisify(execFile);
assert.equal(process.platform, 'linux');
assert.notEqual(process.getuid(), 0, 'окно обязано идти от обычного владельца');
const { _electron } = await import(pathToFileURL(process.env.HELENE_PLAYWRIGHT_MODULE).href);
const output = process.env.HELENE_EVIDENCE || '/test/evidence';
mkdirSync(output, { recursive: true });
const evidence = { started: new Date().toISOString(), checks: [] };

const labelOf = (i) => i.closest('label')?.querySelector('.field-label')?.textContent || '';
const engineUp = async () => {
  try {
    const { stdout } = await run('pgrep', ['-f', 'localharness/runner.py']);
    return stdout.trim().length > 0;
  } catch { return false; }
};
let app;
try {
  app = await _electron.launch({
    executablePath: '/opt/helene/electron/helene-window',
    args: [], chromiumSandbox: true,
    env: { ...process.env, XDG_SESSION_TYPE: 'x11' },
    timeout: 90_000,
  });
  evidence.checks.push('window launched with real chromium sandbox');
  const page = await app.firstWindow();
  await page.waitForLoadState('domcontentloaded');
  await page.getByText('Настройки', { exact: true }).first().waitFor({ timeout: 30_000 });
  evidence.checks.push('first-run settings shown');

  // Подсказка 1.3.5: экран обязан объяснить, что без ключа модели движок не встанет.
  await page.waitForFunction(
    () => document.body.innerText.includes('Без ключа модели движок не запускается'),
    null, { timeout: 10_000 });
  evidence.checks.push('first-run hint names the model key requirement');

  const filled = await page.evaluate(() => {
    const labelOf = (i) => i.closest('label')?.querySelector('.field-label')?.textContent || '';
    const by = (re) => [...document.querySelectorAll('.field-input')].find((i) => re.test(labelOf(i)));
    const set = (input, value) => {
      if (!input) return false;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    };
    return {
      agent: set(by(/^Имя агента$/), 'Стенд'),
      owner: set(by(/^Твоё имя$/), 'Владелец'),
      key: set(by(/ключ/i), 'sk-gate-firstrun'),
    };
  });
  assert.ok(filled.key, 'поле ключа модели не нашлось — форма изменилась?');
  evidence.checks.push('model key filled');

  await page.getByRole('button', { name: 'Сохранить', exact: true }).first().click();
  await page.waitForFunction(
    () => document.body.innerText.includes('Настройки сохранены'), null, { timeout: 10_000 });
  evidence.checks.push('saved with setup_complete');

  const restart = page.getByRole('button', { name: /перезапустить/i }).first();
  assert.ok(await restart.isVisible(), 'кнопка перезапуска после сохранения не появилась');
  await page.screenshot({ path: join(output, 'firstrun-saved.png') });
  await restart.click();
  evidence.checks.push('relaunch requested');

  // Перезапущенное окно поднимает движок само; мы не держим его хэндл —
  // проверяем ПРОЦЕССОМ, что runner жив (до 90 с на холодный старт рантайма).
  let rose = false;
  for (let i = 0; i < 30 && !rose; i++) {
    await new Promise((r) => setTimeout(r, 3_000));
    rose = await engineUp();
  }
  assert.ok(rose, 'движок не поднялся после первого запуска — проверь helene.log/runner.log');
  evidence.checks.push('engine rose after first run');
  evidence.ok = true;
} catch (error) {
  evidence.ok = false; evidence.error = String(error); process.exitCode = 1;
} finally {
  if (app) await app.close().catch(() => undefined);
  writeFileSync(join(output, 'firstrun.json'), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify(evidence));
}
