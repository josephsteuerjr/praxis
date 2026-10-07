import { strict as assert } from 'node:assert';
import { stripTypeScriptTypes } from 'node:module';

export async function verifyContinuousPickup(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto}#i{height:3000px}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const results=[];
  const read=()=>page.evaluate(()=>({...s.debug(),position:s.pos,velocity:s.vel}));
  const wheel=dy=>page.evaluate(dy=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,cancelable:true})),dy);
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) {
    const name=[feel,overscroll,dir].join('/');
    await page.evaluate(({feel,overscroll,dir})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false});s.scrollTo(dir<0?400:s.debug().max-400,false);
    },{feel,overscroll,dir});
    for(const d of [30,50,70,100,120,180]){await page.clock.runFor(16);await wheel(dir*d);}
    for(let t=0;t<120 && Math.abs((await read()).raw)<2;t+=4)await page.clock.runFor(4);
    await page.clock.runFor(32);
    const before=await read();assert.ok(Math.abs(before.raw)>2,name+': did not enter impact');
    // Measure within one event dispatch so a browser frame cannot intervene.
    const event=await page.evaluate(dy=>{
      const before={p:s.pos,v:s.vel};document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,cancelable:true}));
      return {before,after:{p:s.pos,v:s.vel}};
    },dir*12.5);
    assert.deepEqual(event.after,event.before,name+': pickup jumped');
    for(let i=0;i<7;i++){await page.clock.runFor(16);await wheel(dir*12.5);}
    await page.clock.runFor(32);const after=await read();
    assert.ok(Math.abs(after.raw)>Math.abs(before.raw)+10,name+': same-stream pull was blocked');
    await page.clock.runFor(1500);assert.equal((await read()).raw,0,name+': pickup stranded the edge');
    results.push({name,before,after});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();return results;
}

export async function verifyImpactBrowser(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const results=[];
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch']) for(const dir of [-1,1]) for(const speed of [1.5,4,7]) {
    const name=[feel,overscroll,dir,speed].join('/');
    await page.evaluate(({feel,overscroll,dir,speed})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false,canGrab:()=>true});s.scrollTo(dir<0?20:s.debug().max-20,false);s.fling(dir*speed);
      window.trace=[];let left=95;
      function sample(){trace.push({...s.debug(),velocity:s.vel});if(--left)requestAnimationFrame(sample);}
      requestAnimationFrame(sample);
    },{feel,overscroll,dir,speed});
    await page.clock.runFor(1600);
    const trace=await page.evaluate(()=>window.trace);
    const peak=Math.max(...trace.map(s=>Math.abs(s.raw)));
    const rebound=Math.max(0,...trace.map(s=>-s.velocity*dir));
    assert.ok(peak>2 && peak<80,name+': excessive deformation '+peak);
    assert.ok(rebound/speed<0.15,name+': excessive return momentum '+rebound/speed);
    assert.equal(trace.at(-1).raw,0,name+': did not settle');
    assert.equal(trace.at(-1).pos,dir<0?0:trace.at(-1).max,name+': moved boundary');
    results.push({name,peak,reboundRatio:rebound/speed,trace});
  }
  // Real pointer capture, during the same moving spring.
  await page.evaluate(()=>{s.overscroll='rubber';s.scrollTo(20,false);s.fling(-4);});
  await page.clock.runFor(80);
  await page.mouse.move(100,100);
  await page.evaluate(()=>{
    const v=document.querySelector('#v');
    v.addEventListener('pointerdown',()=>{window.pickupBefore={position:s.pos,velocity:s.vel};},{capture:true,once:true});
    v.addEventListener('pointerdown',()=>{window.pickupAfter={position:s.pos,velocity:s.vel};},{once:true});
  });
  await page.mouse.down();
  const pickup=await page.evaluate(()=>({before:window.pickupBefore,after:window.pickupAfter}));
  assert.deepEqual(pickup.after,pickup.before,'pointer pickup restarted the motion');
  await page.mouse.move(100,200);await page.clock.runFor(160);
  assert.ok(Math.abs((await page.evaluate(()=>s.debug())).raw)>40,'pointer pickup lost control');
  await page.mouse.up();await page.clock.runFor(1600);
  assert.equal((await page.evaluate(()=>s.debug())).raw,0);
  await page.evaluate(()=>s.destroy());await page.clock.resume();
  return results;
}
