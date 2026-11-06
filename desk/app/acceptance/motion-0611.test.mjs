import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { ready, close } from '../test/fixtures/ui-session-server.mjs';
import '../test/fixtures/desktop-coverage-data.mjs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const browser = await chromium.launch({ headless: true });
const source = stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/feed/disclosure.ts', import.meta.url), 'utf8')).replace(/^export /gm, '');
const out = process.env.MOTION_OUT;
if (out) await mkdir(out, { recursive: true });
test.after(async () => { await browser.close(); await close(); });

async function fixture({ boxSizing = 'border-box', scale = 1, reducedMotion = 'no-preference' } = {}) {
  const context = await browser.newContext({ reducedMotion, viewport: { width: 1200, height: 800 } });
  context.setDefaultTimeout(8000);
  const page = await context.newPage();
  await page.route('**/*', route => route.abort());
  await page.setContent(`<style>body{font:16px sans-serif;background:#fdfcfa;color:#262320}#wrap{transform:scale(${scale});transform-origin:0 0}#body{box-sizing:${boxSizing};padding:8px 10px 12px;border:2px solid #bbb;margin:8px 4px 6px;background:white;border-radius:12px;overflow:visible}#content{height:400px}</style><div id="wrap"><div id="body" hidden><div id="content">Содержимое раскрывающегося блока</div></div></div>`);
  await page.addScriptTag({ content: `(()=>{${source};window.slide=slide})()` });
  await page.evaluate(() => { window.b = document.querySelector('#body'); window.motionCloseCalls = 0; });
  return { context, page };
}

for (const boxSizing of ['border-box', 'content-box']) for (const scale of [1, 0.8]) {
  test(`reversals remain continuous: ${boxSizing}, scale ${scale}`, async () => {
    const { context, page } = await fixture({ boxSizing, scale });
    try {
      await page.evaluate(() => slide(b, true));
      await page.waitForTimeout(75);
      const closing = await page.evaluate(() => {
        const before = b.getBoundingClientRect().height;
        slide(b, false, { done: () => window.motionCloseCalls++ });
        return { before, after: b.getBoundingClientRect().height, animations: b.getAnimations().length };
      });
      assert.ok(Math.abs(closing.after - closing.before) < 1, JSON.stringify(closing));
      assert.equal(closing.animations, 1);
      await page.waitForTimeout(50);
      const opening = await page.evaluate(() => {
        const before = b.getBoundingClientRect().height;
        slide(b, true);
        return { before, after: b.getBoundingClientRect().height, animations: b.getAnimations().length };
      });
      assert.ok(Math.abs(opening.after - opening.before) < 1, JSON.stringify(opening));
      assert.equal(opening.animations, 1);
      await page.waitForFunction(() => b.getAnimations().length === 0);
      const settled = await page.evaluate(() => ({ hidden: b.hidden, closed: window.motionCloseCalls, height: b.getBoundingClientRect().height, overflow: b.style.overflow, cssOverflow: getComputedStyle(b).overflow }));
      assert.equal(settled.hidden, false); assert.equal(settled.closed, 0);
      assert.ok(Math.abs(settled.height - 424 * scale) < 1, JSON.stringify(settled));
      assert.equal(settled.overflow, ''); assert.equal(settled.cssOverflow, 'visible');
      await page.evaluate(() => slide(b, false, { done: () => window.motionCloseCalls++ }));
      await page.waitForFunction(() => b.hidden);
      assert.deepEqual(await page.evaluate(() => ({ height: b.getBoundingClientRect().height, active: b.getAnimations().length, closed: window.motionCloseCalls })), { height: 0, active: 0, closed: 1 });
    } finally { await context.close(); }
  });
}

test('new and delayed content is measured before targeting; active growth retargets without a jump', async () => {
  const { context, page } = await fixture();
  try {
    await page.evaluate(() => { b.replaceChildren(); slide(b, true, { mutate: () => { const c = document.createElement('div'); c.style.height = '80px'; b.append(c); } }); });
    await page.waitForTimeout(70);
    const first = await page.evaluate(() => b.getBoundingClientRect().height);
    assert.ok(first > 0 && first < 104, `cached content should already be opening: ${first}`);
    const changed = await page.evaluate(() => {
      const before = b.getBoundingClientRect().height;
      slide(b, true, { mutate: () => { b.firstElementChild.style.height = '400px'; } });
      return { before, after: b.getBoundingClientRect().height, active: b.getAnimations().length };
    });
    assert.ok(Math.abs(changed.before - changed.after) < 1, JSON.stringify(changed));
    assert.equal(changed.active, 1);
    await page.waitForTimeout(120);
    const middle = await page.evaluate(() => b.getBoundingClientRect().height);
    assert.ok(middle > changed.after && middle < 424, `loaded content grows before finish: ${middle}`);
    if (out) await page.screenshot({ path: resolve(out, 'disclosure-growing.png') });
    await page.waitForFunction(() => b.getAnimations().length === 0);
    assert.equal(await page.evaluate(() => b.getBoundingClientRect().height), 424);
  } finally { await context.close(); }
});

test('reduced motion or explicit instant mode finishes and cancels old callbacks', async () => {
  const { context, page } = await fixture();
  try {
    await page.evaluate(() => slide(b, true));
    await page.waitForTimeout(60);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => slide(b, false, { done: () => window.motionCloseCalls++ }));
    assert.deepEqual(await page.evaluate(() => ({ hidden: b.hidden, active: b.getAnimations().length, closed: window.motionCloseCalls })), { hidden: true, active: 0, closed: 1 });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.evaluate(() => slide(b, true, { animate: false }));
    assert.deepEqual(await page.evaluate(() => ({ hidden: b.hidden, active: b.getAnimations().length, height: b.getBoundingClientRect().height })), { hidden: false, active: 0, height: 424 });
    await page.evaluate(() => { b.animate = undefined; slide(b, false); });
    assert.equal(await page.evaluate(() => b.hidden), true);
  } finally { await context.close(); }
});

