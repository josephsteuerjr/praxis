// Real browser DOM, real main.show/Now/Forge/activity source; only transport and
// unrelated view dependencies are fixtures. No installed app/profile or build.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const source=path=>readFileSync(new URL('../../ui-kit/window/'+path,import.meta.url),'utf8');
const compile=text=>stripTypeScriptTypes(text.replace(/^import .*;\r?\n/gm,'')).replace(/^export /gm,'');
const main=source('main.ts'),start=main.indexOf('  async function show(id: View,');
const end=main.indexOf('\n  // ---------------------------------------------------------------- комнаты',start);
assert.ok(start>=0 && end>start,'execute complete production navigation owner');
const code={work:compile(source('work-activity.ts')),forge:compile(source('views/forge-panel.ts')),
  now:compile(source('views/now.ts')),show:compile(main.slice(start,end)),
  go:compile(main.slice(main.indexOf('  addEventListener("frame-go",'),main.indexOf('  addEventListener("frame-restart",')))};
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
const task=(id='fresh')=>({id,goal:'Task '+id,status:'active',agents:[{id:'unit-'+id,role:'worker',model:'fixture-model',subtask:'Subtask '+id,status:'running',observation:{generation:'g-one'},result_ref:{kind:'result',sha256:'a'.repeat(64)}}]});
async function fixture(mountNow=true) {
  const page=await browser.newPage();
  await page.setContent('<main id="app"><div id="view"><div id="inner"></div></div></main>');
  await page.evaluate(({code})=>{
    const cfg={base:'fixture',key:'secret-fixture',agent:'one'},S={view:'now',runs:[],evCache:new Map(),agentState:{phrase:'Ready',runner:{alive:true,busy:false}}};
    const calls=[],timers=new Map(),pending=new Map();let timerId=0;
    const state={active:[],recent:[],delay:'',detailDelay:false,detailCalls:0,detailResult:undefined};
    const read=async(url,options)=>{
      const path=String(url);calls.push({path,signal:options.signal});
      if(state.delay && path.includes(state.delay))return await new Promise(resolve=>pending.set(state.delay,resolve));
      if(path.startsWith('/api/forge/tasks'))return {ok:true,json:async()=>({active:state.active,recent:state.recent,next_cursor:null})};
      if(path.startsWith('/api/forge/events'))return {ok:true,json:async()=>({events:[],next_offset:0,has_more:false,generation:'g-one'})};
      if(path.startsWith('/api/forge/control'))return {ok:true,json:async()=>({commands:[]})};
      throw Error('Unexpected transport '+path);
    };
    const noop=()=>'',schedule=fn=>{timers.set(++timerId,fn);return timerId},clear=id=>timers.delete(id);
    const impl=new Function('document','cfg','S','fetch','ApiError','channelURL','setTimeout','clearTimeout','window','api',
      'mountUsage','usageShell','frameStripHTML','activityHTML','updateActivity','foreignHarness','runIsRecent',
      'bindRuns','loadWords','runDetail','runRowHTML','esc','fmtDur','fmtTime','ACTIONS',
      code.work+'\n'+code.forge+'\n'+code.now+'\nreturn {render,dispose,onEvent,runsChanged,publishActivity,openAgents:typeof openAgents==="function" ? openAgents : undefined,subscriptionCount:()=>listeners.size};')(
      document,cfg,S,read,Error,p=>p,schedule,clear,{setTimeout:schedule},noop,noop,noop,noop,
      r=>'<article id="turn-live" data-run="'+r.id+'"><div id="turn-live-steps"></div><span id="turn-live-t"></span></article>',
      (card,d)=>card.dataset.detail=d.text,()=>false,()=>true,noop,async()=>{},
      async()=>{state.detailCalls++;return state.detailDelay ? await new Promise(resolve=>pending.set('detail',resolve)) : state.detailResult},noop,String,String,String,{});
    const pages=new Map(),view=document.querySelector('#view'),viewInner=document.querySelector('#inner'),app=document.querySelector('#app');
    const pageFor=id=>{if(!pages.has(id)){const node=document.createElement('section');node.dataset.page=id;pages.set(id,node);}return pages.get(id)};
    const views={now:impl,learn:{render:async node=>{node.textContent='Learn fixture'}},plans:{render:async()=>{}}};
    const show=new Function('now','S','views','pageFor','pages','view','viewInner','app','scroll','scrolls',
      'syncRail','SECTIONS','FOOT','headKicker','headTitle','composer','panelBox','panelBtn','isDirty','announce','homeScroll','toast','plans','panel','failHTML','bindFail','humanError',
      'let showSeq=0;\n'+code.show+'\nreturn show;')(
      impl,S,views,pageFor,pages,view,viewInner,app,{view:()=>null,sectionShown:()=>{}},new Map(),noop,[],[],
      document.createElement('span'),document.createElement('h1'),document.createElement('div'),document.createElement('div'),document.createElement('button'),
      ()=>false,noop,()=>0,noop,{dispose:noop},{dispose:noop},e=>String(e),noop,e=>({text:String(e)}));
    let go;
    new Function('addEventListener','show','now',code.go)((name,handler)=>{go=target=>handler({detail:target})},show,impl);
    window.fixture={cfg,S,impl,state,show,go,calls,timers,pending,pages,
      publish:value=>impl.publishActivity(cfg.base+'\n'+cfg.key+'\n'+cfg.agent,value),
      release:(key,value)=>{const resolve=pending.get(key);pending.delete(key);resolve(key==='detail' ? value : {ok:true,json:async()=>value});}};
  },{code});
  await page.evaluate(mountNow=>window.fixture.show(mountNow ? 'now' : 'learn'),mountNow);
  return page;
}
async function openDrawer(page) {await page.locator('.now-agents > summary').click();}
async function settle(page) {await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,0)));}
test.after(async()=>{await browser.close()});

