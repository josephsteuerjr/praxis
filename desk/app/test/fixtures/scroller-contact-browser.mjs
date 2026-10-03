import { strict as assert } from 'node:assert';
import { stripTypeScriptTypes } from 'node:module';
export async function verifyContactBrowser(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const contact=(contacts,available=true)=>page.evaluate(({contacts,available})=>window.dispatchEvent(new CustomEvent('helene-touchpad-contact',{detail:{contacts,available}})),{contacts,available});
  const wheel=dy=>page.evaluate(dy=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,momentum:false,cancelable:true})),dy);
  const read=()=>page.evaluate(()=>s.debug());const results=[];
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch','none']) for(const dir of [-1,1]) {
    const name=[feel,overscroll,dir].join('/');
    await page.evaluate(({feel,overscroll,dir})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false});s.scrollTo(dir<0?0:99999,false);
    },{feel,overscroll,dir});
    await contact(2);
    for(let j=0;j<30;j++){await wheel(dir*12.5);await page.clock.runFor(16);}
    await page.clock.runFor(64);const held=await read();let least=Math.abs(held.shown);
    for(let j=0;j<50;j++){
      await page.clock.runFor(100);await contact(2);if(j%2===0)await wheel(0);
      const sample=await read();least=Math.min(least,Math.abs(sample.shown));assert.equal(sample.max,held.max,name+': boundary changed');
    }
    assert.ok(least>=Math.abs(held.shown)-2,name+': stationary contacts lost the pull');
    const continuity=await page.evaluate(()=>{
      const p=s.pos,v=s.vel;window.dispatchEvent(new CustomEvent('helene-touchpad-contact',{detail:{contacts:0,available:true}}));
      return {p,v,nextP:s.pos,nextV:s.vel};
    });
    assert.equal(continuity.nextP,continuity.p,name+': release jumped');assert.equal(continuity.nextV,continuity.v,name+': velocity cut');
    await page.clock.runFor(120);const moving=await read();
    if(overscroll!=='none')assert.ok(Math.abs(moving.shown)<Math.abs(held.shown)-5,name+': lift was delayed');
    await page.clock.runFor(1600);const final=await read();assert.equal(final.shown,0,name+': stranded edge');
    results.push({name,held:held.shown,least,moving:moving.shown,final:final.shown});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();return results;
}
