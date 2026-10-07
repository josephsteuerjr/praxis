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
      const events=[];s.__PROBE_EVENTS=events;
      const release=s.release.bind(s);
      s.release=()=>{if(events.length<30)events.push({event:'release',t:performance.now(),active:s.wheelActive,
        releaseAt:s.releaseAt,sessionAt:s.sessionAt,session:s.session,stack:new Error().stack});release();};
      view.addEventListener('scroll',()=>{if(events.length<30)events.push({event:'scroll',t:performance.now(),
        top:view.scrollTop,written:s.written,pos:s.pos,target:s.target,active:s.wheelActive});},true);
      window.addEventListener('blur',()=>{if(events.length<30)events.push({event:'blur',t:performance.now()});},true);
      cases.push({s,view,dir,name:`${feel}/${over}/${dir}`,over});
    }
    const wheel=dy=>{for(const c of cases)c.view.dispatchEvent(new WheelEvent('wheel',{deltaY:c.dir*dy,cancelable:true}));};
    const hand=async count=>{
      // Production observers keep the phase bridge alive every 100ms during
      // motion too. A one-shot fixture can expire on a slower renderer before
      // its thirty manual wheel updates finish; that's a lost bridge, not hold.
      for(let i=0;i<count;i++){if(i%4===0)await phase(i?'stationary':'manual');wheel(12.5);await wait(16);}
    };
    const shown=c=>Math.abs(c.s.debug().shown);
    await hand(30);await wait(80);
    const held=cases.map(shown);window.__PROBE_HELD=held;
    for(let i=0;i<45;i++){await wait(100);await phase('stationary');wheel(0);}
    cases.forEach((c,i)=>assert(shown(c)>=held[i]-2,`${c.name}: stationary session slipped`));
    await phase('end');
    // Native eval is asynchronous. Continuity is checked exactly by unit tests;
    // this probe checks that release is already moving without a quiet delay.
    await wait(160);cases.forEach((c,i)=>{if(c.over!=='none')assert(shown(c)<held[i]-5,`${c.name}: release delayed`);});
    await wait(1700);cases.forEach(c=>assert(shown(c)<1,`${c.name}: edge stuck`));
    await hand(25);
    await phase('inertia');for(let i=0;i<18;i++){wheel(2);await wait(40);await phase('inertia');}
    cases.forEach(c=>assert(shown(c)<8,`${c.name}: native inertia pumped edge`));
    await hand(16);
    cases.forEach(c=>{if(c.over!=='none')assert(shown(c)>40,`${c.name}: pickup blocked`);});
    await phase('cancel');await wait(1800);cases.forEach(c=>assert(shown(c)<1,`${c.name}: cancel stuck`));
    await hand(16);await wait(2300);
    cases.forEach(c=>assert(shown(c)<1,`${c.name}: dead bridge stuck`));
    for(const c of cases)c.s.destroy();
    await invoke('finish',{result:{ok:true,checks,cases:cases.length,support,hardwareAccepted:false,
      evidence:'Native phase conversion and bridge in system WebView; synthetic wheel motion',userAgent:navigator.userAgent}});
  }catch(e){await invoke('finish',{result:{ok:false,checks,error:String(e),hardwareAccepted:false,
    phase:window.__HELENE_SCROLL_SESSION,now:Date.now(),held:window.__PROBE_HELD,
    userAgent:navigator.userAgent,
    samples:cases.map(c=>({name:c.name,debug:c.s.debug(),pos:c.s.pos,target:c.s.target,vel:c.s.vel,
      session:c.s.session,sessionAt:c.s.sessionAt,now:performance.now(),wheelActive:c.s.wheelActive,
      notch:c.s.notch,releaseAt:c.s.releaseAt,top:c.view.scrollTop,written:c.s.written,
      events:c.s.__PROBE_EVENTS}))}});}
})();
