// Called with an isolated Playwright page; never opens installed data or sends API calls.
// A real Chromium layout is essential: transforms enlarge native scrollHeight.
import { strict as assert } from "node:assert";
import { stripTypeScriptTypes } from "node:module";

export async function verifyScrollerBrowser(page, source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({ content: stripTypeScriptTypes(source).replace(/^export /gm, "") + ";window.Scroller=Scroller;" });
  const results = [];
  for (const feel of ["brisk", "smooth", "syrup"]) for (const overscroll of ["rubber", "stretch", "none"]) for (const dir of [-1, 1]) {
    const name = `${feel}/${overscroll}/${dir}`;
    const base = await page.evaluate(({ feel, overscroll, dir }) => {
      window.s?.destroy();
      const v = document.querySelector('#v'), i = document.querySelector('#i');
      i.style.transform = ''; v.scrollTop = 0;
      window.s = new Scroller(v, i, { feel, overscroll, stick: false });
      s.scrollTo(dir < 0 ? 0 : 9999, false);
      return s.debug().max;
    }, { feel, overscroll, dir });
    for (let j = 0; j < 30; j++) {
      await page.evaluate(dy => document.querySelector('#v').dispatchEvent(new WheelEvent('wheel', { deltaY: dy, cancelable: true })), dir * 12.5);
      await page.clock.runFor(16);
    }
    const held = await page.evaluate(() => ({ ...s.debug(), nativeHeight: document.querySelector('#v').scrollHeight }));
    assert.equal(held.max, base, `${name}: transform moved the scroll boundary`);
    assert.equal(held.pos, dir < 0 ? 0 : base, `${name}: pull moved native scrollTop`);
    if (overscroll !== 'none') assert.ok(Math.abs(held.shown) > 40, `${name}: pull did not respond`);
    await page.evaluate(() => document.querySelector('#v').dispatchEvent(new WheelEvent('wheel', { deltaY: 0, cancelable: true })));
    const samples = [];
    for (let j = 0; j < 100; j++) {
      await page.clock.runFor(16);
      samples.push(await page.evaluate(() => s.debug()));
    }
    // The 90ms lift debounce can still finish following the last input. Thereafter
    // release must only approach zero, with no reversal and no moving edge.
    for (let j = 7; j < samples.length; j++) {
      assert.ok(Math.abs(samples[j].shown) <= Math.abs(samples[j-1].shown) + 0.15, `${name}: release reversed at frame ${j}`);
      assert.equal(samples[j].max, base, `${name}: release moved edge`);
    }
    assert.equal(samples.at(-1).shown, 0, `${name}: release did not finish`);
    results.push({ name, max: base, held: held.shown, released: samples.at(-1).shown });
  }
  await page.evaluate(() => s.destroy());
  await page.clock.resume();
  return results;
}