test('main navigation aborts delayed Forge list; rapid return rejects stale response and keeps disclosure',async()=>{
  const page=await fixture();try {
    assert.equal(await page.evaluate(()=>window.fixture.calls.length),0,'closed drawer is passive');
    await page.evaluate(()=>{window.fixture.state.delay='/api/forge/tasks'});await openDrawer(page);
    await page.waitForFunction(()=>window.fixture.pending.has('/api/forge/tasks'));
    await page.evaluate(()=>window.fixture.show('learn'));
    assert.deepEqual(await page.evaluate(()=>({aborted:window.fixture.calls[0].signal.aborted,subscriptions:window.fixture.impl.subscriptionCount(),timers:window.fixture.timers.size})),{aborted:true,subscriptions:0,timers:0});
    await page.evaluate(()=>{window.fixture.state.delay=''});
    await page.evaluate(()=>window.fixture.show('now'));
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),true,'same owner disclosure survives return');
    await page.evaluate(stale=>window.fixture.release('/api/forge/tasks',{active:[stale],recent:[],next_cursor:null}),task('STALE'));
    await settle(page);assert.doesNotMatch(await page.locator('.now-agents').textContent(),/STALE/);
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),1);
    await page.evaluate(()=>window.fixture.show('now'));await settle(page);
    assert.equal(await page.evaluate(()=>window.fixture.calls.filter(c=>c.path.startsWith('/api/forge/tasks')).length),2,'rerendering Now does not immediately repoll open drawer');
    await openDrawer(page);await settle(page);
    assert.deepEqual(await page.evaluate(()=>[window.fixture.timers.size,window.fixture.impl.subscriptionCount()]),[0,0]);
  } finally {await page.close()}
});

test('payload request is aborted on navigation, late payload cannot paint after return',async()=>{
  const page=await fixture();try {
    await page.evaluate(value=>{window.fixture.state.active=[value]},task());await openDrawer(page);
    await page.locator('.forge-task > summary').click();await page.locator('.forge-unit > summary').click();
    await page.evaluate(()=>{window.fixture.state.delay='/api/forge/payload'});
    await page.getByRole('button',{name:'Итог участника',exact:true}).click();await page.waitForFunction(()=>window.fixture.pending.has('/api/forge/payload'));
    await page.evaluate(()=>window.fixture.show('learn'));
    assert.equal(await page.evaluate(()=>window.fixture.calls.find(c=>c.path.startsWith('/api/forge/payload')).signal.aborted),true);
    await page.evaluate(()=>{window.fixture.state.delay=''});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>window.fixture.release('/api/forge/payload',{sha256:'a'.repeat(64),offset:0,text:'STALE_PAYLOAD',total_chars:13,next_offset:null}));await settle(page);
    assert.doesNotMatch(await page.locator('.now-agents').textContent(),/STALE_PAYLOAD/);
    assert.equal(await page.locator('.forge-unit').evaluate(e=>e.open),true);
    assert.match(await page.locator('.forge-unit').textContent(),/Чтение приостановлено/);
  } finally {await page.close()}
});

