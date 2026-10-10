import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

const source=readFileSync(new URL('../../ui-kit/window/connection-guide.ts',import.meta.url),'utf8');
const calls=[];
const nodes=[];
function node(tag,text=''){const item={tag,text,children:[],isConnected:true,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},querySelectorAll(){return [];}};nodes.push(item);return item;}
let current={path:'owner-config',mtime_ns:'100',config:{mode:'local',telegram:{bot_token:'private-token',owner_id:'1'},phone:{enabled:false},model:{name:'owned-model'}}};
globalThis.__guideTest={
  el:(tag,cls,text)=>node(tag,text),
  button:(text,cls,action)=>Object.assign(node('button',text),{action}),
  field:(label,value,change)=>Object.assign(node('field'),{label,value,change}),toggle:()=>node('toggle'),
  humanError:error=>({text:error.message}),collectConnection:()=>null,
  api:async()=>({ok:true,config:current.config,mtime_ns:current.mtime_ns}),
  post:async(path,body)=>{calls.push({path,body});return {ok:true};},
  shell:async(command,args)=>{
    calls.push({command,args});
    if(command==='config_load')return structuredClone(current);
    if(command==='owner_state')return {stopped:true,runner_alive:false,pid:0};
    if(command==='config_save'){current={...current,mtime_ns:'101',config:JSON.parse(args.config)};return {ok:true};}
    if(command==='server_transfer'){
      if(args.action==='status')return {pending:false};
      if(args.action==='probe')return {ok:true,fingerprint:'SHA256:test',resumable:true,message:'ready'};
      if(args.action==='telegram-check')return {checks:[{ok:true,name:'Бот',message:'ready'}]};
      if(args.action==='run')return {ok:true,base:'https://moved.test',key:'moved-owner-key',server_locator:'telegram-menu'};
    }
    return null;
  },
};
const stub=`const {api,post,shell,el,button,field,toggle,humanError,collectConnection}=globalThis.__guideTest;const cfg={base:''};const inTauri=true;`;
const stripped=stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm,'')+'\nexport {save,stopForTransfer,drawExistingServer,drawNewServer};');
const {save,stopForTransfer,drawExistingServer,drawNewServer}=await import('data:text/javascript;base64,'+Buffer.from(stub+stripped).toString('base64'));

const session={config:structuredClone(current.config),stamp:'100',remote:false};
current.mtime_ns='changed-by-agent';
await assert.rejects(()=>save({telegram:{owner_id:'2'}},session),/уже изменились/);
assert.equal(calls.filter(row=>row.command==='config_save').length,0,'stale drafts must not overwrite live config');
assert.equal(current.config.telegram.owner_id,'1');
current.mtime_ns='100';
await save({phone:{enabled:true,mode:'automatic'}},session);
assert.deepEqual(current.config.model,{name:'owned-model'});
assert.equal(current.config.telegram.bot_token,'private-token');
assert.equal(session.stamp,'101');

const note={className:'',textContent:''};
await stopForTransfer(note);
assert.equal(calls.at(-2).command,'owner_control');
assert.equal(calls.at(-2).args.action,'panic');
assert.equal(calls.at(-1).command,'owner_state');
assert.equal(calls.filter(row=>row.command==='owner_control'&&row.args.action==='resume').length,0,'uncertain transfer must never auto-resume another bot');

current.config={...current.config,mode:'remote',base:'https://agent.test',key:'owner-test-key'};
calls.length=0;
const originalFetch=globalThis.fetch;
globalThis.fetch=async(url,options)=>{assert.equal(url,'https://agent.test/api/state');assert.equal(options.redirect,'error');return {ok:true,json:async()=>({agent:{name:'real remote agent'}})};};
try{
  drawExistingServer(node('existing-server'));
  await Promise.resolve();await Promise.resolve();
  await nodes.findLast(item=>item.tag==='button'&&item.text==='Проверить и подключить').action();
  assert.equal(calls.filter(row=>row.command==='config_save').length,1,'a remote-only window must be able to change its server');
  assert.equal(calls.filter(row=>row.command==='owner_control').length,0,'remote windows must not stop an unrelated local installation');
  assert.equal(calls.at(-1).command,'restart_self');
}finally{globalThis.fetch=originalFetch;}

current.config.mode='local';calls.length=0;
drawNewServer(node('new-server'));
await Promise.resolve();await Promise.resolve();
nodes.findLast(item=>item.label==='Адрес сервера').change('server.test');
await nodes.findLast(item=>item.text==='Проверить сервер').action();
// The UI click schedules probe() through void; settle its helper promise first.
await Promise.resolve();await Promise.resolve();
await nodes.findLast(item=>item.text==='Перенести и подключить').action();
assert.equal(calls.filter(row=>row.path==='/api/telegram/check').length,0,'the local channel can be stopped from a previous transfer attempt');
const move=calls.findIndex(row=>row.command==='server_transfer'&&row.args.action==='run');
const stopped=calls.findIndex(row=>row.command==='owner_state');
assert.ok(move>stopped&&stopped>=0,'transfer must wait for the local stop receipt');
assert.equal(current.config.mode,'remote');assert.equal(current.config.server_locator,'telegram-menu');
assert.equal(calls.at(-1).command,'restart_self');

const rpc=readFileSync(new URL('../../shell/src/host_rpc.rs',import.meta.url),'utf8');
assert.match(rpc,/"server_transfer"\s*=>\s*server_transfer\(/,'Electron must expose the same migration helper as Tauri');
assert.match(source,/kind==="server"&&inTauri\?await shell<Loaded>\("config_load"\)/,'server setup must stay accessible while remote API is down');
assert.match(source,/action:"telegram-check"/,'resuming a transfer must check Telegram without requiring the already stopped local channel');
console.log('connection-guide: ok');
