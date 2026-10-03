import { strict as assert } from 'node:assert';
import { stripTypeScriptTypes } from 'node:module';

export async function verifyMomentumBrowser(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const results=[];
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch','none']) for(const dir of [-1,1]) {
    const name=[feel,overscroll,dir].join('/');
    const max=await page.evaluate(({feel,overscroll,dir})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false});s.scrollTo(dir<0?0:99999,false);return s.debug().max;
    },{feel,overscroll,dir});
    const wheel=(dy,momentum)=>page.evaluate(({dy,momentum})=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,momentum,cancelable:true})),{dy,momentum});
    const read=()=>page.evaluate(()=>({...s.debug(),velocity:s.vel,position:s.pos}));
    for(let i=0;i<30;i++){await wheel(dir*12.5,false);await page.clock.runFor(16);}
    await page.clock.runFor(32);const held=await read();
    const continuity=await page.evaluate(dy=>{
      const p=s.pos,v=s.vel;document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,momentum:true,cancelable:true}));
      return {p,v,nextP:s.pos,nextV:s.vel};
    },dir*120);
    assert.equal(continuity.nextP,continuity.p,name+': release jumped');assert.equal(continuity.nextV,continuity.v,name+': release cut velocity');
    let peak=Math.abs(held.shown);
    for(const dy of [110,95,80,55,130,35,20,12,7,3,1]) {
      await page.clock.runFor(16);await wheel(dir*dy,true);const d=await read();peak=Math.max(peak,Math.abs(d.shown));
      assert.equal(d.max,max,name+': transform moved the boundary');
    }
    assert.ok(peak<Math.abs(held.shown)+4,name+': inertia inflated the edge');
    await page.clock.runFor(80);const pickup=await read();
    for(let i=0;i<10;i++){await wheel(dir*6,false);await page.clock.runFor(16);}
    const after=await read();
    if(overscroll!=='none')assert.ok(Math.abs(after.shown)>Math.abs(pickup.shown)+3,name+': new hand input blocked');
    await page.clock.runFor(1600);const final=await read();assert.equal(final.shown,0,name+': stranded edge');
    assert.equal(final.pos,dir<0?0:max,name+': boundary not restored');
    results.push({name,held:held.shown,tailPeak:peak,pickup:pickup.shown,after:after.shown,final:final.shown});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();return results;
}