test('standalone reviewers use shared stream, retain details across refresh and clear on identity switch',async()=>{
  const page=await fixture();try {
    const reviewer={id:'review/standalone',name:'Standalone critic',execution:'running',live:true,model:'review-model',summary:'Initial review'};
    await page.evaluate(value=>window.fixture.publish({reviewers:[value]}),reviewer);await openDrawer(page);
    await page.locator('.work-reviewer > summary').click();
    assert.match(await page.locator('.now-agents').textContent(),/Standalone critic/);
    await page.evaluate(()=>window.fixture.show('now'));assert.equal(await page.locator('.work-reviewer').evaluate(e=>e.open),true);
    await page.evaluate(value=>window.fixture.publish({reviewers:[value]}),{...reviewer,summary:'Updated review'});
    assert.match(await page.locator('.work-reviewer').textContent(),/Updated review/);
    assert.equal(await page.locator('.work-reviewer').evaluate(e=>e.open),true);
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),1);
    await page.evaluate(()=>window.fixture.show('learn'));assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),0);
    await page.evaluate(()=>{window.fixture.cfg.agent='two'});await page.evaluate(()=>window.fixture.show('now'));
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),false,'new identity starts private drawer closed');
    await openDrawer(page);assert.equal(await page.locator('.work-reviewer').count(),0,'old scoped replay cannot leak reviewers');
    assert.equal(await page.evaluate(()=>window.fixture.calls.some(c=>c.path.includes('/activity'))),false,'Now adds no parallel reviewer poll');
  } finally {await page.close()}
});

test('late Now render and live detail cannot mutate a returned page or revive a timer',async()=>{
  const page=await fixture();try {
    await page.evaluate(()=>{window.fixture.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];window.fixture.S.agentState.runner={alive:true,busy:true,run:'run'};window.fixture.state.detailDelay=true;window.fixture.oldRender=window.fixture.show('now')});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));await page.evaluate(()=>window.fixture.show('learn'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=false});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>window.fixture.release('detail',{text:'STALE_DETAIL'}));await page.evaluate(()=>window.fixture.oldRender);
    assert.equal(await page.locator('.now-agents').count(),1);
    assert.equal(await page.evaluate(()=>window.fixture.timers.size),1);
    // Start actual live refresh through its timer, leave while detail is pending.
    await page.evaluate(()=>{window.fixture.state.detailDelay=true;const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));await page.evaluate(()=>window.fixture.show('learn'));
    await page.evaluate(()=>window.fixture.release('detail',{text:'STALE_LIVE'}));await settle(page);
    assert.equal(await page.evaluate(()=>window.fixture.timers.size),0,'old finally must not reschedule after disposal');
    assert.equal(await page.locator('[data-page=learn]').textContent(),'Learn fixture');
  } finally {await page.close()}
});

test('same-mount rerender retires an old live read without wedging or releasing a newer flight',async()=>{
  const page=await fixture();try {
    await page.evaluate(()=>{window.fixture.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];window.fixture.S.agentState.runner={alive:true,busy:true,run:'run'}});
    await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=true;const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=false});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>window.fixture.release('detail',{text:'STALE_BEFORE_RERENDER'}));await settle(page);
    assert.equal(await page.locator('#turn-live').getAttribute('data-detail'),null);
    const before=await page.evaluate(()=>window.fixture.state.detailCalls);
    await page.evaluate(()=>{window.fixture.state.detailResult={text:'FRESH_AFTER_RERENDER'};const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});await settle(page);
    assert.equal(await page.evaluate(()=>window.fixture.state.detailCalls),before+1,'old flight must release its busy state even after render gen changes');
    assert.equal(await page.locator('#turn-live').getAttribute('data-detail'),'FRESH_AFTER_RERENDER');
  } finally {await page.close()}
});

test('a disposed flight cannot release a newer mounted flight when its late finally runs',async()=>{
  const page=await fixture();try {
    await page.evaluate(()=>{window.fixture.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];window.fixture.S.agentState.runner={alive:true,busy:true,run:'run'}});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=true;const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>{window.fixture.oldDetailRelease=window.fixture.pending.get('detail');window.fixture.pending.delete('detail')});
    await page.evaluate(()=>window.fixture.show('learn'));await page.evaluate(()=>{window.fixture.state.detailDelay=false});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=true;const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>window.fixture.oldDetailRelease({text:'OLD_DISPOSED_FLIGHT'}));await settle(page);
    const before=await page.evaluate(()=>window.fixture.state.detailCalls);
    await page.evaluate(()=>{window.fixture.impl.onEvent('llm');const [id,fn]=[...window.fixture.timers][0];window.fixture.timers.delete(id);fn()});await settle(page);
    assert.equal(await page.evaluate(()=>window.fixture.state.detailCalls),before,'newer flight still owns busy state');
    await page.evaluate(()=>window.fixture.release('detail',{text:'NEW_MOUNT_FLIGHT'}));await settle(page);
    assert.equal(await page.locator('#turn-live').getAttribute('data-detail'),'NEW_MOUNT_FLIGHT');
  } finally {await page.close()}
});

