// Production-entry desktop audit; no installed profile, OS commands or model calls.
// npm build is deliberately parent-owned. See desktop-coverage.md for the isolated runtime.
import {pathToFileURL} from 'node:url';
import {mkdir,writeFile,readFile,readdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {createHash} from 'node:crypto';
import {ready,close,fixtureStatus,setCoverageState} from '../test/fixtures/desktop-coverage-data.mjs';
import {measureDesktop} from './desktop-coverage-measure.mjs';
const args=Object.fromEntries(process.argv.slice(2).map(arg=>{const [k,...v]=arg.replace(/^--/,'').split('=');return [k,v.join('=')||true]}));
const {chromium}=await import(args.playwright?pathToFileURL(resolve(String(args.playwright))).href:process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const out=resolve(String(args.out||'desk/app/test-results/desktop-coverage'));await mkdir(out,{recursive:true});
const url=await ready, browser=await chromium.launch({headless:true});
const routes=['now','talk','plans','wakes','frame','files','journal','anatomy','learn','settings'];
const groups=['agent','brain','rights','app','look'];
const learnSections=['start','connect','map','tasks','flow','how','rhythm','contracts','economy','limits'];
const matrices=args.matrix==='all'?[
 {theme:'light',scale:.9,width:1707,height:882,font:'hand'}, {theme:'dark',scale:.9,width:1707,height:882,font:'hand'},
 {theme:'light',scale:1,width:1100,height:760,font:'sans'}, {theme:'dark',scale:1,width:1100,height:760,font:'serif'},
 {theme:'light',scale:.9,width:1707,height:882,font:'mono',text:'xl',density:'compact',shape:'square'},
 {theme:'light',scale:1,width:900,height:600,font:'hand',shape:'round'}, {theme:'dark',scale:1,width:900,height:600,font:'sans',density:'compact',shape:'square'}
]:[{theme:String(args.theme||'light'),scale:Number(args.scale||.9),width:Number(args.width||1707),height:Number(args.height||882),font:String(args.font||'hand'),shape:String(args.shape||'default'),text:String(args.text||'m'),density:String(args.density||'comfortable')}];
const ledger=[],expected=[],omissions=[
 {family:'native-auxiliary',status:'separate_author',reason:'First-start/migration/update HTML, WinForms tray and Qt wizard are not app CSS; delegated separately.'},
 {family:'physical-device-platform',status:'not_executed',reason:'Chromium synthetic Windows desktop is not installed WebView/macOS/Linux or real audio/OS permission execution.'},
 {family:'unbounded-dynamic-content',status:'bounded_fixture',reason:'Long synthetic content is exercised; not every possible runtime string or user palette combination.'},
 {family:'contrast',status:'not_measured',reason:'Geometry/color inventory does not prove pixel contrast over textured/translucent surfaces.'},
 {family:'overlay-boundaries',status:'explicit',reason:'HTML media viewer/text preview/native save chooser/overwrite/saved receipt/device save/keyboard text menu/upstream restore confirmation are exercised by named overlay scenes. Physical right-click menu timing and OS-owned native attachment picker remain unqualified.'}
];
const sources=[];
for(const name of await readdir(new URL('../dist/assets/',import.meta.url))){if(!name.endsWith('.js')&&!name.endsWith('.css'))continue;const bytes=await readFile(new URL('../dist/assets/'+name,import.meta.url));sources.push({name,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});}
const fixtureSources=await Promise.all(['desktop-coverage.mjs','desktop-coverage-measure.mjs','../test/fixtures/desktop-coverage-data.mjs','../test/fixtures/ui-session-server.mjs'].map(async name=>{const bytes=await readFile(new URL(name,import.meta.url));return {name,sha256:createHash('sha256').update(bytes).digest('hex')};}));
let page,context,current,sceneName,consoleErrors=[],failedRequests=[],responses=[],blockedRequests=[];
async function painted(){await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))});await page.waitForTimeout(120);}
async function requireNode(selector,{visible=true,count}={}){const node=page.locator(selector);await node.first().waitFor({state:visible?'visible':'attached',timeout:10000});if(count!==undefined&&await node.count()!==count)throw Error(`Required ${selector}: expected ${count}, observed ${await node.count()}`);return node;}
async function click(selector){const node=await requireNode(selector);await node.first().click({timeout:10000});await painted();}
async function openDetails(selector){const nodes=await requireNode(selector,{visible:false});await nodes.evaluateAll(nodes=>nodes.forEach(e=>{if(!e.open){e.open=true;e.dispatchEvent(new Event('toggle'));}}));await painted();}
async function nav(id){await click(`.rail-item[data-view="${id}"]`);await requireNode(`[data-page="${id}"]`);await page.waitForTimeout(200);}
async function fresh(id,state={scene:'populated'}){setCoverageState(state);await page.goto(url+'/?previewTheme='+current.theme);await requireNode('.rail-item');await nav(id);}
async function capture(id,{slices=true,anchor}={}){
 await painted();if(anchor){await requireNode(anchor);await page.locator(anchor).first().scrollIntoViewIfNeeded();await painted();}
 const prefix=`${current.theme}-${current.scale}-${current.width}x${current.height}-${current.font}-${id}`;
 const dimensions=await page.evaluate(()=>{const s=document.querySelector('#view');return {height:s.clientHeight,total:s.scrollHeight}});
 const max=slices?Math.max(1,Math.ceil(dimensions.total/Math.max(1,dimensions.height*.82))):1;
 const entry={id,matrix:{...current},state:fixtureStatus().scene,expectedFaults:{paths:fixtureStatus().errorPaths,nativeRead:fixtureStatus().scene==='settings-error',conflict:fixtureStatus().conflict},entry:page.url(),productionBundle:true,rendered:true,measured:true,visuallyInspected:false,screenshots:[],measurements:[],consoleErrors:[...consoleErrors],failedRequests:[...failedRequests],httpFailures:[...responses],blockedExternal:[...blockedRequests],assertions:[],status:'observed'};
 for(let i=0;i<max;i++){
  if(slices)await page.evaluate(({i,step})=>{document.querySelector('#view').scrollTop=i*step},{i,step:dimensions.height*.82});
  await painted();const path=resolve(out,`${prefix}-${i}.png`);await page.screenshot({path});entry.screenshots.push(path);entry.measurements.push(await page.evaluate(measureDesktop));
 }
 ledger.push(entry);await persist();return entry;
}
async function persist(){const report={schema:2,generatedAt:new Date().toISOString(),basis:'Actual built app/index entry, isolated same-origin API/native bridge, Chromium; screenshots are viewport-sized, not montages.',censusSource:'d972aa29 plus current source: Forge is under Now drawer, not Plans',browser:browser.version(),filters:{only:args.only||null,scenes:args.scenes||null,baseline:!!args.baseline,quick:!!args.quick},sources,fixtureSources,matrices,expected,omissions,summary:{scenes:ledger.length,rendered:ledger.filter(r=>r.rendered).length,measured:ledger.filter(r=>r.measured).length,visuallyInspected:ledger.filter(r=>r.visuallyInspected).length,failed:expected.filter(r=>r.status==='failed').length,screenshots:ledger.reduce((n,r)=>n+r.screenshots.length,0)},ledger,fixture:fixtureStatus()};await writeFile(resolve(out,'ledger.json'),JSON.stringify(report,null,2));}
async function scene(id,run){if(args.scenes&&!String(args.scenes).split(',').includes(id))return;const requirement={id,matrix:{...current},status:'running'};expected.push(requirement);sceneName=id;consoleErrors=[];failedRequests=[];responses=[];blockedRequests=[];try{await run();requirement.status='exercised';}catch(error){requirement.status='failed';requirement.error=String(error.stack||error);try{const entry=await capture(id+'-FAIL',{slices:false});entry.status='failed';entry.rendered=false;entry.error=requirement.error;}catch(e){requirement.captureError=String(e);}console.error('SCENE_FAILED '+id+': '+error.message);}await persist();}
async function closeTopDialog(){const close=page.locator('dialog[open] .paper-close').last();await close.waitFor();await close.click();await painted();}
async function finalTextAndRestore(){
 await fresh('settings');await click('.settings-tab[data-group=app]');await page.getByRole('button',{name:'Вернуть код к upstream',exact:true}).click();await page.getByRole('dialog',{name:'Восстановить upstream-код',exact:true}).waitFor();await capture('overlay-upstream-restore-confirmation',{slices:false});await closeTopDialog();
 await fresh('talk',{scene:'media'});const paragraph=page.locator('.msg-body > p').first();await paragraph.scrollIntoViewIfNeeded();await page.waitForTimeout(500);await paragraph.evaluate(node=>{const range=document.createRange();range.selectNodeContents(node);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)});await page.keyboard.press('Shift+F10');await requireNode('.text-menu:not([hidden])');await capture('overlay-text-context-keyboard',{slices:false});await page.keyboard.press('Escape');
}
async function overlayCallbacks(){
 await fresh('talk',{scene:'media'});await requireNode('[data-paper-path="synthetic/document.txt"]');
 await capture('media-message-types',{slices:true});
 await click('[data-paper-path="synthetic/image.svg"] [data-paper-open]');await requireNode('dialog[open] .paper-viewer img');await capture('overlay-image-viewer',{slices:false});
 await page.locator('dialog[open]').getByRole('button',{name:'100%',exact:true}).click();await capture('overlay-image-native-size',{slices:false});await closeTopDialog();
 await click('[data-paper-path="synthetic/document.txt"] [data-paper-open]');await requireNode('dialog[open] .paper-text-preview');await capture('overlay-text-preview',{slices:false});
 const nativeSave=await page.evaluate(()=>{delete window.showSaveFilePicker;return !('showSaveFilePicker' in window)});if(!nativeSave)throw Error('Cannot select native HTML save branch: showSaveFilePicker not configurable');
 await page.locator('dialog[open] footer').getByRole('button',{name:'Сохранить…',exact:true}).click();await page.getByRole('dialog',{name:'Сохранить файл',exact:true}).waitFor();await requireNode('dialog[open] .paper-file-row');await capture('overlay-native-file-save',{slices:false});
 await page.getByRole('dialog',{name:'Сохранить файл',exact:true}).locator('footer').getByRole('button',{name:'Сохранить',exact:true}).click();await requireNode('.paper-replace:not([hidden])');await capture('overlay-file-overwrite',{slices:false});
 await page.getByRole('button',{name:'Заменить файл',exact:true}).click();await page.getByRole('dialog',{name:'Файл сохранён',exact:true}).waitFor();await capture('overlay-saved-file-receipt',{slices:false});await closeTopDialog();
 // Saving from a preview leaves the preview open behind the local save dialog.
 if(await page.locator('dialog[open]').count())await closeTopDialog();
 await page.evaluate(()=>{window.__coverageSavedBytes=0;window.showSaveFilePicker=async()=>({createWritable:async()=>({write:async blob=>{window.__coverageSavedBytes=blob.size},close:async()=>{},abort:async()=>{}})});});
 await click('[data-paper-path="synthetic/document.txt"] [data-paper-save]');await page.getByRole('dialog',{name:'Сохранить на устройство',exact:true}).waitFor();await capture('overlay-save-to-device',{slices:false});
 await page.getByRole('dialog',{name:'Сохранить на устройство',exact:true}).getByRole('button',{name:'Сохранить',exact:true}).click();await page.getByRole('button',{name:'Готово',exact:true}).waitFor();if(!await page.evaluate(()=>window.__coverageSavedBytes>0))throw Error('Inert device save callback did not receive bytes');await capture('overlay-save-to-device-receipt',{slices:false});await closeTopDialog();
 // Text context and restore run separately so one overlay cannot hide later evidence.
}
async function variants(id){
 if(id==='now'){
  await click('[data-now-history]');await requireNode('.now-history .ev-head');await click('.now-history .ev-head');await openDetails('.now-context');await capture('now-history-context-expanded');
  await openDetails('.now-agents');await requireNode('.now-agents .forge-teams');await capture('now-subagents');
  await openDetails('.now-agents .forge-task');await openDetails('.now-agents .forge-unit');await requireNode('.now-agents .forge-action');await openDetails('.now-agents .forge-action');await capture('now-subagents-expanded',{slices:false,anchor:'.now-agents'});
  const result=page.locator('.now-agents .forge-unit .forge-actions button').filter({hasText:'Итог участника'});await result.first().click();await requireNode('.now-agents .forge-unit > .fold-body > .forge-payload');await capture('now-subagent-result',{slices:false,anchor:'.now-agents .forge-unit > .fold-body > .forge-payload'});
  await openDetails('.now-agents .forge-recent');await capture('now-recent-reviewers');
 }
 if(id==='talk'){
  await click('#toggle-panel');await requireNode('#panel');await capture('talk-panel-open',{slices:false});
  await click('.room[data-key="window-long"] .room-more');await requireNode('#menu:not([hidden])');await capture('talk-room-menu',{slices:false});await page.keyboard.press('Escape');await page.locator('#say').click();
  await page.locator('#say').fill('Проверь исходники и подготовь результат.\n'.repeat(5));await capture('talk-long-draft',{slices:false});await page.locator('#say').fill('');
  await page.locator('#attach-input').setInputFiles({name:'Синтетический длинный документ для проверки вложений.txt',mimeType:'text/plain',buffer:Buffer.from('Only synthetic test bytes.')});await requireNode('#composer-files:not([hidden])');await capture('talk-attachment-chip',{slices:false});
 }
 if(id==='wakes'){await click('[data-page=wakes] .ev-head');await requireNode('[data-page=wakes] .ev.open');await capture('wakes-expanded');}
 if(id==='frame'){
  await click('#do-diff');await requireNode('#frame-out table');await openDetails('#frame-out details');await capture('frame-diff-expanded');
  await click('#do-metrics');await requireNode('#frame-out table');await capture('frame-ten-column-metrics');
  await click('#frame-main .paper-select-trigger');await requireNode('.paper-select-menu');await capture('frame-select-popup',{slices:false});await page.keyboard.press('Escape');
 }
 if(id==='files'){
  for(const [name,path] of [['document','soul/SOUL.md'],['readonly','soul/readonly.md'],['tail','soul/tail.md'],['error','soul/error.md']]){
   await click(`.item[data-path="${path}"]`);await requireNode(name==='error'?'#md-main .fail':'#md-view');await capture('files-'+name);
   if(name==='document'){
    await click('#md-diff');await requireNode('#md-diff-out .diff');await capture('files-previous-diff');await click('#md-edit');await requireNode('#md-editor:not([hidden])');await capture('files-editor');
    setCoverageState({scene:'file-conflict',conflict:true});
    // HTTP preserves 409 conflict code. The legacy generic WS fixture cannot represent code.
    // Runner intentionally uses HTTP for all data requests, including this 409.
    await click('#md-save');await requireNode('#md-conflict:not([hidden])');await capture('files-save-conflict');await click('#md-cancel');setCoverageState({scene:'populated'});
   }
  }
 }
 if(id==='journal'){await openDetails('[data-page=journal] details');await capture('journal-full-record');for(const kind of ['model','perception']){await click(`[data-journal-kind=${kind}]`);await capture('journal-'+kind);}}
 if(id==='anatomy'){await openDetails('[data-page=anatomy] details');await openDetails('#tool-list details');await capture('anatomy-all-disclosures');await page.locator('#tool-filter').fill('no-such-tool');await requireNode('#tool-list .empty');await capture('anatomy-filter-empty',{anchor:'#tool-filter',slices:false});}
 if(id==='settings'){
  await click('.settings-tab[data-group=brain]');await click('[data-page=settings] .paper-select-trigger');await requireNode('.paper-select-menu');await capture('settings-select-popover',{slices:false});await page.keyboard.press('Escape');
  await click('.settings-tab[data-group=look]');await openDetails('.look-details');await capture('settings-look-details');
  await click('.settings-tab[data-group=agent]');
  for(const state of ['populated','settings-conflict','settings-save-error']){
   setCoverageState({scene:state});await click('.save-bar .btn-primary');await requireNode('.save-bar .receipt');await capture('settings-save-'+state,{anchor:'.save-bar',slices:false});
  }
 }
 if(id==='learn'){
  for(const section of learnSections){await requireNode('#lrn-'+section);await capture('learn-section-'+section,{anchor:'#lrn-'+section,slices:false});}
  const categories=await page.locator('.lrn-chip').evaluateAll(nodes=>nodes.map(n=>n.dataset.cat));if(categories.length<5)throw Error('Expected at least five task categories');
  for(const category of categories){await click(`.lrn-chip[data-cat="${category}"]`);await capture('learn-category-'+category,{anchor:'#lrn-tasks',slices:false});}
  await click('.lrn-chip[data-cat="all"]');await click('.flow-play');await capture('learn-flow-active',{anchor:'#lrn-flow',slices:false});
  await click('[data-voice-help]');await requireNode('dialog.voice-help[open]');await capture('learn-voice-help',{slices:false});await click('dialog .paper-close');
  for(const goal of ['telegram','server']){await click(`[data-goal="${goal}"]`);await requireNode('#connection-guide .guide-content');await capture('learn-connection-'+goal,{anchor:'#lrn-connect',slices:false});}
 }
}
try{
 for(current of matrices){
  context=await browser.newContext({viewport:{width:current.width,height:current.height},deviceScaleFactor:1,colorScheme:current.theme,reducedMotion:'reduce'});page=await context.newPage();
  await context.route('**/*',route=>{const request=route.request(),target=new URL(request.url());if(['data:','blob:'].includes(target.protocol)||target.origin===url)return route.continue();blockedRequests.push(request.url());return route.abort('blockedbyclient');});
  page.on('pageerror',e=>consoleErrors.push(String(e)));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});page.on('requestfailed',r=>failedRequests.push({url:r.url(),error:r.failure()?.errorText}));page.on('response',r=>{if(r.status()>=400)responses.push({url:r.url(),status:r.status()})});
  await page.addInitScript(({scale,theme,font,text,density,shape})=>{window.WebSocket=class {constructor(){throw Error('Synthetic audit selects HTTP transport');}};localStorage.setItem('helene.look',JSON.stringify({uiScale:scale,font,text,density,shape}));localStorage.setItem('helene.theme',theme);localStorage.setItem('frame.panel','1');localStorage.setItem('frame.rail','0');localStorage.setItem('frame.view','now');},current);
  if(!args.baseline&&(!args.only||String(args.scenes||'').split(',').includes('overlay-callbacks')))await scene('overlay-callbacks',overlayCallbacks);
  if(!args.baseline&&(!args.only||String(args.scenes||'').split(',').includes('overlay-text-and-restore')))await scene('overlay-text-and-restore',finalTextAndRestore);
  for(const id of routes){if(args.only&&!String(args.only).split(',').includes(id))continue;
   await scene(id+'-populated',async()=>{await fresh(id);
    if(id==='settings'){
     await requireNode('.settings-tab',{count:5});
     for(const group of groups){await click(`.settings-tab[data-group="${group}"]`);const e=await capture('settings-'+group);e.assertions.push('All five native settings tabs mounted, selected '+group);}
    }else{const mount={now:'.now',talk:'.talk',plans:'.forge-plans',wakes:'.section-title',frame:'#frame-main',files:'#md-main',journal:'.journal',anatomy:'.system-overview',learn:'.learn'}[id];await requireNode(`[data-page=${id}] ${mount}`);await capture(id+'-populated',{slices:id!=='talk'});}
   });
   if(args.baseline)continue;
   if(id!=='plans')await scene(id+'-expanded',async()=>{await fresh(id);await variants(id);});
   if(!args.quick&&['talk','plans','wakes','frame','files','journal','now'].includes(id))await scene(id+'-empty',async()=>{await fresh(id,{scene:'empty'});await capture(id+'-empty',{slices:id!=='talk'});});
  }
  if(!args.baseline&&!args.quick){
   if(!args.only||String(args.only).includes('settings')){
    for(const provider of ['anthropic','chatgpt','local'])await scene('settings-provider-'+provider,async()=>{await fresh('settings',{provider});await click('.settings-tab[data-group=brain]');await capture('settings-provider-'+provider);});
    await scene('settings-remote-edition',async()=>{await fresh('settings',{scene:'remote'});await requireNode('[data-page=settings] .settings');await page.locator('[data-page=settings]').getByRole('heading',{name:'Агент живёт на сервере',exact:true}).waitFor();await capture('settings-remote-edition');});
    await scene('settings-read-error',async()=>{await fresh('settings',{scene:'settings-error'});await requireNode('[data-page=settings] .fail');await capture('settings-read-error');});
   }
   const errors={plans:['/api/agenda','/api/board'],wakes:['/api/runs'],frame:['/api/shadow'],files:['/api/md-tree'],journal:['/api/errors'],anatomy:['/api/anatomy'],talk:['/api/chat/window']};
   for(const [id,errorPaths] of Object.entries(errors)){if(args.only&&!String(args.only).split(',').includes(id))continue;await scene(id+'-read-error',async()=>{await fresh(id,{scene:'error',errorPaths});await capture(id+'-read-error',{slices:id!=='talk'});});}
   if(!args.only||String(args.only).includes('frame'))await scene('frame-loading',async()=>{await fresh('frame');setCoverageState({delayedPaths:['/api/shadow/chat-window/capture'],delayMs:2000});await click('#do-view');await capture('frame-loading',{slices:false});await page.waitForTimeout(2200);await capture('frame-loading-recovered',{slices:false});});
  }
  await context.close();context=null;
 }
}finally{await persist();await context?.close();await browser.close();await close();}
const failed=expected.filter(e=>e.status==='failed');console.log(JSON.stringify({ok:failed.length===0,scenes:ledger.length,requirements:expected.length,failed:failed.map(e=>({id:e.id,error:e.error})),ledger:resolve(out,'ledger.json')},null,2));if(failed.length)process.exitCode=1;
