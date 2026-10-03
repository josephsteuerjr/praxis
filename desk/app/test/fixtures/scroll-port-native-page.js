// Real WKWebView/WebKitGTK, native Rust phase conversion and bridge. Wheel
// motion is synthetic; this does not certify the physical touchpad or routing.
(async()=>{
  const invoke=window.__TAURI_INTERNALS__.invoke;
  const wait=ms=>new Promise(r=>setTimeout(r,ms));let seq=0,checks=0;
  const phase=async kind=>{
    const next=++seq;await invoke('phase',{kind,seq:next});
    const until=Date.now()+1500;
    while(window.__HELENE_SCROLL_SESSION?.seq!==next){if(Date.now()>until)throw Error('native phase bridge did not acknowledge');await wait(5);}
  };
  const assert=(v,message)=>{checks++;if(!v)throw Error(message);};
  const cases=[];
  try {
    const support=window.__HELENE_SCROLL_SUPPORT;
    assert(!!support,'native support status missing');
    assert(support.source===window.__SCROLL_PROBE_EXPECT,'wrong native display backend');
    assert(support.hold===(support.source==='x11'?'unavailable':'unverified'),'wrong platform support claim');
    assert(['unverified','unavailable'].includes(support.hold),'physical acceptance claimed');
    for(const feel of ['brisk','smooth','syrup'])for(const over of ['rubber','stretch','none'])for(const dir of [-1,1]){
      const view=document.createElement('div');view.className='view';
      const inner=document.createElement('div');inner.className='inner';view.append(inner);lab.append(view);
      const s=new Scroller(view,inner,{feel,overscroll:over,stick:false});s.scrollTo(dir<0?0:99999,false);
      cases.push({s,view,dir,name:`${feel}/${over}/${dir}`,over});
    }
    const wheel=dy=>{for(const c of cases)c.view.dispatchEvent(new WheelEvent('wheel',{deltaY:c.dir*dy,cancelable:true}));};
    const shown=c=>Math.abs(c.s.debug().shown);
    await phase('manual');for(let i=0;i<30;i++){wheel(12.5);await wait(16);}await wait(80);
    const held=cases.map(shown);
    for(let i=0;i<45;i++){await wait(100);await phase('stationary');wheel(0);}
    cases.forEach((c,i)=>assert(shown(c)>=held[i]-2,`${c.name}: stationary session slipped`));
    await phase('end');
    // Native eval is asynchronous. Continuity is checked exactly by unit tests;
    // this probe checks that release is already moving without a quiet delay.
    await wait(160);cases.forEach((c,i)=>{if(c.over!=='none')assert(shown(c)<held[i]-5,`${c.name}: release delayed`);});
    await wait(1700);cases.forEach(c=>assert(shown(c)<1,`${c.name}: edge stuck`));
    await phase('manual');for(let i=0;i<25;i++){wheel(12.5);await wait(16);}
    await phase('inertia');for(let i=0;i<18;i++){wheel(2);await wait(40);await phase('inertia');}
    cases.forEach(c=>assert(shown(c)<8,`${c.name}: native inertia pumped edge`));
    await phase('manual');for(let i=0;i<16;i++){wheel(12.5);await wait(16);}
    cases.forEach(c=>{if(c.over!=='none')assert(shown(c)>40,`${c.name}: pickup blocked`);});
    await phase('cancel');await wait(1800);cases.forEach(c=>assert(shown(c)<1,`${c.name}: cancel stuck`));
    await phase('manual');for(let i=0;i<16;i++){wheel(12.5);await wait(16);}await wait(2300);
    cases.forEach(c=>assert(shown(c)<1,`${c.name}: dead bridge stuck`));
    for(const c of cases)c.s.destroy();
    await invoke('finish',{result:{ok:true,checks,cases:cases.length,support,hardwareAccepted:false,
      evidence:'Native phase conversion and bridge in system WebView; synthetic wheel motion',userAgent:navigator.userAgent}});
  }catch(e){await invoke('finish',{result:{ok:false,checks,error:String(e),hardwareAccepted:false}});}
})();