// Execute the production frame-go listener as well as show/Now: awaiting show
// alone does not mean its render won, and a later Now mount may look identical.
test('Overview pending detail cannot open a new Now mount after leaving and returning',async()=>{
  const page=await fixture(false);try{
    await page.evaluate(()=>{const f=window.fixture;f.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];f.S.agentState.runner={alive:true,busy:true,run:'run'};f.state.detailDelay=true;f.oldOverview=f.go({view:'now',openAgents:true});});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>window.fixture.show('learn'));
    await page.evaluate(()=>{window.fixture.state.detailDelay=false});await page.evaluate(()=>window.fixture.show('now'));
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),false);
    await page.evaluate(()=>window.fixture.release('detail',{text:'OLD_OVERVIEW'}));await page.evaluate(()=>window.fixture.oldOverview);await settle(page);
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),false,'retired Overview must not open the returned mount');
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),0,'retired Overview must not restart observation');
    assert.equal(await page.evaluate(()=>window.fixture.calls.length),0);
  }finally{await page.close()}
});

test('Overview intent survives first-mount run refresh until the winning render mounts the drawer',async()=>{
  const page=await fixture(false);try{
    await page.evaluate(()=>{const f=window.fixture;f.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];f.S.agentState.runner={alive:true,busy:true,run:'run'};f.state.detailDelay=true;f.oldOverview=f.go({view:'now',openAgents:true});});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    assert.equal(await page.locator('.now-agents').count(),0,'first render has not mounted the drawer');
    await page.evaluate(()=>{const f=window.fixture;f.oldDetailRelease=f.pending.get('detail');f.pending.delete('detail');f.impl.onEvent('run');});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>window.fixture.oldDetailRelease({text:'SUPERSEDED_OVERVIEW'}));await page.evaluate(()=>window.fixture.oldOverview);
    await page.evaluate(()=>window.fixture.release('detail',{text:'WINNING_REFRESH'}));await settle(page);
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),true,'the intent belongs to the mount, not its first render generation');
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),1);
    assert.equal(await page.evaluate(()=>window.fixture.calls.filter(c=>c.path.startsWith('/api/forge/tasks')).length),1);
  }finally{await page.close()}
});

test('Overview intent is cleared by an identity switch before its first mount completes',async()=>{
  const page=await fixture(false);try{
    await page.evaluate(()=>{const f=window.fixture;f.S.runs=[{id:'run',kind:'chat_turn',status:'running'}];f.S.agentState.runner={alive:true,busy:true,run:'run'};f.state.detailDelay=true;f.oldOverview=f.go({view:'now',openAgents:true});});
    await page.waitForFunction(()=>window.fixture.pending.has('detail'));
    await page.evaluate(()=>{const f=window.fixture;f.cfg.agent='other';f.state.detailDelay=false;});await page.evaluate(()=>window.fixture.show('now'));
    await page.evaluate(()=>window.fixture.release('detail',{text:'OLD_SCOPE'}));await page.evaluate(()=>window.fixture.oldOverview);await settle(page);
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),false);
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),0);
  }finally{await page.close()}
});

test('fresh Overview frame-go opens the mounted drawer once without command transport',async()=>{
  const page=await fixture(false);try{
    await page.evaluate(()=>window.fixture.go({view:'now',openAgents:true}));await settle(page);
    assert.equal(await page.locator('.now-agents').evaluate(e=>e.open),true);
    assert.equal(await page.evaluate(()=>window.fixture.impl.subscriptionCount()),1);
    assert.equal(await page.evaluate(()=>window.fixture.calls.filter(c=>c.path.startsWith('/api/forge/tasks')).length),1);
    assert.equal(await page.evaluate(()=>window.fixture.calls.filter(c=>c.path.startsWith('/api/forge/control')).length),0);
  }finally{await page.close()}
});
