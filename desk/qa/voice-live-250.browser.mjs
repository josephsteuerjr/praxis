// Real DOM + exact production TypeScript modules, controlled media/native edges.
// No owner data, microphone permission, network/model API or installed app is used.
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';

const kit=fileURLToPath(new URL('../ui-kit/',import.meta.url));
const out=path.resolve(process.env.HELENE_VOICE_QA_OUT||'voice-live-250-proof');
const driver=process.env.HELENE_PLAYWRIGHT_MODULE||'playwright';
const {chromium}=await import(path.isAbsolute(driver)?pathToFileURL(driver).href:driver);
const source=async name=>readFile(path.join(kit,name),'utf8');
const modules={
 '/paper-audio.js':'paper-audio.ts','/voice-session.js':'window/voice-session.ts',
 '/voice-composer.js':'window/voice-composer.ts','/voice-preferences.js':'window/voice-preferences.ts',
 '/text.js':'text.ts',
};
const fakeMedia=`
window.sent=[];window.trace=[];window.errors=[];window.nativeActive=false;
class TestAudio extends EventTarget {
 constructor(src=''){super();this.src=src;this.paused=true;this.currentTime=0;this.duration=2;this.volume=1;this.playbackRate=1;window.trace.push({event:'audio',audio:this});}
 play(){this.paused=false;window.trace.push({event:'play',src:this.src,audio:this});this.dispatchEvent(new Event('play'));return Promise.resolve();}
 pause(){if(!this.paused){this.paused=true;this.dispatchEvent(new Event('pause'));}}
 removeAttribute(name){if(name==='src')this.src='';} load(){}
}
window.Audio=TestAudio;
window.AudioContext=class {createAnalyser(){return {fftSize:256,frequencyBinCount:128,getByteTimeDomainData:v=>v.fill(128)}}createMediaStreamSource(){return {connect(){}}}close(){return Promise.resolve()}};
const stream={getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{enabled:true}]};
Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>stream},configurable:true});
window.MediaRecorder=class {static isTypeSupported(){return true}state='inactive';mimeType='audio/webm';start(){this.state='recording'}pause(){this.state='paused'}resume(){this.state='recording'}stop(){this.state='inactive'}requestData(){queueMicrotask(()=>this.ondataavailable?.({data:new Blob(['x'.repeat(2048)],{type:'audio/webm'})}))}};
`;
const server=http.createServer(async(req,res)=>{
 try {
  const name=new URL(req.url,'http://fixture').pathname;
  let body,type='text/javascript; charset=utf-8';
  if(name in modules){
   body=stripTypeScriptTypes(await source(modules[name]),{mode:'transform'});
   for(const [from,to] of Object.entries({'./text':'/text.js','./voice-session':'/voice-session.js','./voice-preferences':'/voice-preferences.js','../paper-audio':'/paper-audio.js','./api':'/stub-api.js','../paper-dialog':'/stub-dialog.js','./lib':'/stub-lib.js'}))body=body.replaceAll("from '"+from+"'","from '"+to+"'");
   body=body.replace(/import '\.\/styles\/voice\.css';/,'');
  } else if(name==='/stub-api.js')body="export const electron={shell:'python'};export const shell=async cmd=>cmd==='voice_shortcuts'?{active:window.nativeActive,bindings:{record:'Ctrl+Alt+Space',pause:'Shift',cancel:'Esc'}}:{};";
  else if(name==='/stub-dialog.js')body="export function paperDialog(){throw Error('Dialog not requested in this test')}export function paperButton(){}";
  else if(name==='/stub-lib.js')body="export {esc} from '/text.js';export const toast=value=>window.errors.push(String(value));";
  else if(name==='/style.css'){type='text/css; charset=utf-8';body=await source('paper-files.css')+'\n'+await source('window/styles/voice.css');}
  else if(name==='/'){
   type='text/html; charset=utf-8';body=`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"><style>:root{--ink:#342f2a;--ink-2:#776b60;--ink-3:#8a8277;--paper:#fffdf9;--surface-2:#f6f1e9;--accent:#8d593d;--accent-ink:#fff;--line:#d8cfc3;--line-strong:#baad9d;--sans:Arial,sans-serif;}*{box-sizing:border-box}body{margin:0;background:#f5f1eb;color:var(--ink);font:15px var(--sans)}main{max-width:660px;margin:35px auto;padding:20px}h1{font-size:22px;font-weight:500}.message{margin:20px 0;padding:15px;border:1px solid var(--line);border-radius:20px;background:var(--paper)}#composer{padding:14px;border:1px solid var(--line);border-radius:20px;display:flex;justify-content:space-between;background:var(--paper)}button{font:inherit}#mic{border:0;border-radius:50%;width:36px;height:36px;background:var(--accent);color:white}body.dark{--ink:#ede5db;--ink-2:#bcafa0;--ink-3:#a49685;--paper:#28251f;--surface-2:#302b24;--line:#51483b;--line-strong:#71614c;background:#211e19}</style><script>${fakeMedia}</script></head><body><main><h1>Голос · Hélène 2.5</h1><div id="messages"></div><div id="composer"><span>Сообщение Hélène…</span><button id="mic" aria-label="Записать голосовое">●</button></div></main></body></html>`;
  } else {res.writeHead(404);res.end();return;}
  res.writeHead(200,{'Content-Type':type});res.end(body);
 } catch(error){res.writeHead(500);res.end(String(error));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({headless:true,channel:process.env.HELENE_BROWSER_CHANNEL||'msedge'});
const checks=[];
await mkdir(out,{recursive:true});
try {
 const page=await browser.newPage({viewport:{width:980,height:820}});
 const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
 await page.goto(origin);
 await page.evaluate(async()=>{
  const {mountVoiceComposer}=await import('/voice-composer.js');
  window.composer=mountVoiceComposer({mic:document.querySelector('#mic'),composer:document.querySelector('#composer'),target:()=>({agent:'one',room:'window',name:'Hélène'}),send:async(blob,target)=>window.sent.push({size:blob.size,target})});
  window.states=[];new MutationObserver(()=>window.states.push(document.querySelector('.voice-draft').dataset.state)).observe(document.querySelector('.voice-draft'),{attributes:true,attributeFilter:['data-state']});
 });
 // Focused DOM chord sends on release without ever rendering a preview state.
 await page.keyboard.down('Control');await page.keyboard.down('Alt');await page.keyboard.down('Space');
 await page.waitForFunction(()=>document.querySelector('.voice-draft').dataset.state==='recording');
 assert.equal(await page.locator('[data-preview]').isVisible(),false);
 await page.waitForTimeout(320);await page.keyboard.up('Space');await page.keyboard.up('Alt');await page.keyboard.up('Control');
 await page.waitForFunction(()=>sent.length===1);
 assert.equal(await page.evaluate(()=>states.includes('paused')),false);
 checks.push('focused hotkey release sends once without preview');
 // The same application-native event sends regardless of document focus.
 for(const focused of [true,false]){
  const count=await page.evaluate(()=>sent.length);
  await page.evaluate(focused=>{Object.defineProperty(document,'hasFocus',{value:()=>focused,configurable:true});dispatchEvent(new CustomEvent('helene:voice-shortcut',{detail:{action:'down'}}));},focused);
  await page.waitForTimeout(320);await page.evaluate(()=>dispatchEvent(new CustomEvent('helene:voice-shortcut',{detail:{action:'up'}})));
  await page.waitForFunction(n=>sent.length===n,count+1);
 }
 checks.push('native hotkey release sends once both focused and in background');
 // Shift remains an explicit preview/pause; releasing the chord does not send.
 await page.keyboard.down('Control');await page.keyboard.down('Alt');await page.keyboard.down('Space');await page.waitForTimeout(320);await page.keyboard.press('Shift');
 await page.keyboard.up('Space');await page.keyboard.up('Alt');await page.keyboard.up('Control');
 assert.equal(await page.locator('.voice-draft').getAttribute('data-state'),'paused');assert.equal(await page.evaluate(()=>sent.length),3);
 assert.equal(await page.locator('[data-preview]').isVisible(),true);await page.keyboard.press('Escape');
 checks.push('explicit Shift pause and Esc cancellation retained');
 // A Shift modifier in the configured chord cannot accidentally pause it.
 await page.evaluate(()=>localStorage.setItem('helene.voice-ui',JSON.stringify({hotkey:'Ctrl+Shift+Space'})));
 await page.keyboard.down('Control');await page.keyboard.down('Shift');await page.keyboard.down('Space');await page.waitForTimeout(320);
 await page.keyboard.up('Space');await page.keyboard.up('Shift');await page.keyboard.up('Control');await page.waitForFunction(()=>sent.length===4);
 checks.push('Shift modifier chord sends without accidental pause');
 // Default auto playback is enabled, but an explicit owner mute is retained.
 assert.deepEqual(await page.evaluate(async()=>{const {voicePreferences}=await import('/voice-preferences.js');localStorage.removeItem('helene.voice-ui');const fresh=voicePreferences().autoplay;localStorage.setItem('helene.voice-ui','{"autoplay":false}');return [fresh,voicePreferences().autoplay];}),[true,false]);
 checks.push('autoplay default with explicit mute preserved');
 await page.evaluate(async()=>{
  const module=await import('/paper-audio.js');window.audioModule=module;
  window.waveReady=new Promise(resolve=>window.allowWave=()=>resolve({create:options=>{window.waveMedia=options.media;options.media.src='waveform-decoder-only';window.trace.push({event:'wave-create'});return {loadBlob:()=>new Promise(done=>window.finishWave=done),destroy(){}}}}));
  module.setAudioPlayer(()=>{window.trace.push({event:'wave-import'});return window.waveReady;});
  const m={path:'synthetic/voice.wav',kind:'audio',name:'voice-test.wav'};window.voiceMedia=m;
  document.querySelector('#messages').innerHTML='<div class="message"><p>Голосовое сообщение</p>'+module.audioHTML(m,'data-paper-path="synthetic/voice.wav" data-paper-name="voice-test.wav"')+'</div>';
  window.disposeAudio=module.mountPaperAudio(document.querySelector('main'),{key:m=>m.path,bytes:async m=>{window.trace.push({event:'bytes',path:m.path});return new Blob(['controlled audio bytes'],{type:'audio/wav'});},save:async()=>{}});
  dispatchEvent(new CustomEvent('frame-room',{detail:{scope:'one|window'}}));
 });
 assert.equal(await page.locator('.paper-audio-detail').isVisible(),false);
 assert.equal(await page.evaluate(()=>trace.filter(r=>r.event==='bytes').length),0,'history does not prefetch/decode every audio');
 await page.locator('[data-audio-toggle]').click();await page.waitForFunction(()=>trace.some(r=>r.event==='play'));
 assert.equal(await page.evaluate(()=>trace.some(r=>r.event==='wave-import')),false);
 const liveSource=await page.evaluate(()=>trace.find(r=>r.event==='play').src);
 await page.locator('[data-audio-details]').click();await page.waitForFunction(()=>trace.some(r=>r.event==='wave-import'));
 await page.evaluate(()=>allowWave());await page.waitForFunction(()=>trace.some(r=>r.event==='wave-create'));
 assert.equal(await page.evaluate(()=>trace.find(r=>r.event==='play').audio.src),liveSource);
 assert.equal(await page.evaluate(()=>waveMedia===trace.find(r=>r.event==='play').audio),false);
 checks.push('play begins before unresolved waveform import/decode; independent waveform media');
 await page.locator('[data-audio-details]').click();
 // Incoming answers received during recording play when recording ends.
 const before=await page.evaluate(()=>trace.filter(r=>r.event==='play').length);
 await page.evaluate(()=>{dispatchEvent(new Event('helene:recording'));dispatchEvent(new CustomEvent('helene:voice-incoming',{detail:voiceMedia}));});
 assert.equal(await page.evaluate(()=>trace.filter(r=>r.event==='play').length),before);
 await page.evaluate(()=>dispatchEvent(new Event('helene:recording-ended')));
 await page.waitForFunction(n=>trace.filter(r=>r.event==='play').length===n,before+1);
 checks.push('incoming answer queued during recording resumes on recording end');
 // A pending source must not start late after another recording or chat switch.
 await page.evaluate(()=>{disposeAudio();window.delayedReady=new Promise(resolve=>window.allowBytes=resolve);window.disposeAudio=audioModule.mountPaperAudio(document.querySelector('main'),{key:m=>m.path,bytes:()=>window.delayedReady,save:async()=>{}});dispatchEvent(new CustomEvent('frame-room',{detail:{scope:'one|window'}}));dispatchEvent(new CustomEvent('helene:voice-incoming',{detail:voiceMedia}));dispatchEvent(new Event('helene:recording'));});
 const cancelledBefore=await page.evaluate(()=>trace.filter(r=>r.event==='play').length);
 await page.evaluate(()=>allowBytes(new Blob(['controlled bytes'])));await page.waitForTimeout(60);
 assert.equal(await page.evaluate(()=>trace.filter(r=>r.event==='play').length),cancelledBefore);
 await page.evaluate(()=>{dispatchEvent(new CustomEvent('helene:voice-incoming',{detail:voiceMedia}));dispatchEvent(new CustomEvent('frame-room',{detail:{scope:'two|window'}}));dispatchEvent(new Event('helene:recording-ended'));});
 await page.waitForTimeout(60);assert.equal(await page.evaluate(()=>trace.filter(r=>r.event==='play').length),cancelledBefore);
 checks.push('recording and conversation change cancel pending playback across scopes');
 assert.equal(pageErrors.length,0,pageErrors.join('\n'));
 assert.equal(await page.evaluate(()=>errors.length),0);
 await page.evaluate(()=>{disposeAudio();composer.dispose();});
 await page.close();
 const screenshots=[];
 for(const width of [980,390])for(const theme of ['light','dark']){
  const p=await browser.newPage({viewport:{width,height:820}});await p.goto(origin);
  await p.evaluate(async theme=>{
   document.body.classList.toggle('dark',theme==='dark');const {audioHTML}=await import('/paper-audio.js');
   const row=(name,text)=>'<div class="message"><p>'+text+'</p>'+audioHTML({path:'synthetic/'+name,kind:'audio',name},'data-paper-path="synthetic/'+name+'" data-paper-name="'+name+'"')+'</div>';
   document.querySelector('#messages').innerHTML=row('voice-sent.wav','Отправленное голосовое')+row('0123456789abcdef0123456789abcdef.ogg','Голосовой ответ Hélène');
   const {mountVoiceComposer}=await import('/voice-composer.js');window.composer=mountVoiceComposer({mic:document.querySelector('#mic'),composer:document.querySelector('#composer'),target:()=>({agent:'one',room:'window',name:'Hélène'}),send:async()=>{}});
  },theme);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const boxes=await p.locator('.paper-audio').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {width:r.width,height:r.height,right:r.right};}));
  assert.ok(boxes.every(r=>r.height<=65&&r.right<=width));assert.ok(Math.abs(boxes[0].height-boxes[1].height)<1);
  const file=`voice-compact-${width}-${theme}.png`;await p.screenshot({path:path.join(out,file)});screenshots.push({file,width,theme,boxes});
  await p.evaluate(()=>dispatchEvent(new CustomEvent('helene:voice-shortcut',{detail:{action:'down'}})));
  await p.waitForFunction(()=>document.querySelector('.voice-draft').dataset.state==='recording');
  assert.equal(await p.locator('[data-preview]').isVisible(),false);
  const recording=`voice-recording-${width}-${theme}.png`;await p.screenshot({path:path.join(out,recording)});screenshots.push({file:recording,width,theme});
  await p.close();
 }
 const result={passed:true,checks,screenshots,level:'Exact UI modules in real Edge DOM with controlled native/media edges; no acoustic or owner-install acceptance'};
 await writeFile(path.join(out,'acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
