import { strict as assert } from 'node:assert';
import { stripTypeScriptTypes } from 'node:module';

export async function verifyHandHoldBrowser(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const results=[];
  const read=()=>page.evaluate(()=>s.debug());
  const wheel=(dy,momentum=false)=>page.evaluate(({dy,momentum})=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,momentum,cancelable:true})),{dy,momentum});
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch','none']) for(const dir of [-1,1]) {
    const name=[feel,overscroll,dir].join('/');
    await page.evaluate(({feel,overscroll,dir})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false});s.scrollTo(dir<0?0:99999,false);
    },{feel,overscroll,dir});
    for(let j=0;j<30;j++){await wheel(dir*12.5);await page.clock.runFor(16);}
    await page.clock.runFor(64);const held=await read();let least=Math.abs(held.shown);
    for(let j=0;j<16;j++){
      await page.clock.runFor(80);await wheel(dir*0.4);const sample=await read();
      least=Math.min(least,Math.abs(sample.shown));
      assert.equal(sample.max,held.max,name+': transform changed the boundary');
    }
    if(overscroll!=='none'){
      assert.ok(Math.abs(held.shown)>65,name+': weak initial pull');
      assert.ok(least>=Math.abs(held.shown)-2,name+': slow moving fingers lost their pull');
    } else assert.equal(least,0,name+': none has a pull');
    const after=await read();
    let tailPeak=Math.abs(after.shown);
    for(const dy of [120,90,60,110,30,12,4,1]){
      await wheel(dir*dy,true);await page.clock.runFor(16);tailPeak=Math.max(tailPeak,Math.abs((await read()).shown));
    }
    assert.ok(tailPeak<Math.abs(after.shown)+4,name+': release pumped the held pull');
    await page.clock.runFor(1600);const final=await read();
    assert.equal(final.shown,0,name+': release stranded content');
    assert.equal(final.pos,dir<0?0:held.max,name+': wrong final boundary');
    results.push({name,held:held.shown,least,after:after.shown,tailPeak,final:final.shown});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();return results;
}
