import { strict as assert } from 'node:assert';
import { fixture } from './fixtures/scroller-clock.mjs';

let failures=0,checks=0;
function check(name,fn){checks++;try{fn();console.log('ok: '+name);}catch(e){failures++;console.error('FAIL: '+name+': '+e.message);}}
for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) {
  check(`${feel}/${overscroll}/${dir}: native momentum cannot pump the held edge`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:2312,false);
    for(let i=0;i<30;i++)h.wheel(dir*12.5,16,{momentum:false});
    h.advance(32);const held=h.shown(),p=h.s.pos,v=h.s.vel;
    h.wheel(dir*120,0,{momentum:true});
    assert.equal(h.s.pos,p);assert.equal(h.s.vel,v,'release must preserve velocity');
    let peak=h.shown();
    for(const dy of [110,95,80,55,130,35,20,12,7,3,1]){h.wheel(dir*dy,16,{momentum:true});peak=Math.max(peak,h.shown());}
    assert.ok(peak<held+4,`tail inflated the edge ${held} -> ${peak}`);
    h.advance(1300);assert.equal(h.shown(),0);h.s.destroy();
  });
  check(`${feel}/${overscroll}/${dir}: slow new hand input interrupts native momentum immediately`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?0:2312,false);
    for(let i=0;i<25;i++)h.wheel(dir*12.5,16,{momentum:false});
    for(const dy of [80,55,25,12])h.wheel(dir*dy,16,{momentum:true});
    h.advance(80);const before=h.shown(),p=h.s.pos,v=h.s.vel;
    h.wheel(dir*6,0,{momentum:false});assert.equal(h.s.pos,p);assert.equal(h.s.vel,v);
    for(let i=0;i<9;i++)h.wheel(dir*6,16,{momentum:false});
    assert.ok(h.shown()>before+3 && h.s.vel*dir>0,`renewed pull did not respond ${before} -> ${h.shown()}`);
    h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
  });
  check(`${feel}/${overscroll}/${dir}: runaway native inertia is absorbed at the edge`,()=>{
    const h=fixture({feel,overscroll});h.s.scrollTo(dir<0?400:1912,false);
    for(const dy of [30,50,70,100,120,180])h.wheel(dir*dy,16,{momentum:false});
    let peak=0,rebound=0;
    for(const dy of [180,140,220,100,85,70,50,40,30,20,12,8,4,2,1]){
      h.wheel(dir*dy,16,{momentum:true});peak=Math.max(peak,h.shown());rebound=Math.max(rebound,-dir*h.s.vel);
    }
    assert.ok(peak<55,`excessive residual deformation ${peak}`);
    assert.ok(rebound<0.4,`edge threw the content back: ${rebound}`);
    h.advance(1600);assert.equal(h.shown(),0);h.s.destroy();
  });
}
for(const height of [400,688,1100]) for(const feel of ['brisk','smooth','syrup']) {
  check(`${feel}/${height}: the same deliberate pull has a calibrated size`,()=>{
    const h=fixture({feel});h.view.clientHeight=height;h.s.scrollTo(0,false);
    h.pointer('pointerdown',100);h.pointer('pointermove',400);h.advance(500);
    assert.ok(h.shown()>=68 && h.shown()<=101,`300px hand pull produced ${h.shown()}px`);
    h.pointer('pointerup',400);h.advance(1500);assert.equal(h.shown(),0);h.s.destroy();
  });
}
check('native momentum inside the content still scrolls toward the edge',()=>{
  const h=fixture();h.s.scrollTo(1000,false);h.wheel(-80,16,{momentum:true});h.advance(300);
  assert.ok(h.view.scrollTop<980,'inertia inside the document was swallowed');h.s.destroy();
});
console.log(`${checks-failures}/${checks} momentum checks passed`);if(failures)process.exitCode=1;
