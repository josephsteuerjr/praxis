// Owner acceptance: even slowly moving fingers must hold the pulled content.
// Keep the release/impact contract separate from sustained hand input.
import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';

let failures=0,checks=0;
function check(name,fn){checks++;try{fn();console.log('ok: '+name);}catch(e){failures++;console.error('FAIL: '+name+': '+e.message);}}
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch'])
for(const dir of [-1,1]) for(const frameMs of [8,16,33]) for(const reported of [false,true]) {
  const extra=reported?{momentum:false}:{};
  check(`${feel}/${overscroll}/${dir}/${frameMs}/${reported}: slowly moving fingers keep their pull`,()=>{
    const h=fixture({feel,overscroll,frameMs});h.s.scrollTo(dir<0?0:2312,false);
    for(let i=0;i<30;i++)h.wheel(dir*12.5,16,extra);
    h.advance(64);const held=h.shown();assert.ok(held>50,`initial pull is too weak: ${held}`);
    let least=held;
    // A sparse trackpad stream is still an active gesture, well before quiet.
    for(let i=0;i<16;i++){h.wheel(dir*0.4,80,extra);least=Math.min(least,h.shown());}
    assert.ok(least>=held-2,`hand pull collapsed while fingers moved: ${held} -> ${least}`);
    h.advance(1800);assert.equal(h.shown(),0,'silence must still settle at the edge');
    h.s.destroy();
  });
}
for(const feel of ['brisk','smooth','syrup']) for(const dir of [-1,1]) {
  check(`${feel}/${dir}: momentum after a held pull still dissipates`,()=>{
    const h=fixture({feel});h.s.scrollTo(dir<0?0:2312,false);
    for(let i=0;i<30;i++)h.wheel(dir*12.5,16,{momentum:false});
    for(let i=0;i<12;i++)h.wheel(dir*0.4,80,{momentum:false});
    const held=h.shown();let peak=held;
    for(const dy of [120,90,65,110,35,20,9,4,1]){h.wheel(dir*dy,16,{momentum:true});peak=Math.max(peak,h.shown());}
    assert.ok(peak<held+4,`released inertia pumped the edge: ${held} -> ${peak}`);
    h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
  });
}
console.log(`${checks-failures}/${checks} hand hold checks passed`);if(failures)process.exitCode=1;
