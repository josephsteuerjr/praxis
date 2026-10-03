import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';
let failed=0, count=0;
function check(name,fn){count++;try{fn();console.log('ok: '+name);}catch(e){failed++;console.error('FAIL: '+name+': '+e.message);}}
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) {
  check(`${feel}/${overscroll}/${dir}: first input after zero is never swallowed`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:3000,false);
    h.wheel(dir*40);h.wheel(0);h.advance(1000);
    h.wheel(dir*12.5);h.advance(64);
    assert.ok(h.shown()>1,'first real input produced no movement');h.s.destroy();
  });
  check(`${feel}/${overscroll}/${dir}: a declining new gesture still responds`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:3000,false);
    h.wheel(dir*40);h.wheel(0);h.advance(1000);
    for(const d of [24,17,11,6,3])h.wheel(dir*d);
    h.advance(32);assert.ok(h.shown()>5,'declining input disappeared');
    h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
  });
  check(`${feel}/${overscroll}/${dir}: silence always releases even a large pull`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:3000,false);
    h.wheel(0);h.advance(200);for(let i=0;i<40;i++)h.wheel(dir*12.5);
    assert.ok(h.shown()>40);h.advance(1500);assert.equal(h.shown(),0,'large pull remained held');h.s.destroy();
  });
}
check('reversing direction discards queued travel from the old gesture',()=>{
  const h=fixture();h.view.scrollHeight=30000;h.s.scrollTo(5000,false);
  h.wheel(900);h.advance(32);const atTurn=h.view.scrollTop;
  for(let i=0;i<8;i++)h.wheel(-10);
  h.advance(180);assert.ok(h.view.scrollTop<atTurn,'opposite input kept travelling the old way');h.s.destroy();
});
check('a long tiny tail cannot keep a large pull parked at the limit',()=>{
  const h=fixture();for(let i=0;i<20;i++)h.wheel(-100);
  const peak=h.shown();assert.ok(peak>70);
  for(let i=0;i<150;i++)h.wheel(-0.5);
  assert.ok(h.shown()<10,`tiny tail kept ${h.shown()} of ${peak}px`);
  h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
});
console.log(`${count-failed}/${count} input checks passed`);if(failed)process.exitCode=1;
