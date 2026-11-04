// Two final-review regressions through the built production entry and actual UI
// controls. Reuse the disposable transport fixture; never read an installed profile.
// Run after parent-owned `npm run build` in desk/app.
import assert from 'node:assert/strict';
import test from 'node:test';
import {pathToFileURL} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {ready,close} from '../test/fixtures/desktop-coverage-data.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const url=await ready;
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {})});
const out=process.env.FINAL_REGRESSION_OUT;
if(out)await mkdir(out,{recursive:true});
test.after(async()=>{await browser.close();await close()});
async function fixture(view){
 const context=await browser.newContext({viewport:{width:1707,height:882},reducedMotion:'reduce'});
 await context.route('**/*',route=>new URL(route.request().url()).origin===url ? route.continue() : route.abort());
 await context.addInitScript(()=>{window.WebSocket=class{constructor(){throw Error('Fixture selects HTTP')}};localStorage.setItem('frame.panel','1');localStorage.setItem('frame.rail','0');});
 const page=await context.newPage();await page.goto(url+'/?page='+view+'&scene=populated');
 await page.locator(`[data-page=${view}]`).waitFor({state:'visible'});
 return {page,context};
}
test('production onboarding category hides every nonmatching card/control, All restores twelve',async()=>{
 const {page,context}=await fixture('learn');try{
  await page.locator('.learn .task').first().waitFor();
  assert.equal(await page.locator('.learn .task:visible').count(),12);
  const categories=await page.locator('.lrn-chip:not([data-cat=all])').evaluateAll(nodes=>nodes.map(n=>n.dataset.cat));
  assert.ok(categories.length>=4);
  for(const cat of categories){
   await page.locator(`.lrn-chip[data-cat="${cat}"]`).click();
   const rows=await page.locator('.learn .task').evaluateAll(nodes=>nodes.map(n=>({cat:n.dataset.cat,hidden:n.hidden,display:getComputedStyle(n).display,visible:!!n.getClientRects().length})));
   assert.ok(rows.some(r=>r.cat===cat));
   for(const row of rows){assert.equal(row.hidden,row.cat!==cat);assert.equal(row.visible,row.cat===cat);if(row.hidden)assert.equal(row.display,'none');}
   assert.equal(await page.locator('.learn .task[hidden] :is(input,textarea,button):visible').count(),0);
  }
  const cat=categories[0];await page.locator(`.lrn-chip[data-cat="${cat}"]`).click();
  // Counterfactual restores precisely the reviewed CSS fault on the real DOM.
  const oldRule=await page.addStyleTag({content:'.learn .task { display:grid; }'});
  assert.ok(await page.locator('.learn .task[hidden]:visible').count()>0,'old rule reproduces the failure');
  await oldRule.evaluate(node=>node.remove());
  assert.equal(await page.locator('.learn .task[hidden]:visible').count(),0);
  await page.locator(`.lrn-chip[data-cat="${cat}"]`).focus();
  const reached=[];
  for(let i=0;i<110;i++){
   await page.keyboard.press('Tab');
   const card=await page.evaluate(()=>{const n=document.activeElement?.closest('.task');return n ? {cat:n.dataset.cat,hidden:n.hidden} : null});
   if(card){assert.equal(card.hidden,false,'nonmatching controls excluded from keyboard traversal');assert.equal(card.cat,cat);reached.push(card.cat);}
  }
  assert.ok(reached.length>0,'matching template controls remain keyboard reachable');
  if(out){await page.locator('#lrn-tasks').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(out,'learn-category.png')});}
  await page.locator('.lrn-chip[data-cat=all]').click();
  assert.equal(await page.locator('.learn .task:visible').count(),12);
  assert.equal(await page.locator('.learn .task[hidden]').count(),0);
 }finally{await context.close()}
});
test('actual chat roster overview opens Now disclosure, not Plans, with explicit controls',async()=>{
 const {page,context}=await fixture('talk');try{
  await page.locator('#toggle-panel').click();
  await page.locator('#panel .forge-task').first().waitFor({state:'attached'});
  assert.equal(await page.locator('#panel .forge-actions button').filter({hasText:/Остановить|Приостановить|Возобновить/}).count(),0,'chat roster remains read-only');
  const commands=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/forge/control')commands.push(r.url())});
  await page.locator('#panel').getByRole('button',{name:'Открыть обзор →',exact:true}).click();
  await page.locator('[data-page=now] .now-agents[open]').waitFor({state:'visible'});
  await page.locator('[data-page=now] .now-agents .forge-task').first().waitFor({state:'attached'});
  assert.equal(await page.locator('[data-page=plans]:visible').count(),0);
  const task=page.locator('.now-agents .forge-task').first();await task.locator(':scope > summary').click();
  assert.ok(await task.getByRole('button',{name:'Остановить группу',exact:true}).isVisible(),'explicit owner controls moved to Now');
  assert.equal(commands.length,0,'opening overview is observation, not a command');
  if(out){await page.locator('.now-agents').scrollIntoViewIfNeeded();await page.screenshot({path:resolve(out,'now-overview.png')});}
 }finally{await context.close()}
});