test('built application replaces a delayed loading placeholder with growing content', async () => {
  const url = await ready;
  const context = await browser.newContext({ viewport: { width: 1707, height: 882 } });
  context.setDefaultTimeout(8000);
  await context.route('**/*', route => new URL(route.request().url()).origin === url ? route.continue() : route.abort());
  let admit;
  const gate = new Promise(resolve => { admit = resolve; });
  await context.route('**/api/run/*', async route => { await gate; await route.continue(); });
  const runs = await (await context.request.get(url + '/api/runs')).json();
  const completed = runs.find(run => run.status === 'done' && run.chat_id === 'window');
  assert.ok(completed, 'fixture supplies a completed turn to open explicitly');
  await context.route('**/api/chat-turns/*', route => route.fulfill({ json: [
    { run_id: completed.id, ts: completed.created_at, kind: completed.kind, out: 'Проверка раскрытия после загрузки' },
  ] }));
  await context.addInitScript(() => { window.WebSocket = class { constructor() { throw Error('Fixture selects HTTP'); } }; localStorage.setItem('frame.panel', '0'); });
  const page = await context.newPage();
  try {
    await page.goto(url + '/?page=talk&scene=populated');
    const chosen = page.locator('#panel .turn[data-run=' + JSON.stringify(completed.id) + ']');
    await chosen.locator('.turn-head').evaluate(el => el.click());
    const loading = chosen.locator('.turn-body.loading');
    await loading.waitFor({ state: 'visible' });
    await page.waitForTimeout(450);
    await loading.evaluate(el => {
      window.motionLoadedBody = el;
      window.motionLoadFrames = [];
      const start = performance.now();
      const tick = () => {
        window.motionLoadFrames.push({ height: el.getBoundingClientRect().height, loading: el.classList.contains('loading'), active: el.getAnimations().length });
        if (performance.now() - start < 850) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    admit();
    await page.waitForFunction(() => !motionLoadedBody.classList.contains('loading') && motionLoadedBody.querySelector('.st-toggle'));
    await page.waitForTimeout(900);
    const samples = await page.evaluate(() => motionLoadFrames);
    const loaded = samples.filter(row => !row.loading);
    const first = loaded.find(row => row.active > 0);
    const finished = loaded.findIndex(row => row.active === 0);
    assert.ok(first && first.height < loaded.at(-1).height, JSON.stringify(loaded));
    assert.ok(finished > 0, 'loaded content finishes its reveal');
    assert.ok(Math.abs(loaded[finished].height - loaded[finished - 1].height) < 2, 'natural layout must not jump at animation completion');
    if (out) await page.screenshot({ path: resolve(out, 'application-loaded-turn.png') });
  } finally { admit(); await context.close(); }
});

test('built application opens populated turns and reverses nested details on repeated clicks', async () => {
  const url = await ready;
  const context = await browser.newContext({ viewport: { width: 1707, height: 882 }, reducedMotion: 'no-preference' });
  await context.route('**/*', route => new URL(route.request().url()).origin === url ? route.continue() : route.abort());
  await context.addInitScript(() => { window.WebSocket = class { constructor() { throw Error('Fixture selects HTTP'); } }; localStorage.setItem('frame.panel', '0'); });
  context.setDefaultTimeout(8000);
  const page = await context.newPage();
  try {
    await page.goto(url + '/?page=talk&scene=populated');
    await page.locator('#panel .turn').first().waitFor({ state: 'visible' });
    await page.waitForTimeout(900);
    const runId = await page.locator('#panel .turn').first().getAttribute('data-run');
    const turn = page.locator('#panel .turn[data-run=' + JSON.stringify(runId) + ']');
    if (await turn.locator('.turn-head').getAttribute('aria-expanded') !== 'true') {
      await turn.locator('.turn-head').evaluate(el => el.click());
    }
    const body = turn.locator('.turn-body');
    await body.waitFor({ state: 'visible' });
    await page.waitForFunction(() => [...document.querySelectorAll('#panel .turn-body')].some(b => !b.hidden && b.querySelector('.st-toggle')));
    const toggle = body.locator('.st-toggle').first();
    await toggle.evaluate(el => el.click());
    await page.waitForTimeout(45);
    // DOM clicks bypass Playwright's wait-for-stability so the reversal is real.
    await toggle.evaluate(el => el.click());
    await page.waitForTimeout(35);
    await toggle.evaluate(el => el.click());
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    const detail = toggle.locator('..').locator('.st-detail');
    await page.waitForTimeout(500);
    assert.equal(await detail.isVisible(), true);
    assert.equal(await detail.evaluate(el => el.getAnimations().length), 0);
    if (out) await page.screenshot({ path: resolve(out, 'application-turn-details.png') });
    await turn.locator('.turn-head').evaluate(el => el.click());
    await page.waitForTimeout(45);
    await turn.locator('.turn-head').evaluate(el => el.click());
    await page.waitForTimeout(500);
    assert.equal(await body.isVisible(), true);
    assert.ok(await body.evaluate(el => el.getBoundingClientRect().height > 20 && el.getAnimations().length === 0));
  } finally { await context.close(); }
});
