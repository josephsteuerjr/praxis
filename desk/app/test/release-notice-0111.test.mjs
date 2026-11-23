import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import './fixtures/ru-localization.mjs';
class Element {
  constructor(tag){Object.assign(this,{tag,children:[],hidden:false,textContent:'',attrs:{},events:new Map()});}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(name,callback){this.events.set(name,callback);}
  click(){this.events.get('click')?.();}
  remove(){this.removed=true;}
}
const code=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/release-notice.ts',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'').replace(/export /g,'');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(check) {
  const events=new Map(), timers=new Map(), restarts=[]; let opened=0;
  const document={hidden:false,createElement:tag=>new Element(tag),addEventListener:(n,f)=>events.set(n,f),removeEventListener:n=>events.delete(n)};
  const window={setInterval:f=>{timers.set(1,f);return 1},clearInterval:id=>timers.delete(id),addEventListener:(n,f)=>events.set(n,f),removeEventListener:n=>events.delete(n)};
  const mount=new Function('document','window',code+';return releaseNotice;')(document,window);
  const host=new Element('section'),component=mount(host,check,async sha=>{restarts.push(sha)},()=>opened++,e=>{throw e});
  return {host,component,events,timers,restarts,get opened(){return opened}};
}
let result={state:'preparing'},calls=0;
const f=fixture(async()=>{calls++;return result});await turn();
assert.equal(f.host.hidden,true);assert.equal(calls,1);assert.deepEqual(f.restarts,[]);
result={state:'ready',latest:'2.9.9',sha256:'a'.repeat(64)};await f.component.refresh();
assert.equal(f.host.hidden,false);assert.match(f.host.children[0].textContent,/2\.9\.9/);
f.host.children[1].click();assert.equal(f.opened,1);assert.deepEqual(f.restarts,[]);
f.host.children[0].click();f.host.children[0].click();await turn();assert.deepEqual(f.restarts,[result.sha256]);
f.component.dispose();assert.equal(f.timers.size,0);assert.equal(f.events.size,0);
for(const state of ['disabled','current','error']) {
  const disabled=fixture(async()=>({state}));await turn();assert.equal(disabled.host.hidden,true);disabled.component.dispose();
}
let resolve,n=0;const cold=fixture(()=>{n++;return new Promise(done=>{resolve=done})});
await cold.component.refresh();assert.equal(n,1);cold.component.dispose();resolve(result);await turn();
assert.equal(cold.host.hidden,true);assert.equal(cold.host.removed,true);
const controlSource=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/update-operation-controls.ts',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'').replace(/export /g,'');
const controls=new Function(controlSource+';return updateOperationControls;')();
assert.deepEqual(controls('attention'),{active:true,cancellable:true,cancelLabel:'Закрыть неудавшуюся попытку'});
assert.equal(controls('cancelled').active,false);assert.equal(controls('superseded').active,false);assert.equal(controls('running').active,true);
const availability=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/feature-availability.ts',import.meta.url),'utf8')).replace(/^import .*;\r?\n/gm,'').replace(/export /g,'');
const flags=new Function(availability+';return {SERVER_TRANSFER_AVAILABLE,SERVER_TRANSFER_NOTICE};')();
// The product transfer is enabled again. Its real UI actions/Stop/SSH and
// no-effect-before-confirmation are exercised by connection-guide.test.mjs.
assert.equal(flags.SERVER_TRANSFER_AVAILABLE,true);
console.log('PASS: prepared release notice, restart/keep paths, pending/dispose, attention recovery controls, enabled server feature');
