import { strict as assert } from 'node:assert';
import { stripTypeScriptTypes } from 'node:module';

export async function verifyCrossing(page,source) {
  await page.setContent('<style>#v{height:688px;overflow:auto;padding:4px 32px 24px;box-sizing:border-box}#i{height:3000px;display:flow-root}</style><div id="v"><div id="i"></div></div>');
  await page.clock.install();
  await page.addScriptTag({content:stripTypeScriptTypes(source).replace(/^export /gm,'')+';window.Scroller=Scroller;'});
  const results=[];
  const read=()=>page.evaluate(()=>({...s.debug(),top:document.querySelector('#v').scrollTop}));
  async function wheel(dy,ms=16) {
    await page.evaluate(dy=>document.querySelector('#v').dispatchEvent(new WheelEvent('wheel',{deltaY:dy,cancelable:true})),dy);
    await page.clock.runFor(ms);
    return read();
  }
  for(const feel of ['brisk','smooth','syrup']) for(const overscroll of ['rubber','stretch','none']) for(const dir of [-1,1]) {
    const name=feel+'/'+overscroll+'/'+dir;
    const initial=await page.evaluate(({feel,overscroll,dir})=>{
      window.s?.destroy();const v=document.querySelector('#v'),i=document.querySelector('#i');
      i.style.transform='';i.style.height='3000px';v.scrollTop=0;
      window.s=new Scroller(v,i,{feel,overscroll,stick:false});
      s.scrollTo(dir<0?150:s.debug().max-150,false);return s.debug();
    },{feel,overscroll,dir});
    let previous=initial.pos;
    for(const delta of [5,8,15,25,50,100,120,60,30,12,4,1]) {
      const state=await wheel(dir*delta);
      const coordinate=state.pos+state.raw;
      // Внутри ленты ввод однонаправлен. За краем слабый хвост уже уступает
      // возвратной силе — не обязан удерживать накопленную оттяжку.
      if(previous>=0 && previous<=initial.max && coordinate>=0 && coordinate<=initial.max)
        assert.ok((coordinate-previous)*dir>=-1,name+': reversed inside scroll range');
      assert.ok(Math.abs(coordinate-previous)<=161,name+': discontinuous edge crossing');
      assert.equal(state.max,initial.max,name+': transform changed bounds');
      previous=coordinate;
    }
    await page.clock.runFor(1500);
    const parked=await read();
    assert.equal(parked.raw,0,name+': stranded after crossing');
    assert.equal(parked.top,dir<0?0:initial.max,name+': parked short of boundary');
    await wheel(0);
    const small=await wheel(dir*6,64);
    if(overscroll!=='none')assert.ok(Math.abs(small.raw)>1,name+': small next gesture swallowed');
    for(let i=0;i<16;i++)await wheel(-dir*10);
    await page.clock.runFor(1000);
    const reverse=await read();
    assert.ok(Math.abs(reverse.top-parked.top)>130,name+': reverse stuck near edge');
    assert.equal(reverse.raw,0);
    // Shrink during queued motion: the old, now unreachable destination must die.
    await wheel(1500);
    await page.evaluate(()=>{document.querySelector('#i').style.height='900px';});
    await page.clock.runFor(1800);
    const resized=await read();
    assert.equal(resized.raw,0,name+': resize stranded overscroll');
    assert.ok(resized.top<=resized.max,name+': resize retained obsolete destination');
    results.push({name,parked,small,reverse,resized});
  }
  await page.evaluate(()=>s.destroy());await page.clock.resume();
  return results;
}
