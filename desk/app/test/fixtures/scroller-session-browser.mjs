import {strict as assert} from 'node:assert';
import {stripTypeScriptTypes} from 'node:module';
export async function verifySessionBrowser(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  let seq=0;const results=[];
  const phase=(active,momentum=false,source='macos')=>page.evaluate(v=>window.dispatchEvent(new CustomEvent('helene-scroll-session',{detail:{...v,sentAt:Date.now(),available:true}})),{active,momentum,source,seq:++seq});
  const wheel=dy=>page.evaluate(dy=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,cancelable:true})),dy);
  const shown=()=>page.evaluate(()=>Math.abs(s.debug().shown));
  for(const source of ['macos','wayland'])for(const feel of ['brisk','smooth','syrup'])for(const overscroll of ['rubber','stretch','none'])for(const dir of [-1,1]){
    await page.evaluate(({feel,overscroll,dir})=>{window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');i.style.transform='';v.scrollTop=0;window.s=new Scroller(v,i,{feel,overscroll,stick:false});s.scrollTo(dir<0?0:99999,false);},{feel,overscroll,dir});
    await phase(true,false,source);
    for(let i=0;i<30;i++){await wheel(dir*12.5);await page.clock.runFor(16);}
    await page.clock.runFor(64);const held=await shown();
    for(let i=0;i<50;i++){await page.clock.runFor(100);await phase(true,false,source);await wheel(0);}
    assert.ok(await shown()>=held-2,`${source}/${feel}/${overscroll}: stationary slip`);
    await phase(false,false,source);await page.clock.runFor(120);
    if(overscroll!=='none')assert.ok(await shown()<held-5,'delayed native end');
    await page.clock.runFor(1600);assert.equal(await shown(),0,'stranded edge');
    await phase(true,false,source);for(let i=0;i<30;i++){await wheel(dir*12.5);await page.clock.runFor(16);}
    await phase(false,true,source);for(let i=0;i<30;i++){await wheel(dir*2);await page.clock.runFor(40);await phase(false,true,source);}
    assert.ok(await shown()<8,'native session inertia repumped edge');
    await phase(true,false,source);for(let i=0;i<16;i++){await wheel(dir*12.5);await page.clock.runFor(16);}
    if(overscroll!=='none')assert.ok(await shown()>40,'pickup blocked');
    await page.clock.runFor(2300);assert.equal(await shown(),0,'expired source held edge');
    results.push({source,feel,overscroll,dir,held});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();return results;
}
