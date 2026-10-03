import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';
let count=0,failed=0;
function check(name,fn){count++;try{fn();console.log('ok: '+name);}catch(e){failed++;console.error('FAIL: '+name+': '+e.message);}}
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) {
  check(`${feel}/${overscroll}/${dir}: impact dissipates momentum without cutting velocity`,()=>{
    const h=fixture({feel,overscroll,frameMs:1});
    h.s.scrollTo(dir<0?20:3000-688-20,false);h.s.fling(dir*4);
    let previous=h.s.vel,arrival=0,peak=0,rebound=0;
    for(let i=0;i<1500;i++) {
      h.advance(1);const shown=h.shown(),speed=h.s.vel;
      if(shown>0 && !arrival) {
        arrival=Math.abs(previous);
        assert.ok(speed*dir>arrival*0.85,`instant velocity cut ${previous} -> ${speed}`);
      }
      peak=Math.max(peak,shown);rebound=Math.max(rebound,-speed*dir);
      previous=speed;
    }
    assert.ok(arrival>3,'did not reach edge at speed');
    assert.ok(peak>2 && peak<80,`deformation is not a small residual: ${peak}`);
    assert.ok(rebound<arrival*0.12,`too much return momentum: ${rebound}/${arrival}`);
    assert.equal(h.shown(),0);assert.equal(h.view.scrollTop,dir<0?0:2312);
    h.s.destroy();
  });
}
check('the same pull by hand remains elastic and holds while captured',()=>{
  const h=fixture();h.pointer('pointerdown',100);h.pointer('pointermove',400);h.advance(500);
  const held=h.shown();assert.ok(held>70);h.advance(500);assert.ok(h.shown()>=held-1);
  h.pointer('pointerup',400);h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
});
check('a touchpad run-up and system tail leave only a small edge deformation',()=>{
  const h=fixture();h.s.scrollTo(400,false);
  for(const dy of [-30,-50,-70,-100,-120])h.wheel(dy);
  h.wheel(0);let peak=h.shown();
  for(const dy of [-90,-70,-50,-40,-30,-20,-12,-6,-3,-1]){h.wheel(dy);peak=Math.max(peak,h.shown());}
  for(let i=0;i<100;i++){h.advance(16);peak=Math.max(peak,h.shown());}
  assert.ok(peak>2 && peak<70,`system tail became a full hand pull: ${peak}`);
  assert.equal(h.shown(),0);assert.equal(h.view.scrollTop,0);h.s.destroy();
});
for(const at of [12,60,180]) check(`hand pickup at ${at}ms preserves position and velocity`,()=>{
  const h=fixture();h.s.scrollTo(20,false);h.s.fling(-4);h.advance(at);
  const position=h.s.pos,velocity=h.s.vel;
  h.pointer('pointerdown',100);
  assert.equal(h.s.pos,position);assert.equal(h.s.vel,velocity);
  h.pointer('pointermove',180);h.advance(200);
  assert.ok(h.shown()>Math.abs(position)+5,'pickup did not transfer control to the hand');
  h.pointer('pointerup',180);h.advance(1600);assert.equal(h.shown(),0);h.s.destroy();
});
check('a small wheel pickup during absorption keeps its motion and responds',()=>{
  const h=fixture();h.s.scrollTo(20,false);h.s.fling(-4);h.advance(80);
  const position=h.s.pos,velocity=h.s.vel;
  h.wheel(-6,0);assert.equal(h.s.pos,position);assert.equal(h.s.vel,velocity);
  h.advance(80);assert.ok(h.shown()>Math.abs(position)*0.7,'pickup was swallowed');
  h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
});
check('absorption stays consistent across frame rates',()=>{
  const peaks=[];
  for(const frameMs of [8,16,32]) {
    const h=fixture({frameMs});h.s.scrollTo(100,false);h.s.fling(-5);let peak=0;
    for(let i=0;i<1600/frameMs;i++){h.advance(frameMs);peak=Math.max(peak,h.shown());}
    peaks.push(peak);assert.equal(h.shown(),0);h.s.destroy();
  }
  assert.ok(Math.max(...peaks)-Math.min(...peaks)<2,`frame-dependent impact: ${peaks}`);
});
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) {
  check(`${feel}/${overscroll}/${dir}: continued wheel pull takes over before the return finishes`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?400:2312-400,false);
    for(const d of [30,50,70,100,120,180])h.wheel(dir*d);
    // No zero and no quiet gap: wheelActive must stay true throughout pickup.
    for(let t=0;t<120 && h.shown()<2;t+=2)h.advance(2);
    assert.ok(h.shown()>=2,'run-up did not reach the edge');
    h.advance(32);const before=h.shown();
    const position=h.s.pos,velocity=h.s.vel;
    h.wheel(dir*12.5,0);assert.equal(h.s.pos,position);assert.equal(h.s.vel,velocity);
    for(let i=0;i<7;i++)h.wheel(dir*12.5);
    h.advance(32);
    assert.ok(h.shown()>before+10,`pull waited for absorption: ${before} -> ${h.shown()}`);
    h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
  });
}
console.log(`${count-failed}/${count} impact checks passed`);if(failed)process.exitCode=1;
