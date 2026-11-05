// Production bundle, native browser radios, synthetic bridge only. Build app first.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {ready,close} from '../test/fixtures/desktop-coverage-data.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
const url=await ready;
test.after(async()=>{await browser.close();await close();});
const out=process.env.SOURCE_MODE_SCREENSHOTS;
if(out)await mkdir(out,{recursive:true});
async function fixture(row={source_modes:['preserve','clean'],default_source_mode:'preserve'},width=1280,theme='light'){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),calls=[];
 await page.route('**/__fixture/native',async route=>{
  const body=route.request().postDataJSON();calls.push(body);
  const value=body.cmd==='update_agent_request'?row:body.cmd==='update_check'?{newer:true,current:'2.3.0',latest:'2.3.1',url:'https://fixture.invalid/release.zip',sha256:'a'.repeat(64)}:
   body.cmd==='update_start'?{state:'admitted',observer_may_close:true,job:{id:'synthetic-update'}}:
   body.cmd==='update_agent_confirm'?{state:'confirmed'}:undefined;
  if(value===undefined)return route.continue();
  await route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
 });
 await page.goto(url+'/?page=settings&previewTheme='+theme);
 await page.locator('.settings-tab[data-group=app]').click();
 const cards=page.locator('.source-mode-cards');
 await cards.waitFor({state:'visible'});
 // The about section lives in an independently scrolling Settings document.
 await cards.scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>!document.querySelector('.source-mode-cards input[value=clean]').disabled);
 return {page,cards,calls,close:()=>context.close()};
}

test('both full-card radios stay visible; clean requires a deliberate choice even when default/only mode',async()=>{
 const f=await fixture({source_modes:['clean'],default_source_mode:'clean'});
 try{
  assert.equal(await f.cards.locator('label').count(),2);
  assert.equal(await f.cards.locator(':checked').count(),0);
  assert.equal(await f.cards.locator('input[value=preserve]').isDisabled(),true);
  await f.page.getByRole('button',{name:'Проверить обновления',exact:true}).click();
  await f.page.getByRole('button',{name:'Обновить Hélène',exact:true}).click();
  assert.equal(f.calls.filter(c=>c.cmd==='update_start').length,0);
  await f.cards.locator('[data-source-mode=clean] .choice-text').click();
  await f.page.getByRole('button',{name:'Обновить Hélène',exact:true}).click();
  await f.page.waitForFunction(()=>document.querySelector('.source-mode-cards').disabled);
  assert.deepEqual(f.calls.filter(c=>c.cmd==='update_start').map(c=>c.args.sourceMode),['clean']);
 }finally{await f.close();}
});

test('keyboard chooses the same policy and preserves conflict/review disclosure',async()=>{
 const f=await fixture();
 try{
  const clean=f.cards.locator('input[value=clean]'),preserve=f.cards.locator('input[value=preserve]');
  await clean.focus();await f.page.keyboard.press('Space');
  assert.equal(await clean.isChecked(),true);
  await f.page.keyboard.press('ArrowRight');
  assert.equal(await preserve.isChecked(),true);
  assert.equal(await f.cards.locator('[data-selected=true]').getAttribute('data-source-mode'),'preserve');
  assert.match(await f.page.locator('#update-source-mode-hint').innerText(),/Конфликты.*решения/);
  await f.page.getByRole('button',{name:'Проверить обновления',exact:true}).click();
  await f.page.getByRole('button',{name:'Обновить Hélène',exact:true}).click();
  await f.page.waitForFunction(()=>document.querySelector('.source-mode-cards').disabled);
  assert.deepEqual(f.calls.filter(c=>c.cmd==='update_start').map(c=>c.args.sourceMode),['preserve']);
 }finally{await f.close();}
});

test('a prepared proposal locks its selected mode; confirmation never silently rewrites it',async()=>{
 const f=await fixture({source_modes:['clean','preserve'],default_source_mode:'preserve',source_mode:'clean',id:'bound-proposal',state:'awaiting',proposal_current:true});
 try{
  assert.equal(await f.cards.locator('input[value=clean]').isChecked(),true);
  assert.equal(await f.cards.locator('input[value=preserve]').isDisabled(),true);
  await f.page.getByRole('button',{name:'Подтвердить предложение агента',exact:true}).click();
  assert.deepEqual(f.calls.filter(c=>c.cmd==='update_agent_confirm').map(c=>c.args),[{id:'bound-proposal'}]);
 }finally{await f.close();}
});

test('wide and phone layouts keep symmetric cards, actions and readable selected states',async()=>{
 for(const width of [1280,390])for(const theme of ['light','dark']){
  const f=await fixture(undefined,width,theme);
  try{
   await f.cards.locator('input[value=preserve]').check();
   const boxes=await f.cards.locator('label').evaluateAll(rows=>rows.map(row=>{const b=row.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,right:b.right};}));
   assert.ok(Math.abs(boxes[0].width-boxes[1].width)<1);
   if(width>560){assert.equal(boxes[0].y,boxes[1].y);assert.equal(boxes[0].height,boxes[1].height);}
   else assert.ok(boxes[1].y>=boxes[0].y+boxes[0].height);
   assert.ok(boxes.every(b=>b.x>=0&&b.right<=width));
   const overflow=await f.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
   assert.equal(overflow,false);
   await f.page.getByRole('button',{name:'Проверить обновления',exact:true}).click();
   await f.cards.scrollIntoViewIfNeeded();
   if(out)await f.page.screenshot({path:resolve(out,`source-cards-${width}-${theme}.png`)});
  }finally{await f.close();}
 }
});
