import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';
let checks=0;
for(const source of ['macos','wayland']) for(const feel of ['brisk','smooth','syrup']) for(const over of ['rubber','stretch','none']) for(const dir of [-1,1]) {
  const h=fixture({feel,overscroll:over});h.s.scrollTo(dir<0?0:2312,false);h.session(true,false,{source});
  for(let i=0;i<30;i++)h.wheel(dir*12.5);
  h.advance(64);const held=h.shown();
  for(let i=0;i<50;i++){h.advance(100);h.session(true,false,{source});h.wheel(0,0);}
  assert.ok(h.shown()>=held-2,`${source}/${feel}/${over}: stationary session slipped`);
  const p=h.s.pos,v=h.s.vel;h.session(false,false,{source});
  assert.equal(h.s.pos,p);assert.equal(h.s.vel,v);h.advance(120);
  if(over!=='none')assert.ok(h.shown()<held-5,'end waited for wheel timeout');
  h.advance(1800);assert.equal(h.shown(),0);h.s.destroy();checks++;
}
for(const loss of ['lease','blur','unavailable','old-seq','old-time']) {
  const h=fixture();h.session(true);for(let i=0;i<30;i++)h.wheel(-12.5);
  if(loss==='blur')h.windowHandlers.blur();
  if(loss==='unavailable')h.session(false,false,{available:false});
  if(loss.startsWith('old')){
    h.session(false);h.session(true,false,loss==='old-seq'?{seq:0}:{sentAt:Date.now()-1000});
  }
  h.advance(2200);assert.equal(h.shown(),0,`${loss} left the edge stuck`);h.s.destroy();checks++;
}
{
  const h=fixture();h.session(true);for(let i=0;i<30;i++)h.wheel(-12.5);
  const held=h.shown();h.session(false,true);
  // A WebKit version with no DOM momentum property still uses native phase.
  for(let i=0;i<30;i++){h.wheel(-2,40);h.session(false,true);}
  assert.ok(h.shown()<held/4,'native inertia pumped the edge');
  h.session(true);for(let i=0;i<12;i++)h.wheel(-12.5);
  assert.ok(h.shown()>40,'new hand input could not pick up');h.s.destroy();checks++;
}
{
  const h=fixture();h.session(true);h.wheel(-3,16,{deltaMode:1});
  for(let i=0;i<20;i++){h.advance(100);h.session(true);}
  assert.equal(h.shown(),0,'mouse notch was held by gesture channel');h.s.destroy();checks++;
}
console.log(`${checks} scroll session cases passed`);
