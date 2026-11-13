import {createRequire} from 'node:module';import {createServer} from 'node:http';import fs from 'node:fs/promises';import assert from 'node:assert/strict';
const {chromium}=createRequire('/work/browser/package.json')('playwright');
const server=createServer(async(req,res)=>{try{const name=req.url==='/test.js'?'test.js':req.url==='/test.css'?'test.css':null;
  res.setHeader('Content-Type',(name?.endsWith('.js')?'text/javascript':name?'text/css':'text/html')+'; charset=utf-8');res.end(name?await fs.readFile('/work/revision4/test-dist/'+name):'<html><head><meta charset="utf-8"><link rel="stylesheet" href="/test.css"></head><body><main id="root"></main><script type="module" src="/test.js"></script></body></html>');}catch{res.writeHead(404);res.end()}});
await new Promise(r=>server.listen(38945,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});const errors=[],checks=[];let page;
try {
 page=await browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce'});page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:38945');await page.waitForFunction(()=>!!window.stepsTest);
 const summaries=await page.evaluate(()=>{
   const t=window.stepsTest,base={tool:'edit',args:{path:'test.py'},at:'2026-11-10T00:00:00Z',finished_at:'2026-11-10T00:00:02Z',status:'received',result:{head:'{}'}};
   return ['ok','confirmed','refused','failed','partial','in_doubt','unknown','surprise'].map(outcome=>t.toolSummary({...base,outcome}));
 });assert.deepEqual(summaries.map(s=>s.icon),['✓','✓','⊘','✗','◐','?','·','·']);assert.equal(summaries[2].state,'отказ');assert.equal(summaries[0].duration,'2.0 с');checks.push('honest_outcomes');
 assert.equal(await page.evaluate(()=>window.stepsTest.toolSummary({status:'received',outcome:'failed',operations:[{exit_code:0},{exit_code:3}]}).state),'exit 3');
 const live=await page.evaluate(()=>{
   const t=window.stepsTest,now=Date.now(),run=(id,age)=>({id,status:'running',updated_at:new Date(now-age).toISOString()});
   const runs=[run('old',800000),run('recent',10000),run('new',1000),run('future',100000*-1)];
   return [t.currentRunId(runs,{runner:{alive:false}},now),t.currentRunId(runs,{runner:{alive:true,busy:false}},now),t.currentRunId(runs,{runner:{alive:true,busy:true,run:'owned'}},now),t.currentRunId([runs[0]],{},now)];
 });assert.deepEqual(live,['new','','owned','']);checks.push('current_run_fallback');
 await page.evaluate(()=>{
   const t=window.stepsTest;window.paint=t.mountActionSteps(document.querySelector('#root'));
   window.fixture={id:'fixture',manifest:{status:'running'},iterations:[{call_id:'model',status:'completed',ms:1000,tools:[{call_id:'call',tool:'edit',args:{path:'a.py',text:'<script>not markup</script>'},outcome:'refused',target:'a.py',status:'received',at:'2026-11-10T00:00:00Z',finished_at:'2026-11-10T00:00:02Z',definition:{description:'Edit',input_schema:{type:'object'}},model_request:{tool:'edit',args:{path:'a.py'}},execution:{handler:'hands.edit',status:'entered'},result:{head:'{"status":"refused"}',truncated:true,result_id:'result'},model_observation:'Refused. Re-read the file.'}]}]};
   t.setResultFetcher(async()=>({text:'{"raw":"FULL-JSON"}',model_text:'MODEL-TEXT'}));window.paint(window.fixture);
 });
 assert.equal(await page.locator('.st-think').count(),0);await page.locator('.st-toggle').click();
 assert.equal(await page.getByRole('tab').count(),5);assert.equal(await page.getByRole('tabpanel').count(),1);
 await page.getByRole('tab',{name:'Исполнение',exact:true}).click();assert.match(await page.getByRole('tabpanel').innerText(),/hands.edit/);
 await page.evaluate(()=>{window.fixture.iterations[0].tools[0].operations=[{kind:'process',argv:['python','x.py'],exit_code:1}];window.paint(window.fixture)});
 assert.equal(await page.getByRole('tab',{name:'Исполнение',exact:true}).getAttribute('aria-selected'),'true');assert.match(await page.getByRole('tabpanel').innerText(),/python/);
 await page.getByRole('tab',{name:'JSON-ответ',exact:true}).click();await page.getByRole('tabpanel').getByRole('button',{name:/Показать целиком/}).click();assert.match(await page.getByRole('tabpanel').innerText(),/FULL-JSON/);assert.doesNotMatch(await page.getByRole('tabpanel').innerText(),/MODEL-TEXT/);
 await page.getByRole('tab',{name:'JSON-ответ',exact:true}).focus();await page.keyboard.press('Home');assert.equal(await page.getByRole('tab',{name:'Аргументы',exact:true}).getAttribute('aria-selected'),'true');assert.equal(await page.locator('#root script').count(),0);checks.push('trace_tabs_refresh_keyboard_raw_payload_xss');
 await page.screenshot({path:'/work/revision4/fixture-steps-390.png'});
 await page.evaluate(()=>{
   document.querySelector('#root').replaceChildren();window.scope='one';window.runState={runner:{alive:false}};
   window.runRows=[{id:'live',status:'running',updated_at:new Date().toISOString(),created_at:new Date().toISOString(),goal_head:'Текущая задача'},{id:'done',status:'done',goal_head:'Предыдущая задача'}];
   window.request=async path=>path.startsWith('/api/runs')?window.runRows:path==='/api/state'?window.runState:path==='/api/phone/activity'?{}:path.includes('/forge/tasks')?{active:[],recent:[],next_cursor:null}:{...window.fixture,id:path.split('/').at(-1)};
   window.phone=window.stepsTest.phoneActions(document.querySelector('#root'),window.request,()=>window.scope);window.phone.start();
 });
 await page.locator('.phone-current .turn[data-status="live"]').waitFor();assert.equal(await page.locator('.phone-history .turn').count(),1);
 await page.locator('.phone-current .turn-head').click();await page.waitForTimeout(2800);assert.equal(await page.locator('.phone-current .turn-head').getAttribute('aria-expanded'),'false');
 await page.evaluate(()=>{window.runRows=window.runRows.map(r=>({...r,status:'done'}))});await page.waitForFunction(()=>document.querySelector('.phone-current').textContent.includes('Сейчас нет текущего хода'));assert.equal(await page.locator('.phone-current .turn').count(),0);assert.equal(await page.locator('.phone-history .turn').count(),2);
 await page.evaluate(()=>window.phone.stop());checks.push('current_turn_completion_moves_to_history');
 await page.evaluate(()=>{
   document.querySelector('#root').replaceChildren();window.rosterReads=0;window.rosterPending=[];window.rosterDelayed=true;
   window.rosterPage={active:[{id:'delayed',status:'active',goal:'Team',agents:[{id:'one',status:'running',subtask:'First worker'},{id:'two',status:'done',subtask:'Second worker'}]}],recent:[],next_cursor:null};
   window.request=async path=>{
     if(path.includes('/forge/tasks')){window.rosterReads++;return window.rosterDelayed?await new Promise(resolve=>window.rosterPending.push(()=>resolve(window.rosterPage))):window.rosterPage}
     if(path.includes('/forge/events'))return {events:[],next_offset:0,next_after_seq:0,has_more:false,generation:''};
     if(path.startsWith('/api/runs'))return [{id:'history',status:'done',goal_head:'Previous turn'}];
     if(path==='/api/state')return {runner:{alive:false}};
     if(path==='/api/phone/activity')return {};
     return {...window.fixture,id:'history'};
   };
   window.phone=window.stepsTest.phoneActions(document.querySelector('#root'),window.request,()=>window.scope);window.phone.start();
 });
 const agents=page.locator('.phone-agents'),history=page.locator('.phone-history');
 await page.locator('.phone-history .turn').waitFor({state:'attached'});
 assert.equal(await agents.evaluate(e=>e.open),false);assert.equal(await page.evaluate(()=>window.rosterReads),0);
 assert.equal(await page.evaluate(()=>!!(document.querySelector('.phone-history').compareDocumentPosition(document.querySelector('.phone-agents'))&Node.DOCUMENT_POSITION_FOLLOWING)),true);
 await history.locator(':scope > summary').click();await history.locator('.turn-head').click();
 await page.waitForTimeout(2700);assert.equal(await agents.evaluate(e=>e.open),false);assert.equal(await page.evaluate(()=>window.rosterReads),0);
 await agents.locator(':scope > summary').focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>window.rosterReads===1);
 await agents.locator(':scope > summary').click();await page.waitForFunction(()=>!document.querySelector('.phone-agents').open);
 await page.evaluate(()=>window.rosterPending.shift()());await page.waitForTimeout(100);
 assert.equal(await page.locator('.forge-unit').count(),0,'Late response must not populate a closed disclosure');
 await history.locator(':scope > summary').click();assert.equal(await agents.evaluate(e=>e.open),false);
 await agents.locator(':scope > summary').click();await page.waitForFunction(()=>window.rosterReads===2);
 await page.evaluate(()=>{window.rosterDelayed=false;window.rosterPending.shift()()});await page.locator('.forge-unit').first().waitFor();
 assert.equal(await page.locator('.forge-unit[open]').count(),0);
 await page.locator('.forge-unit > summary').first().click();assert.equal(await page.locator('.forge-unit[open]').count(),1);
 await history.locator(':scope > summary').click();assert.equal(await page.locator('.forge-unit[open]').count(),1);
 await page.locator('.forge-unit > summary').first().click();await page.waitForFunction(()=>window.rosterReads>=3);assert.equal(await page.locator('.forge-unit[open]').count(),0);
 await agents.locator(':scope > summary').click();await page.waitForTimeout(100);const reads=await page.evaluate(()=>window.rosterReads);
 await page.evaluate(()=>{window.phone.stop();window.phone.start()});await page.waitForTimeout(2700);
 assert.equal(await agents.evaluate(e=>e.open),false);assert.equal(await page.evaluate(()=>window.rosterReads),reads);
 await page.screenshot({path:'/work/revision4/fixture-disclosures-390.png'});await page.evaluate(()=>window.phone.stop());
 checks.push('turns_before_lazy_independent_agents_late_response_polling_keyboard_resume');
 await page.evaluate(()=>{
   document.querySelector('#root').replaceChildren();window.scope='pages';window.pageFail=true;window.pageDelay=false;window.pageRequests=[];
   window.pageRows=Array.from({length:85},(_,i)=>({id:'run-'+String(85-i).padStart(4,'0'),status:'done',updated_at:'fixed',goal_head:'row-'+String(85-i).padStart(4,'0')}));
   window.request=async path=>{
     if(path.startsWith('/api/runs')){
       const q=new URL(path,'http://local').searchParams,before=q.get('before'),limit=+q.get('limit');window.pageRequests.push(path);
       const rows=window.pageRows.filter(r=>!before||r.id<before).slice(0,limit);
       if(before&&window.pageFail){window.pageFail=false;throw Error('transient fixture')}
       if(before&&window.pageDelay)return await new Promise(resolve=>window.pageResolve=()=>resolve(rows));
       return rows;
     }
     if(path==='/api/state')return {runner:{alive:false}};
     if(path==='/api/phone/activity')return {};
     if(path.includes('/forge/tasks'))return {active:[],recent:[],next_cursor:null};
     return {...window.fixture,id:path.split('/').at(-1)};
   };
   window.phone=window.stepsTest.phoneActions(document.querySelector('#root'),window.request,()=>window.scope);window.phone.start();
 });
 const pageHistory=page.locator('.phone-history'),more=page.locator('.phone-history-more');
 await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===40);
 assert.equal(await page.locator('.turns-n').innerText(),'40+');await pageHistory.locator(':scope > summary').click();
 await more.click();await page.waitForFunction(()=>document.querySelector('.phone-history').textContent.includes('Не удалось прочитать продолжение'));
 assert.equal(await page.locator('.phone-history .turn').count(),40);assert.equal(await more.isEnabled(),true);
 await more.click();await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===80);assert.equal(await page.locator('.turns-n').innerText(),'80+');
 const opened=page.locator('.phone-history .turn').filter({has:page.locator('.turn-title',{hasText:'row-0060'})});await opened.locator('.turn-head').click();
 await page.evaluate(()=>window.pageRows.unshift({id:'run-0086',status:'done',updated_at:'new',goal_head:'row-0086'}));
 await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===81);assert.equal(await opened.locator('.turn-head').getAttribute('aria-expanded'),'true');
 await more.click();await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===86);assert.equal(await more.isVisible(),false);assert.equal(await page.locator('.turns-n').innerText(),'86');
 assert.equal(await page.evaluate(()=>new Set([...document.querySelectorAll('.phone-history .turn-title')].map(e=>e.textContent)).size),86);
 assert.match((await page.evaluate(()=>window.pageRequests.filter(p=>p.includes('before=')).at(-1))),/before=run-0006/);
 await page.waitForTimeout(2700);assert.equal(await page.locator('.phone-history .turn').count(),86);assert.equal(await more.isVisible(),false);assert.equal(await page.locator('.phone-agents').evaluate(e=>e.open),false);
 await page.evaluate(()=>{window.phone.stop();window.scope='delay';window.pageDelay=true;window.phone.start()});await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===40);await more.click();await page.waitForFunction(()=>!!window.pageResolve);
 await page.evaluate(()=>{window.phone.stop();window.scope='other';window.pageRows=[{id:'other',status:'done',goal_head:'Other scope'}];window.phone.start()});await page.waitForFunction(()=>document.querySelectorAll('.phone-history .turn').length===1);await page.evaluate(()=>window.pageResolve());await page.waitForTimeout(100);
 assert.equal(await page.locator('.phone-history .turn').count(),1);assert.match(await pageHistory.innerText(),/Other scope/);await page.evaluate(()=>window.phone.stop());
 checks.push('full_history_keyset_retry_refresh_dedup_end_and_scope');


 await page.evaluate(()=>{
   document.querySelector('#root').replaceChildren();const ref={kind:'payload',sha256:'a'.repeat(64)},out={kind:'payload',sha256:'b'.repeat(64)};
   const task={id:'t',status:'active',goal:'Общая цель',agents:[{id:'u',role:'worker',status:'done',subtask:'Подробная задача участника занимает всю ширину, а не узкую колонку',observation:{generation:'g'}}]};
   const events=[{seq:1,kind:'tool_start',input_ref:ref},{seq:2,kind:'tool_dispatch',receipt:{handler:'worker.run',status:'entered'}},{seq:3,kind:'tool_execution',receipt:{kind:'process',argv:['python','-c','print(1)'],exit_code:1}},{seq:4,kind:'tool_model_response'},{seq:5,kind:'tool_result',output_ref:out,outcome:'failed'}].map((e,i)=>({...e,tool:'run',call_id:'call',generation:'g',iteration:1,at:`2026-11-10T00:00:0${i}Z`}));
   window.forge=window.stepsTest.forgePanel(document.querySelector('#root'),async path=>{
     if(path.includes('/tasks'))return await new Promise(resolve=>{window.resolveRoster=()=>resolve({active:[task],recent:[],next_cursor:null})});
     if(path.includes('/events'))return {events,next_offset:5,next_after_seq:5,has_more:false,generation:'g'};
     const q=new URL(path,'http://local').searchParams,sha=q.get('sha256'),text=sha===ref.sha256?'{"command":"print(1)"}':'{"error":"fixture failure"}';
     return {sha256:sha,offset:0,text,total_chars:Array.from(text).length,next_offset:null};
   },60000,{compact:true,readOnly:true});window.forge.start();window.forge.setReviewers({});
 });
 assert.equal(await page.locator('.forge-recent').isVisible(),false);assert.match(await page.locator('#root > [role=status]').innerText(),/Читаю записи/);assert.equal(await page.locator('.forge-unit').count(),0);await page.evaluate(()=>window.resolveRoster());checks.push('forge_loading_waits_for_roster');
 assert.equal(await page.locator('.forge-recent').isVisible(),false);
 await page.locator('.forge-unit > summary').click();await page.locator('.forge-action').waitFor();assert.equal(await page.locator('.forge-action').count(),1);
 await page.locator('.forge-action > summary').click();await page.waitForFunction(()=>document.querySelector('.forge-action-payload')?.textContent.includes('print(1)'));
 await page.getByRole('tab',{name:'Исполнение',exact:true}).click();assert.match(await page.getByRole('tabpanel').innerText(),/python/);
 await page.getByRole('tab',{name:'JSON-ответ',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-trace-panel="response"]')?.textContent.includes('fixture failure'));
 assert.equal(await page.getByRole('tab',{name:'JSON-ответ',exact:true}).getAttribute('aria-selected'),'true');assert.match(await page.getByRole('tabpanel').innerText(),/fixture failure/);
 assert.match(await page.locator('.forge-action > summary').innerText(),/exit 1/);assert.equal(await page.locator('[data-command]').count(),0);checks.push('forge_grouping_commands_per_action_payloads');
 await page.screenshot({path:'/work/revision4/fixture-forge-390.png'});await page.evaluate(()=>window.forge.stop());

 await page.evaluate(()=>{
   document.querySelector('#root').replaceChildren();window.finishedRows=[];
   window.forge=window.stepsTest.forgePanel(document.querySelector('#root'),async()=>({active:[],recent:window.finishedRows,next_cursor:null}),60000,{compact:true,readOnly:true});window.forge.start();
 });
 await page.waitForFunction(()=>document.querySelector('#root > [role=status]').textContent==='Субагентов пока нет.');assert.equal(await page.locator('.forge-recent').isVisible(),false);
 await page.evaluate(async()=>{window.finishedRows=[{id:'past',status:'done',agents:[{id:'done',status:'done',subtask:'Completed work'}]}];await window.forge.refresh()});
 assert.equal(await page.locator('.forge-recent').isVisible(),true);assert.equal(await page.locator('.forge-recent > summary').innerText(),'Завершённые субагенты · 1');assert.equal(await page.locator('#root > [role=status]').isVisible(),false);
 await page.locator('.forge-recent > summary').click();assert.equal(await page.locator('.forge-unit').isVisible(),true);await page.evaluate(()=>window.forge.stop());checks.push('forge_archive_hidden_until_real_records_and_accurate_label');
 const shell=await page.evaluate(()=>{
   const events={};let requests=0;const tg={initData:'fixture',colorScheme:'light',viewportHeight:700,isFullscreen:true,safeAreaInset:{top:24},contentSafeAreaInset:{top:32},ready(){},expand(){},requestFullscreen(){requests++},isVersionAtLeast(){return true},onEvent(n,f){events[n]=f},BackButton:{show(){},hide(){},onClick(){}}};
   window.stepsTest.mountTelegramShell(tg);return {requests,fullscreen:document.documentElement.dataset.fullscreen,top:document.documentElement.style.getPropertyValue('--app-safe-top')};
 });assert.equal(shell.requests,1);assert.equal(shell.fullscreen,'active');assert.match(shell.top,/24px/);assert.match(shell.top,/32px/);checks.push('telegram_fullscreen_safe_area');
 assert.deepEqual(errors,[]);await fs.writeFile('/work/revision4/BROWSER-REGRESSIONS.json',JSON.stringify({ok:true,checks,errors},null,2));console.log(JSON.stringify({ok:true,checks}));
} catch(e){if(page)await page.screenshot({path:'/work/revision4/regression-failure.png'}).catch(()=>{});await fs.writeFile('/work/revision4/BROWSER-REGRESSIONS.json',JSON.stringify({ok:false,checks,error:String(e),errors},null,2));throw e}
finally{await browser.close();server.close()}
