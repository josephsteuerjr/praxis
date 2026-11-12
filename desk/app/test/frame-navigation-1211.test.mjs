import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseHTML} from 'linkedom';

const {document,window}=parseHTML('<html><body><main></main></body></html>');
// Linkedom does not implement the browser's select selection state.
Object.defineProperties(window.HTMLSelectElement.prototype,{
 selectedIndex:{get(){const n=[...this.options].findIndex(x=>x.hasAttribute('selected'));return n<0?0:n},set(n){[...this.options].forEach((x,i)=>x.toggleAttribute('selected',i===n))}},
 value:{get(){return this.options[this.selectedIndex]?.value||''},set(v){this.selectedIndex=[...this.options].findIndex(x=>x.value===v)}}
});
const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const captures=Array.from({length:65},(_,i)=>new Date(Date.UTC(2026,10,12,12,0,64-i)).toISOString().replaceAll('-','').replaceAll(':','').replace('.000Z','')+'-12345678.md');
const requests=[],errors=[],pending=new Map();
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const box=document.querySelector('main');
globalThis.__frameStand={
 S:{rooms:[{key:'other',name:'Другой чат'}],agent:'Jarvis',stream:'dm-window'},
 isWindowRoom:x=>x==='window',esc,fmtN:String,md:esc,
 q:(selector,root)=>root.querySelector(selector),
 safeRender:(_box,fn)=>{void fn().catch(e=>errors.push(e))},
 api:async path=>{
  requests.push(path);
  if(pending.has(path))return pending.get(path).promise;
  const u=new URL(path,'http://fixture');
  if(u.pathname==='/api/shadow')return [{stream:'dm-window',captures:65,latest:captures[0]},{stream:'dm-other',captures:1,latest:captures[1]}];
  if(u.pathname.endsWith('/captures')){const index=u.searchParams.has('before')?captures.indexOf(u.searchParams.get('before'))+1:0;return captures.slice(index,index+30)}
  if(u.pathname.endsWith('/capture'))return {text:u.searchParams.get('name'),tools:[{name:'computer',description:'<script>secret()</script>',input_schema:{properties:{timeout_ms:{type:'integer'}}}}]};
  if(u.pathname.endsWith('/metrics'))return [{stream:u.pathname,ts:'synthetic'}];
  if(u.pathname.endsWith('/diff'))return {zones:[],diff:['<script>diff()</script>']};
  throw new Error(path);
 }
};
const temp=mkdtempSync(join(tmpdir(),'helene-frame-'));
try{
 let source=readFileSync(new URL('../../ui-kit/window/views/frame.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
 source='const {api,esc,fmtN,md,q,safeRender,S,isWindowRoom}=globalThis.__frameStand;\n'+source;
 const file=join(temp,'frame.ts');writeFileSync(file,source);
 const {render,captureTime}=await import(pathToFileURL(file));
 await render(box);await tick();
 assert.match(box.textContent,/Основной чат · Jarvis/);
 assert.equal(box.querySelector('#cap-new').options.length,30);
 assert.match(box.querySelector('[data-tool="computer"]').textContent,/timeout_ms/);
 assert.equal(box.querySelectorAll('script').length,0,'schema JSON must be escaped');
 assert.match(captureTime(captures[0]),/2026/);assert.notEqual(captureTime(captures[0]),'12.11 12:01:04');
 box.querySelector('#cap-prev').click();await tick();assert.equal(box.querySelector('#cap-new').value,captures[1]);
 box.querySelector('#cap-next').click();await tick();assert.equal(box.querySelector('#cap-new').value,captures[0]);
 box.querySelector('#cap-more').click();await tick();assert.equal(box.querySelector('#cap-new').options.length,60);
 box.querySelector('#cap-more').click();await tick();assert.equal(box.querySelector('#cap-new').options.length,65);assert.equal(box.querySelector('#cap-more').hidden,true);
 const search=box.querySelector('#frame-search');search.value='другой';search.dispatchEvent(new window.Event('input'));
 assert.equal(box.querySelector('[data-stream="dm-window"]').hidden,true);
 assert.equal(box.querySelector('[data-stream="dm-other"]').hidden,false);
 // An old success and an old failure must not repaint the newly selected chat.
 const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
 const stalePath='/api/shadow/dm-window/capture?name='+encodeURIComponent(captures[1]);
 let stale=deferred();pending.set(stalePath,stale);
 box.querySelector('#cap-prev').click();await tick();
 box.querySelector('[data-stream="dm-other"]').click();await tick();
 stale.resolve({text:'STALE RESPONSE',tools:[]});await tick();assert.ok(!box.textContent.includes('STALE RESPONSE'));
 box.querySelector('#do-metrics').click();await tick();assert.ok(requests.at(-1).endsWith('/dm-other/metrics'));
 pending.delete(stalePath);box.querySelector('[data-stream="dm-window"]').click();await tick();
 stale=deferred();pending.set(stalePath,stale);box.querySelector('#cap-prev').click();await tick();
 box.querySelector('[data-stream="dm-other"]').click();await tick();stale.reject(new Error('old request failed'));await tick();
 assert.equal(errors.length,0);
 console.log('frame-navigation: selection, paging, local time, schemas, escaping, stream metrics and racing success/failure PASS');
}finally{delete globalThis.__frameStand;rmSync(temp,{recursive:true,force:true})}
