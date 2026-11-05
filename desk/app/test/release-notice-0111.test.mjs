import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
class Element {
  constructor(tag){Object.assign(this,{tag,children:[],hidden:false,textContent:'',attrs:{},events:new Map()});}
  append(...children){this.children.push(...children);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(name,callback){this.events.set(name,callback);}
  click(){this.events.get('click')?.();}
  remove(){this.removed=true;}
}
const code=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/release-notice.ts',import.meta.url),'utf8')).replace(/export /g,'');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(check){
  const events=new Map(),timers=new Map(),values=new Map();let opened=0;
  const document={hidden:false,createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)};
  const window={setInterval:fn=>{timers.set(1,fn);return 1;},clearInterval:id=>timers.delete(id)};
  const storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)};
  const mount=new Function('document','window',code+';return releaseNotice;')(document,window);
  const host=new Element('section'),component=mount(host,check,()=>opened++,{storage});
  return {host,component,events,timers,document,values,get opened(){return opened;}};
}
let calls=0;
let release={current:'3.0.0',latest:'3.1.0',newer:true};
const first=fixture(async()=>{calls++;return release;});await turn();
assert.equal(calls,1);assert.equal(first.host.hidden,false);assert.match(first.host.children[0].textContent,/3\.1\.0/);
assert.equal(first.opened,0);first.host.children[1].click();assert.equal(first.opened,1);
first.host.children[2].click();assert.equal(first.host.hidden,true);await first.component.refresh();assert.equal(first.host.hidden,true);
release={...release,latest:'3.2.0'};await first.component.refresh();assert.equal(first.host.hidden,false);
release={...release,newer:false};await first.component.refresh();assert.equal(first.host.hidden,true);
first.component.dispose();assert.equal(first.timers.size,0);assert.equal(first.events.size,0);
let resolve,coldCalls=0;
const cold=fixture(()=>{coldCalls++;return new Promise(done=>{resolve=done;});});
await cold.component.refresh();assert.equal(coldCalls,1,'Overlapping refresh never duplicates metadata requests');
cold.component.dispose();resolve({current:'3.0.0',latest:'3.4.0',newer:true});await turn();
assert.equal(cold.host.hidden,true);assert.equal(cold.host.removed,true);
const outage=fixture(async()=>{throw new Error('offline');});await turn();assert.equal(outage.host.hidden,true);outage.component.dispose();
const controlSource=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/update-operation-controls.ts',import.meta.url),'utf8')).replace(/export /g,'');
const controls=new Function(controlSource+';return updateOperationControls;')();
assert.deepEqual(controls('attention'),{active:true,cancellable:true,cancelLabel:'Закрыть неудавшуюся попытку'});
assert.equal(controls('cancelled').active,false);assert.equal(controls('superseded').active,false);assert.equal(controls('running').active,true);
const availability=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/feature-availability.ts',import.meta.url),'utf8')).replace(/export /g,'');
const flags=new Function(availability+';return {SERVER_TRANSFER_AVAILABLE,SERVER_TRANSFER_NOTICE};')();
// The product transfer is enabled again. Its real UI actions/Stop/SSH and
// no-effect-before-confirmation are exercised by connection-guide.test.mjs.
assert.equal(flags.SERVER_TRANSFER_AVAILABLE,true);
console.log('PASS: passive release notice, pending/dispose/dismissal, attention recovery controls, enabled server feature');
