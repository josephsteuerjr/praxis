// Real browser DOM and production controller. Synthetic channel; no paid TTS.
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createServer} from 'node:http';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const raw=readFileSync(new URL('../../ui-kit/window/server-speech.ts',import.meta.url),'utf8');
const code=stripTypeScriptTypes(raw.replace(/^import .*;\r?\n/gm,'')).replace(/^export /gm,'');
const digestCode=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/sha256.ts',import.meta.url),'utf8')).replace(/^export /gm,'');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
test.after(()=>browser.close());
async function fixture(mode='ready'){
 const context=await browser.newContext();
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<body></body>'}));
 const page=await context.newPage();await page.goto('http://127.0.0.1:15551/');
 const install=async mode=>{await page.evaluate(async({code,digestCode,mode})=>{
  const cfg={base:'https://channel-one.test',key:'offline-channel-key',agent:'main'},S={view:'settings'};
  const state={mode,calls:[],jobs:{},revision:0,selection:{provider:'fish',enabled:true,revision:'initial',reference_id:'voice-one',model:'drama-3'},pending:[],urls:[],revoked:[]};
  const voices=[{id:'voice-one',name:'<img src=x onerror="window.pwned=1">'},{id:'voice-two',name:'Второй голос'}];
  const bytes=new TextEncoder().encode('OggS synthetic integrity fixture');
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  const audio={ok:true,mime:'audio/ogg',data:btoa(String.fromCharCode(...bytes)),bytes:bytes.length,sha256:sha};
  HTMLMediaElement.prototype.play=()=>Promise.resolve();HTMLMediaElement.prototype.pause=()=>{};
  URL.createObjectURL=()=>{const url='blob:fixture-'+state.urls.length;state.urls.push(url);return url;};
  URL.revokeObjectURL=url=>state.revoked.push(url);
  const response=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
  window.fetch=async(url,options={})=>{
   const path=new URL(url).pathname,body=options.body?JSON.parse(options.body):undefined;
   state.calls.push({url,path,method:options.method||'GET',headers:options.headers,body});
   if(state.mode==='delayed-status'&&path==='/api/speech/status')await new Promise(resolve=>state.pending.push(resolve));
   if(path==='/api/speech/status'){
    if(state.mode==='absent')return response({error:'not_found'},404);
    return response({ok:true,selection:state.selection,fish:{configured:true,authenticated:state.mode!=='unconfirmed',api_credit_raw:'15'}});
   }
   if(path.startsWith('/api/speech/fish/catalog/')){
    const page=Number(path.split('/').at(-1));return response({ok:true,voices:page===1?voices:[{id:'voice-three',name:'Третий голос'}],page,has_more:page===1});
   }
   if(path==='/api/speech/selection'){
    if(body.expected_revision!==state.selection.revision)return response({ok:false,error:'config_changed'},409);
    state.selection={...state.selection,provider:body.provider,enabled:body.enabled,revision:'revision-'+(++state.revision),
      ...(body.voice_id?{reference_id:body.voice_id,model:body.model}:{})};return response({ok:true,selection:state.selection});
   }
   if(path==='/api/speech/fish/jobs'){
    const value={ok:true,id:body.request_id,state:state.mode==='pending'?'running':'ready'};state.jobs[value.id]=value;
    if(state.mode==='lost-post')throw Error('Response lost after admission');
    return response(value);
   }
   const match=path.match(/^\/api\/speech\/fish\/jobs\/([a-f0-9]{32})(\/audio|\/cancel)?$/);
   if(match){
    if(match[2]==='/audio')return response({...audio,...(state.mode==='bad-audio'?{sha256:'0'.repeat(64)}:{})});
    if(match[2]==='/cancel'){if(state.mode==='delayed-cancel')await new Promise(resolve=>state.pending.push(resolve));return response({ok:true,remote_cancel_confirmed:false});}
    if(state.mode==='delayed-job')await new Promise(resolve=>state.pending.push(resolve));
    if(state.mode==='missing-job')return response({ok:false,error:'job_not_found'},404);
    return response(state.jobs[match[1]]||{ok:true,id:match[1],state:'ready'});
   }
   throw Error('Unexpected channel request '+path);
  };
  const card=(title,body)=>{const root=document.createElement('section');root.className='card';root.append(body);return root;};
  const sha256=new Function(digestCode+'\nreturn sha256;')();
  const module=new Function('cfg','S','card','sha256',code+'\nreturn {serverSpeechCard};')(cfg,S,card,sha256);
  const root=module.serverSpeechCard();document.body.append(root);
  window.fixture={cfg,S,state,root,voices,module};
 },{code,digestCode,mode});};
 await install(mode);
 if(mode==='delayed-status')await page.waitForFunction(()=>window.fixture.state.pending.length>0);
 else if(mode==='absent')await page.waitForFunction(()=>window.fixture.root.hidden);
 else await page.locator('[data-speech-preview]').first().waitFor();
 return {page,close:()=>context.close(),reload:async mode=>{await page.reload();await install(mode);}};
}
const calls=page=>page.evaluate(()=>window.fixture.state.calls);
const posts=rows=>rows.filter(r=>r.method==='POST');

