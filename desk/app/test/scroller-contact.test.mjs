// Physical acceptance: two fingers stand still for five seconds, then lift.
import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';
let checks=0,failures=0;
function check(name,fn){checks++;try{fn();console.log('ok: '+name);}catch(e){failures++;console.error('FAIL: '+name+': '+e.message);}}
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch','none']) for(const dir of [-1,1]) {
  check(`${feel}/${overscroll}/${dir}: stationary contacts hold, actual lift releases`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:2312,false);h.contact(2);
    for(let i=0;i<30;i++)h.wheel(dir*12.5,16,{momentum:false});
    h.advance(64);const held=h.shown();
    if(overscroll!=='none')assert.ok(held>65,`hand pull too weak ${held}`);
    let least=held;
    for(let i=0;i<50;i++){h.advance(100);h.contact(2);if(i%2===0)h.wheel(0,0);least=Math.min(least,h.shown());}
    assert.ok(least>=held-2,`stationary fingers lost their pull ${held} -> ${least}`);
    const p=h.s.pos,v=h.s.vel;h.contact(0);
    assert.equal(h.s.pos,p,'lift jumped');assert.equal(h.s.vel,v,'lift cut velocity');
    h.advance(120);if(overscroll!=='none')assert.ok(h.shown()<held-5,'real lift waited for a quiet timeout');
    h.advance(1600);assert.equal(h.shown(),0);h.s.destroy();
  });
}
for(const loss of ['bridge','device','blur']) check(`${loss}: missing contact source cannot strand the edge`,()=>{
  const h=fixture();h.contact(2);for(let i=0;i<30;i++)h.wheel(-12.5,16,{momentum:false});
  if(loss==='device')h.contact(0,false);
  if(loss==='blur')h.windowHandlers.blur();
  h.advance(2200);assert.equal(h.shown(),0);h.s.destroy();
});
check('marked native inertia never holds even with an old contact snapshot',()=>{
  const h=fixture();h.contact(2);for(let i=0;i<30;i++)h.wheel(-12.5,16,{momentum:false});
  for(let i=0;i<50;i++){h.wheel(-2,100,{momentum:true});h.contact(2);}
  assert.ok(h.shown()<3,'inertia was held by contacts');h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
});
check('mouse wheel is not held by resting touchpad fingers',()=>{
  const h=fixture();h.contact(2);h.wheel(-3,16,{deltaMode:1});
  for(let i=0;i<20;i++){h.advance(100);h.contact(2);}
  assert.equal(h.shown(),0);h.s.destroy();
});
console.log(`${checks-failures}/${checks} contact checks passed`);if(failures)process.exitCode=1;
