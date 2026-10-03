import { strict as assert } from "node:assert";
import { fixture } from "./fixtures/scroller-clock.mjs";
let failures = 0, checks = 0;
function check(name, fn) {
  checks++;
  try { fn(); console.log(`ok: ${name}`); }
  catch (e) { failures++; console.error(`FAIL: ${name}: ${e.message}`); }
}
for (const feel of ["brisk", "smooth", "syrup"]) {
  for (const overscroll of ["rubber", "stretch"]) {
    for (const dir of [-1, 1]) {
      check(`${feel}/${overscroll}/${dir}: grab keeps the visible position`, () => {
        const h = fixture({ feel, overscroll });
        h.s.scrollTo(dir < 0 ? 0 : 3000, false);
        h.wheel(0, 200); h.advance(200);
        for (let i = 0; i < 40; i++) h.wheel(dir * 12.5);
        h.wheel(0); h.advance(140);
        h.wheel(dir * 12.5, 0); // ambiguous first sample
        h.advance(16);
        const before = h.shown();
        h.wheel(dir * 12.5, 0); // confirmed pickup
        assert.ok(h.shown() >= before - 0.2, `pickup jumped ${before} -> ${h.shown()}`);
        h.advance(16);
        // Return velocity decelerates continuously when caught; it may travel a
        // few pixels before turning. It must not jump to a different coordinate.
        assert.ok(h.shown() >= before - 4, `first frame jumped ${before} -> ${h.shown()}`);
        h.s.destroy();
      });
    }
  }
}
check("slow new contact after an inertia tail responds within three samples", () => {
  const h = fixture();
  for (const d of [-20, -50, -80, 0, -60, -40, -20, -8, -3, -1]) h.wheel(d);
  h.advance(500);
  h.wheel(-1, 0); h.wheel(-2); h.wheel(-3);
  assert.equal(h.s.debug().pad, "fingers");
  for (let i = 0; i < 25; i++) h.wheel(-3);
  assert.ok(h.shown() > 15, `slow contact swallowed: ${h.shown()}`);
  h.advance(1500);
  assert.equal(h.shown(),0,"new slow contact must also release on silence");
  h.s.destroy();
});
check("release zeros do not indefinitely postpone the spring", () => {
  const h = fixture();
  for (let i = 0; i < 30; i++) h.wheel(-12.5);
  h.wheel(0);
  for (let i = 0; i < 40; i++) h.wheel(0, 32);
  assert.ok(h.shown() < 1, `zero stream held ${h.shown()} pixels`);
  h.s.destroy();
});
check("fast pixel stream starting with multiples of 120 still pulls", () => {
  const h = fixture();
  for (let i = 0; i < 8; i++) h.wheel(-120);
  h.advance(64);
  assert.ok(h.shown() > 30, `pixel stream mistaken for notches: ${h.shown()}`);
  h.s.destroy();
});
check("a decaying slow release without a zero cannot hold for eight seconds", () => {
  const h = fixture();
  h.wheel(0, 200); h.advance(200);
  for (const d of [-3, -6, -12, -20, -16, -10, -6, -3, -1]) h.wheel(d);
  h.advance(1500);
  assert.ok(h.shown() < 1, `lost release held ${h.shown()} pixels`);
  h.s.destroy();
});
check("new strong contact responds regardless of the previous gesture peak", () => {
  const h = fixture();
  for (const d of [-8, -12, -20, 0, -18, -15, -13]) h.wheel(d);
  h.wheel(-90); h.wheel(-95);
  assert.equal(h.s.debug().pad, "fingers", "new effort was discarded");
  h.s.destroy();
});
check("an anomalous speed spike stays bounded and cannot strand the spring", () => {
  const h = fixture();
  for (let i = 0; i < 30; i++) h.wheel(-12.5);
  h.wheel(0); h.wheel(-10); h.wheel(-8); h.wheel(-6); h.advance(80);
  const before = h.shown();
  h.wheel(-500); h.wheel(-150); h.wheel(-70); h.wheel(-25); h.wheel(-4); h.wheel(-3);
  assert.ok(Number.isFinite(before) && h.shown() <= 688*0.32*0.75+1,"spike escaped the visible limit");
  h.advance(1200); assert.equal(h.shown(), 0);
  h.s.destroy();
});
check("reverse pickup consumes visible overscroll only once", () => {
  const h = fixture();
  for (let i = 0; i < 35; i++) h.wheel(-12.5);
  h.wheel(0); h.advance(160);
  const before = h.shown();
  for (let i = 0; i < 10; i++) h.wheel(15);
  h.advance(1000);
  assert.ok(h.view.scrollTop > 150 - before - 15, `reverse was spent twice: ${h.view.scrollTop}`);
  assert.equal(h.shown(), 0);
  h.s.destroy();
});
check("silence releases a wheel pull without relying on a zero", () => {
  const h = fixture();
  h.wheel(0); h.advance(200);
  for (let i = 0; i < 35; i++) h.wheel(-12.5);
  assert.ok(h.shown()>40); h.advance(1800);
  assert.equal(h.shown(),0);
  h.wheel(0); h.advance(1500);
  assert.equal(h.shown(), 0);
  h.s.destroy();
});
check("tap without a drag cannot strand the overscroll", () => {
  const h=fixture(); h.wheel(0); h.advance(200);
  for(let i=0;i<35;i++) h.wheel(-12.5);
  h.advance(300); h.pointer('pointerdown'); h.pointer('pointerup');
  h.advance(3000);
  assert.equal(h.shown(),0,'no-move pointerup stranded the content'); h.s.destroy();
});
check("release from rest eases into motion instead of switching velocity instantly", () => {
  const h=fixture(); h.pointer('pointerdown',100); h.pointer('pointermove',450);
  h.advance(400); const a=h.shown();
  h.pointer('pointerup',450); h.advance(16); const b=h.shown();
  h.advance(16); const c=h.shown();
  assert.ok(a-b < b-c,`release starts with its largest jump: ${a-b}, then ${b-c}`);
  h.advance(1800); assert.equal(h.shown(),0); h.s.destroy();
});
check("loss of window focus releases a held gesture", () => {
  const h=fixture(); h.pointer('pointerdown',100); h.pointer('pointermove',450); h.advance(300);
  h.windowHandlers.blur(); h.advance(2000); assert.equal(h.shown(),0); h.s.destroy();
});
check("lost pointer capture releases without inventing a fling", () => {
  const h=fixture(); h.pointer('pointerdown',100); h.pointer('pointermove',450); h.advance(300);
  h.pointer('lostpointercapture',450); h.advance(2000); assert.equal(h.shown(),0); h.s.destroy();
});
check("an extreme wheel packet has bounded speed and settles", () => {
  const h=fixture({overscroll:'none'}); h.view.scrollHeight=30000; h.s.scrollTo(5000,false);
  h.wheel(1000000); let prev=h.view.scrollTop;
  for(let i=0;i<150;i++) {h.advance(16); assert.ok(Math.abs(h.view.scrollTop-prev)<=160.5,'unbounded frame movement');prev=h.view.scrollTop;}
  assert.ok(h.view.scrollTop<=5000+688*1.5+1,'unbounded queued travel');
  const settled=h.view.scrollTop;h.advance(3000);assert.equal(h.view.scrollTop,settled);h.s.destroy();
});
for (const feel of ["brisk", "smooth", "syrup"]) for (const overscroll of ["rubber", "stretch"]) for (const dir of [-1, 1]) {
check(`${feel}/${overscroll}/${dir}: small edge residue does not wait for the eight-second hold safety`, () => {
  const h=fixture({feel,overscroll}); h.s.scrollTo(dir<0?0:3000,false);
  h.wheel(0); h.advance(200);
  h.wheel(dir*6); h.advance(150);
  assert.ok(h.shown()>1,'small pull is visible while moving');
  h.advance(1000); assert.equal(h.shown(),0,'small residue parked near the edge');
  h.s.destroy();
});
}
check("opening the bottom immediately cancels outgoing edge motion", () => {
  const h=fixture(); for(let i=0;i<20;i++)h.wheel(-12.5);
  h.s.toBottom(false);
  assert.equal(h.view.scrollTop,h.view.scrollHeight-h.view.clientHeight);
  assert.equal(h.shown(),0,'opening retained the previous overscroll');
  h.advance(600); assert.equal(h.shown(),0); h.s.destroy();
});
console.log(`${checks - failures}/${checks} continuity checks passed`);
if (failures) process.exitCode = 1;