test('cross-origin channel uses Bearer auth without requiring credentialed CORS or URL keys',async()=>{
 const calls=[];
 const origin=createServer((_request,response)=>{response.setHeader('Content-Type','text/html');response.end('<body></body>');});
 await new Promise(resolve=>origin.listen(0,'127.0.0.1',resolve));
 const server=createServer((request,response)=>{
  calls.push({url:request.url,method:request.method,auth:request.headers.authorization,cookie:request.headers.cookie});
  response.setHeader('Access-Control-Allow-Origin',request.headers.origin||'http://127.0.0.1:15551');
  response.setHeader('Access-Control-Allow-Headers','Authorization, Content-Type');
  response.setHeader('Access-Control-Allow-Methods','GET, POST, OPTIONS');
  if(request.method==='OPTIONS'){response.writeHead(204);response.end();return;}
  response.setHeader('Content-Type','application/json');
  const value=request.url==='/api/speech/status'?{ok:true,selection:{provider:'fish',enabled:true,revision:'one'},fish:{configured:true}}:
   {ok:true,page:1,has_more:false,voices:[{id:'cross-origin-voice',name:'Transport fixture'}]};
  response.end(JSON.stringify(value));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port,context=await browser.newContext();
 try{
  const page=await context.newPage();await page.goto('http://127.0.0.1:'+origin.address().port+'/');
  await page.evaluate(({base,code,digestCode})=>{
   const cfg={base,key:'offline-channel-key',agent:'one'},S={view:'settings'};
   const card=(_title,body)=>{const root=document.createElement('section');root.append(body);return root;};
   const sha256=new Function(digestCode+'\nreturn sha256;')();
   const module=new Function('cfg','S','card','sha256',code+'\nreturn {serverSpeechCard};')(cfg,S,card,sha256);
   document.body.append(module.serverSpeechCard());
  },{base,code,digestCode});
  await page.locator('[data-speech-preview="cross-origin-voice"]').waitFor({timeout:5000});
  const reads=calls.filter(row=>row.method==='GET');assert.ok(reads.some(row=>row.url==='/api/speech/fish/catalog/1'));
  for(const row of reads){assert.equal(row.auth,'Bearer offline-channel-key');assert.equal(row.cookie,undefined);assert.ok(!row.url.includes('key='));}
  assert.equal(calls.filter(row=>row.method==='POST').length,0);
 }finally{await context.close();server.closeAllConnections();origin.closeAllConnections();await Promise.all([new Promise(resolve=>server.close(resolve)),new Promise(resolve=>origin.close(resolve))]);}
});

test('channel credentials stay with captured channel; unconfirmed auth is not success; names are inert',async()=>{
 const f=await fixture('unconfirmed');try{
  const rows=await calls(f.page);assert.ok(rows.length);
  for(const row of rows){assert.equal(new URL(row.url).origin,'https://channel-one.test');assert.equal(row.headers.Authorization,'Bearer offline-channel-key');assert.ok(!row.url.includes('key='));}
  assert.equal(posts(rows).length,0);assert.equal(await f.page.locator('img').count(),0);
  assert.match(await f.page.locator('.field-hint').first().textContent(),/не подтверждён/);
  assert.equal(await f.page.locator('[data-speech-preview]').count(),await f.page.evaluate(()=>window.fixture.voices.length));
 }finally{await f.close();}
});
test('an old server without capability hides only this card and performs no writes',async()=>{
 const f=await fixture('absent');try{assert.equal(await f.page.locator('[data-server-speech-card]').isVisible(),false);assert.equal(posts(await calls(f.page)).length,0);}finally{await f.close();}
});
test('selection and pause are revision-bound and never generate a preview',async()=>{
 const f=await fixture();try{
  await f.page.locator('[data-speech-select="voice-two"]').click();await f.page.waitForFunction(()=>window.fixture.state.selection.reference_id==='voice-two');
  await f.page.getByRole('button',{name:'Приостановить озвучку',exact:true}).click();await f.page.waitForFunction(()=>!window.fixture.state.selection.enabled);
  const rows=posts(await calls(f.page));assert.equal(rows.length,2);
  assert.equal(rows[0].path,'/api/speech/selection');assert.equal(rows[0].body.voice_id,'voice-two');assert.match(rows[0].body.request_id,/^[a-f0-9]{32}$/);
  assert.notEqual(rows[1].body.expected_revision,rows[0].body.expected_revision);assert.ok(!('voice_id'in rows[1].body));assert.ok(!('model'in rows[1].body));
 }finally{await f.close();}
});
test('free-form direction and exact words are distinct; checked audio alone receives a Blob URL',async()=>{
 const f=await fixture();try{
  const words='[тихо] Именно эти <whisper>слова</whisper>.',delivery='Тепло, с улыбкой; затем пауза и шёпот.';
  await f.page.locator('[data-speech-text]').fill(words);await f.page.locator('[data-speech-delivery]').fill(delivery);
  await f.page.locator('[data-speech-model]').selectOption('s2.1-pro');await f.page.locator('[data-speech-preview="voice-two"]').click();
  await f.page.waitForFunction(()=>window.fixture.state.urls.length===1);
  const rows=posts(await calls(f.page));assert.equal(rows.length,1);assert.equal(rows[0].body.text,words);assert.equal(rows[0].body.delivery,delivery);assert.equal(rows[0].body.model,'s2.1-pro');
  assert.equal(await f.page.locator('audio').isVisible(),true);assert.equal(await f.page.evaluate(()=>window.fixture.state.selection.reference_id),'voice-one');
 }finally{await f.close();}
});
test('lost paid POST retains one identity; check resumes GET without another paid request',async()=>{
 const f=await fixture('lost-post');try{
  await f.page.locator('[data-speech-preview]').first().click();await f.page.waitForFunction(()=>window.fixture.root.textContent.includes('не подтверждено'));
  let rows=posts(await calls(f.page));assert.equal(rows.length,1);const id=rows[0].body.request_id;
  assert.ok(await f.page.locator('[data-speech-preview]').first().isDisabled());
  await f.page.evaluate(()=>window.fixture.state.mode='ready');await f.page.locator('[data-speech-check]').click();
  await f.page.waitForFunction(()=>window.fixture.state.urls.length===1);rows=posts(await calls(f.page));assert.equal(rows.length,1);
  assert.ok((await calls(f.page)).some(r=>r.path==='/api/speech/fish/jobs/'+id));
 }finally{await f.close();}
});

test('fresh page explicitly replays the identical saved paid intent despite edited controls',async()=>{
 const f=await fixture('lost-post');try{
  await f.page.locator('[data-speech-text]').fill('Прежние слова, ровно так.');
  await f.page.locator('[data-speech-delivery]').fill('С улыбкой; затем тихий шёпот.');
  await f.page.locator('[data-speech-model]').selectOption('s2.1-pro');
  await f.page.locator('[data-speech-preview="voice-two"]').click();
  await f.page.locator('[data-speech-replay]').waitFor();
  const original=posts(await calls(f.page))[0].body;
  await f.reload('missing-job');await f.page.locator('[data-speech-replay]').waitFor();
  assert.equal(posts(await calls(f.page)).length,0,'Page restoration performs reads only');
  await f.page.locator('[data-speech-text]').fill('Другой черновик');
  await f.page.locator('[data-speech-delivery]').fill('Другая подача');
  await f.page.locator('[data-speech-model]').selectOption('drama-3');
  await f.page.evaluate(()=>window.fixture.state.mode='ready');
  await f.page.locator('[data-speech-replay]').click();await f.page.waitForFunction(()=>window.fixture.state.urls.length===1);
  const rows=posts(await calls(f.page));assert.equal(rows.length,1);assert.deepEqual(rows[0].body,original);
  assert.equal(rows[0].path,'/api/speech/fish/jobs');assert.equal(new Set([original.request_id,rows[0].body.request_id]).size,1);
  assert.ok(!(await calls(f.page)).some(row=>row.path.endsWith('/cancel')));
 }finally{await f.close();}
});

test('a different channel credential cannot restore the previous paid intent',async()=>{
 const f=await fixture('lost-post');try{
  await f.page.locator('[data-speech-preview]').first().click();await f.page.locator('[data-speech-replay]').waitFor();
  await f.page.evaluate(()=>{
   const f=window.fixture;f.root.remove();f.cfg.key='another-offline-channel-key';f.state.mode='ready';f.state.calls=[];
   f.root=f.module.serverSpeechCard();document.body.append(f.root);
  });
  await f.page.locator('[data-speech-preview]').first().waitFor();
  assert.equal(await f.page.locator('[data-speech-replay]').isVisible(),false);
  assert.equal(await f.page.locator('[data-speech-check]').isVisible(),false);
  assert.equal(posts(await calls(f.page)).length,0);
 }finally{await f.close();}
});
test('audio digest mismatch never reaches playback',async()=>{
 const f=await fixture('bad-audio');try{
  await f.page.locator('[data-speech-preview]').first().click();await f.page.waitForFunction(()=>window.fixture.root.textContent.includes('непроверенное аудио'));
  assert.equal(await f.page.evaluate(()=>window.fixture.state.urls.length),0);assert.equal(await f.page.locator('audio').isVisible(),false);
 }finally{await f.close();}
});
test('switching channel while a status read is pending cannot authorize an old button',async()=>{
 const f=await fixture('delayed-status');try{
  await f.page.evaluate(()=>{const f=window.fixture;f.cfg.base='https://channel-two.test';f.cfg.key='other-channel-key';f.state.mode='ready';f.state.pending.splice(0).forEach(resolve=>resolve());});
  await f.page.waitForTimeout(100);await f.page.evaluate(()=>window.fixture.root.querySelector('button').click());
  assert.equal(posts(await calls(f.page)).length,0);assert.ok((await calls(f.page)).every(row=>row.headers.Authorization==='Bearer offline-channel-key'));
 }finally{await f.close();}
});
test('leaving and returning retires the old read; late completion cannot release a new job flight',async()=>{
 const f=await fixture('pending');try{
  await f.page.locator('[data-speech-preview]').first().click();await f.page.waitForFunction(()=>Object.keys(window.fixture.state.jobs).length===1);
  await f.page.evaluate(()=>{const f=window.fixture;f.state.mode='delayed-job';});
  await f.page.waitForFunction(()=>window.fixture.state.pending.length>0);
  await f.page.evaluate(()=>{const f=window.fixture;f.S.view='now';dispatchEvent(new CustomEvent('frame-section',{detail:'now'}));f.state.mode='ready';for(const v of Object.values(f.state.jobs))v.state='ready';f.S.view='settings';dispatchEvent(new CustomEvent('frame-section',{detail:'settings'}));});
  await f.page.waitForFunction(()=>window.fixture.state.urls.length===1);
  await f.page.evaluate(()=>window.fixture.state.pending.splice(0).forEach(resolve=>resolve()));await f.page.waitForTimeout(100);
  assert.equal(await f.page.evaluate(()=>window.fixture.state.urls.length),1);assert.equal(posts(await calls(f.page)).length,1);
 }finally{await f.close();}
});
test('unmount revokes audio and creates no cancellation or synthesis side effect',async()=>{
 const f=await fixture();try{
  await f.page.locator('[data-speech-preview]').first().click();await f.page.waitForFunction(()=>window.fixture.state.urls.length===1);
  await f.page.evaluate(()=>window.fixture.root.remove());await f.page.waitForFunction(()=>window.fixture.state.revoked.length===1);
  const rows=posts(await calls(f.page));assert.equal(rows.length,1);assert.equal(rows[0].path,'/api/speech/fish/jobs');
 }finally{await f.close();}
});
