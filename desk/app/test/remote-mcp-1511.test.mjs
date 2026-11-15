import './fixtures/ru-localization.mjs';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const {t,setLocale,locale}=await import(new URL('../../lang/lang.ts',import.meta.url));
const source=stripTypeScriptTypes(readFileSync(new URL('../src/remote-computer.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,''),{mode:'transform'}).replace(/export /g,'');
class Node{
 constructor(tag,cls='',text=''){Object.assign(this,{tagName:tag,className:cls,textContent:text,children:[],dataset:{},isConnected:true,hidden:false});}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
}
const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
for(const language of ['ru','en','fr','zh']){
 setLocale(language);let timers=[],calls=[],writes=[],held,hold=false,pending=0,maxPending=0;
 const picture={selected:'a',devices:[{id:'a',name:'AUTHORED <script> 日本語',connected:true,scopes:['computer.read']},
 {id:'b',name:'AUTHORED Вторая',connected:true,scopes:['computer.read']}]};
 let component={installed:true,runtime:{state:'running',running:true,tools:21}};
 const api=async path=>{calls.push(path);if(path.endsWith('/state'))return structuredClone(picture);
   assert.ok(path.endsWith('/windows-mcp'));pending++;maxPending=Math.max(maxPending,pending);
   const response={device_id:picture.selected,state:'confirmed',component:structuredClone(component)};
   if(hold)await new Promise(resolve=>{held=resolve;});pending--;return response;};
 const post=async(path,args)=>{writes.push({path,args});assert.ok(path.endsWith('/select'));picture.selected=args.device;};
 const el=(...args)=>new Node(...args),button=(text,cls,click)=>Object.assign(el('button',cls,text),{click});
 const {remoteComputerCard,remoteMcpLine}=new Function('uiText','UiError','locale','api','post','shell','el','button','card','toggle','humanError','window',source+';return {remoteComputerCard,remoteMcpLine};')(
   t,Error,locale,api,post,()=>{throw Error('no native mutation permitted');},el,button,(title,body)=>body,()=>{},e=>({text:String(e)}),{setTimeout:fn=>timers.push(fn)});
 const box=await remoteComputerCard({saved:{},host:{platform:'linux'}});await settle();
 const mcp=box.children[3],devices=box.children[2];assert.equal(mcp.hidden,false);
 assert.equal(mcp.textContent,t('remote.mcp.running',{count:(21).toLocaleString(locale())}));
 assert.ok(devices.children[0].children[0].textContent.includes('AUTHORED <script> 日本語'));
 assert.equal(remoteMcpLine({device_id:'wrong',state:'confirmed',component},'a'),t('remote.mcp.unknown'));
 assert.equal(remoteMcpLine({device_id:'a',state:'unknown',component:null},'a'),t('remote.mcp.unknown'));
 assert.equal(remoteMcpLine({device_id:'a',state:'confirmed',component:{installed:true,runtime:{state:'unknown',running:false,tools:0}}},'a'),t('remote.mcp.unknown'));
 hold=true;timers.shift()();await settle();assert.equal(pending,1);
 devices.children[1].children[1].click();await settle();
 assert.equal(writes.length,1);assert.equal(picture.selected,'b');assert.equal(mcp.textContent,t('remote.mcp.checking'));
 // Device selection remains responsive while the old status response is held.
 devices.children[0].children[1].click();await settle();assert.equal(picture.selected,'a');assert.equal(writes.length,2);
 assert.equal(mcp.textContent,t('remote.mcp.checking'));assert.equal(maxPending,1);
 hold=false;component={installed:false,runtime:{state:'stopped',running:false,tools:0}};held();await settle();
 assert.equal(mcp.textContent,t('remote.mcp.install'));assert.equal(maxPending,1);
 picture.devices[0].connected=false;timers.shift()();await settle();assert.equal(mcp.textContent,t('remote.mcp.offline'));
 picture.selected='';timers.shift()();await settle();assert.equal(mcp.hidden,true);
 box.isConnected=false;const before=calls.length;for(const callback of timers.splice(0))callback();await settle();assert.equal(calls.length,before);
}
setLocale('ru');
console.log('Remote MCP actual card: four languages, exact target, bounded reads, responsive selection, stale answers, unknown/offline/absent and disposal PASS');
