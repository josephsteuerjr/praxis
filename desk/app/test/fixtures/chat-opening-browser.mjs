// Runs against ui-session-server's isolated built UI, never installed data.
import { strict as assert } from 'node:assert';

export async function verifyChatOpening(browser, url) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  try {
    await page.addInitScript(() => {
      window.__openingFrames = [];
      function sample() {
        const v = document.querySelector('#view');
        if (v?.querySelector('.msg')) window.__openingFrames.push({ top: v.scrollTop, max: v.scrollHeight-v.clientHeight });
        requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    });
    await page.goto(url);
    await page.locator('button[data-view="talk"]').click();
    await page.locator('.msg').first().waitFor();
    await page.waitForFunction(() => window.__openingFrames.length >= 30);
    const opening = await page.evaluate(() => window.__openingFrames.slice());
    assert.ok(opening[0].max > 1000, 'history must span several screens');
    for (const f of opening) assert.ok(Math.abs(f.max-f.top) <= 1, `opening visibly travelled: ${f.top}/${f.max}`);

    // Native scroll simulates reading old messages. A cached page must restore
    // this place, not apply the first-history bottom jump on every render.
    await page.evaluate(() => { document.querySelector('#view').scrollTop = 500; });
    await page.waitForTimeout(100);
    await page.locator('button[data-view="now"]').click();
    await page.locator('#view .msg').first().waitFor({state:'detached'});
    await page.evaluate(() => { window.__openingFrames = []; });
    await page.locator('button[data-view="talk"]').click();
    await page.waitForFunction(() => window.__openingFrames.length >= 20);
    const restored = await page.evaluate(() => window.__openingFrames.slice());
    for (const f of restored) assert.ok(Math.abs(f.top-500) <= 1, `lost reading position: ${f.top}`);
    return { opening, restored };
  } finally {
    await context.close();
  }
}
