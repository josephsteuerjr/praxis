// Executes the real production Forge panel, with deterministic DOM/transport.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {webcrypto} from 'node:crypto';

class Element {
  constructor(tag) { this.tag=tag;this.className='';this.children=[];this.listeners={};this.attrs={};this.style={};this._text='';this.isConnected=true;this.parentNode=null;this.disabled=false;this.hidden=false;this.open=false; }
  set textContent(text) { this.children=[];this._text=String(text); }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  get firstChild() {return this.children[0] || null;}
  append(...children) { for(const child of children){if(child.parentNode)child.remove();child.parentNode=this;this.children.push(child);} }
  replaceChildren(...children) {for(const c of this.children)c.parentNode=null;this.children=[];this._text='';this.append(...children);}
  remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(c=>c!==this);this.parentNode=null;}
  setAttribute(k,v) {this.attrs[k]=String(v);}
  addEventListener(k,fn) {(this.listeners[k] ||= []).push(fn);}
  async emit(k) {for(const fn of this.listeners[k] || [])await fn();}
  async click() {if(!this.disabled)await this.emit('click');}
}
const document={createElement:tag=>new Element(tag)};
const walk=node=>[node,...node.children.flatMap(walk)];
const find=(root,text)=>walk(root).find(n=>n.tag==='button' && n.textContent===text);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const source=readFileSync(new URL('../../ui-kit/window/views/forge-panel.ts',import.meta.url),'utf8');
const code=stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'');
const factory=new Function('document','crypto','ApiError','channelURL','setTimeout','clearTimeout','cfg','ACTIONS = {}',code+';return {forgePanel};');
const sha='d'.repeat(64);
function task() {return {id:'code-fixture',goal:'Implement a parser → independent review → repair',status:'active',agents:[{id:'agent-fixture',role:'worker',model:'fixture-model',subtask:'Preserve authored edits while fixing parser.py',status:'unknown',request_status:'running',custody:'unknown',observation:{generation:'g-one',phase:'tool',tool:'coding_verify',observed_at:'2026-10-26T02:20:00Z'},result_ref:{kind:'result',sha256:sha}}]};}
function stand(options={}) {
  const root=new Element('section'),calls=[],timers=new Map();let seq=0;
  const state={tasks:[task()],recent:[],recentPage:[],nextCursor:null,scope:'one',eventPage:null,error:'',eventError:'',delay:null,payloadDelay:null,commandError:false,commandState:'queued',receiptDelay:null};
  const content='<script>inert output</script>\n'+'complete text 🐍\n'.repeat(900);
  const read=async(path,signal,body)=>{
    calls.push({path,signal,body});
    if(body) {if(state.commandError)throw Error('lost reply');return {command_id:body.command_id,state:state.commandState};}
    if(path==='/api/forge/tasks?view=active'){if(state.delay)return await state.delay;if(state.error)throw Error(state.error);return {active:structuredClone(state.tasks),recent:structuredClone(state.recent),next_cursor:state.nextCursor};}
    const url=new URL(path,'http://fixture.invalid');
    if(url.pathname==='/api/forge/tasks')return {active:structuredClone(state.tasks),recent:structuredClone(state.recentPage),next_cursor:null};
    if(url.pathname==='/api/forge/control') {
      if(url.searchParams.has('task_id'))return {commands:[]};
      if(state.receiptDelay)return await state.receiptDelay;
      return {command_id:url.searchParams.get('command_id'),state:state.commandState,result:{status:'unknown'}};
    }
    if(url.pathname==='/api/forge/events'){
      if(state.eventError)throw Error(state.eventError);
      if(state.eventPage)return state.eventPage;
      const offset=Number(url.searchParams.get('offset'));
      return {events:offset?[]:[{seq:1,kind:'tool_result',tool:'coding_verify',at:'2026-10-26T02:20:00Z',output_ref:{sha256:sha}}],next_offset:100,has_more:false,generation:'g-one'};
    }
    if(url.pathname==='/api/forge/payload'){
      if(state.payloadDelay)return await state.payloadDelay;
      const points=Array.from(content),offset=Number(url.searchParams.get('offset')),end=Math.min(offset+8000,points.length);
      return {sha256:sha,text:points.slice(offset,end).join(''),offset,total_chars:points.length,next_offset:end<points.length?end:null};
    }
    throw Error(path);
  };
  const impl=factory(document,{getRandomValues:array=>webcrypto.getRandomValues(array)},Error,path=>path,(fn)=>{const id=++seq;timers.set(id,fn);return id;},id=>timers.delete(id),{});
  const panel=impl.forgePanel(root,read,10,{scope:()=>state.scope,...options});
  return {root,calls,state,panel,timers,content};
}
async function open(test) {
  const team=walk(test.root).find(n=>n.className==='fold forge-task');team.open=true;await team.emit('toggle');
  const unit=walk(test.root).find(n=>n.className==='fold forge-unit');unit.open=true;await unit.emit('toggle');await tick();
  return {team,unit};
}
{
  const t=stand();t.panel.start();await tick();
  assert.match(t.root.textContent,/fixture-model/);assert.match(t.root.textContent,/состояние не подтверждено/);
  assert.match(t.root.textContent,/инструмент: coding_verify/);assert.match(t.root.textContent,/живость процесса неизвестна/);
  const {team,unit}=await open(t);
  await find(unit,'Результат вызова').click();await tick();
  const payload=walk(unit).find(n=>n.tag==='pre');
  assert.equal(payload.textContent,Array.from(t.content).slice(0,8000).join(''));
  await find(unit,'Следующие 8000 знаков').click();await tick();
  assert.equal(payload.textContent,t.content);
  assert.equal(find(unit,'Следующие 8000 знаков').hidden,true);
  assert.match(payload.textContent,/<script>/,'output is inert textContent, not HTML');
  await t.panel.refresh();await tick();
  assert.equal(walk(t.root).find(n=>n.className==='fold forge-task'),team);
  assert.equal(walk(t.root).find(n=>n.className==='fold forge-unit'),unit);
  assert.equal(team.open,true);assert.equal(unit.open,true);assert.equal(payload.textContent,t.content);
  assert.equal(t.timers.size,1,'manual refresh replaces rather than multiplies poll timers');
  t.state.error='disk unavailable';await t.panel.refresh();await tick();
  assert.match(t.root.textContent,/disk unavailable/);assert.match(t.root.textContent,/fixture-model/);
  t.panel.stop();assert.equal(t.timers.size,0);
}
{
  const t=stand();let release;t.state.delay=new Promise(resolve=>release=resolve);
  t.panel.start();await tick();const before=t.root.textContent;
  t.panel.stop();assert.equal(t.calls[0].signal.aborted,true);
  release({active:[task()],recent:[],next_cursor:null});await tick();
  assert.equal(t.root.textContent,before,'late list cannot paint after leaving the tab');assert.equal(t.timers.size,0);
  t.state.delay=null;t.panel.start();await tick();assert.match(t.root.textContent,/fixture-model/);t.panel.stop();
}
{
  const t=stand();t.panel.start();await tick();const {unit}=await open(t);
  let release;t.state.payloadDelay=new Promise(resolve=>release=resolve);
  await find(unit,'Итог участника').click();await tick();t.panel.stop();
  release({sha256:sha,offset:0,text:'LATE_PRIVATE_OUTPUT',total_chars:19,next_offset:null});await tick();
  assert.doesNotMatch(t.root.textContent,/LATE_PRIVATE_OUTPUT/);assert.equal(t.timers.size,0);
}
{
  const t=stand();t.panel.start();await tick();const {unit}=await open(t);
  await find(unit,'Остановить участника').click();await tick();
  const sent=t.calls.filter(c=>c.body);assert.equal(sent.length,1);
  assert.equal(sent[0].body.agent_id,'agent-fixture');assert.equal(sent[0].body.expected_generation,'g-one');
  assert.match(unit.textContent,/в очереди; действие ещё не подтверждено/);
  await find(unit,'Остановить участника').click();await tick();assert.equal(t.calls.filter(c=>c.body).length,1);
  t.state.commandState='observed';await t.panel.refresh();await tick();
  assert.doesNotMatch(unit.textContent,/в очереди; действие ещё не подтверждено/,'observed receipt replaces queued feedback');
  assert.match(unit.textContent,/Получена расписка Forge:.*unknown/);
  t.state.tasks[0].agents[0].observation.generation='g-two';await t.panel.refresh();await tick();
  await find(unit,'Остановить участника').click();await tick();
  assert.equal(t.calls.filter(c=>c.body).at(-1).body.expected_generation,'g-two','control binds the latest observation, not a stale closure');
  t.panel.stop();
}
{
  const t=stand();t.panel.start();await tick();const {unit}=await open(t);
  t.state.commandError=true;await find(unit,'Остановить участника').click();await tick();
  assert.match(unit.textContent,/Результат передачи не подтверждён/);
  await find(unit,'Остановить участника').click();await tick();assert.equal(t.calls.filter(c=>c.body).length,1);
  t.panel.stop();
}
{
  const t=stand();t.state.eventError='journal invalid';t.panel.start();await tick();await open(t);
  assert.match(t.root.textContent,/История не прочиталась: journal invalid/);assert.doesNotMatch(t.root.textContent,/Записанных шагов пока нет/);t.panel.stop();
}
{
  const t=stand();t.panel.start();await tick();const {unit}=await open(t);
  await find(unit,'Остановить участника').click();await tick();
  const id=t.calls.filter(c=>c.body).at(-1).body.command_id;
  let release;t.state.receiptDelay=new Promise(resolve=>release=resolve);
  const refresh=t.panel.refresh();await tick();
  release({command_id:'wrong-id',state:'observed',result:{status:'stopped'}});await refresh;await tick();
  assert.match(unit.textContent,/в очереди; действие ещё не подтверждено/,'another command receipt cannot repaint feedback');
  t.state.receiptDelay=new Promise(resolve=>release=resolve);
  const observed=t.panel.refresh();await tick();
  release({command_id:id,state:'unknown',reason:'prior effect unresolved'});await observed;await tick();
  assert.doesNotMatch(unit.textContent,/в очереди; действие ещё не подтверждено/);
  assert.match(unit.textContent,/Результат управления неизвестен.*prior effect unresolved/);
  t.panel.stop();
}
// The real switch hook is intentionally a narrow, checked integration hunk.
{
  const t=stand({compact:true});t.state.tasks[0].status='done';
  t.state.tasks[0].agents.push({...t.state.tasks[0].agents[0],id:'grandchild',parent_unit_id:'agent-fixture',depth:1,status:'stop_pending'});
  const done=n=>({...task(),id:'done-'+n,goal:'done-'+n,status:'done',agents:[{...task().agents[0],status:'done'}]});
  t.state.recent=Array.from({length:20},(_,n)=>done(n));t.state.recentPage=Array.from({length:15},(_,n)=>done(n+20));t.state.nextCursor='exact-cursor';
  t.panel.start();await tick();
  assert.match(t.root.textContent,/остановка не подтверждена/);
  assert.equal(find(t.root,'Остановить участника'),undefined,'compact shared viewer is read-only, Plans retains controls');
  await find(t.root,'Ещё завершённые').click();await tick();
  assert.match(t.root.textContent,/done-34/);assert.match(t.root.textContent,/grandchild/);
  assert.match(t.calls.at(-1).path,/cursor=exact-cursor/);
  assert.equal(find(t.root,'Ещё завершённые').hidden,true);t.panel.stop();
}
{
  const t=stand();t.panel.start();await tick();const {unit}=await open(t);
  const selected=find(unit,'Итог участника');await t.panel.refresh();await tick();
  assert.equal(find(unit,'Итог участника'),selected,'stable reference retains focus identity across polls');
  let release;t.state.payloadDelay=new Promise(resolve=>release=resolve);
  await selected.click();await tick();
  t.state.scope='different-owner';t.state.tasks[0].agents[0].model='NEW_OWNER_MODEL';t.panel.start();await tick();
  release({sha256:sha,offset:0,text:'OLD_PRIVATE_PAYLOAD',total_chars:19,next_offset:null});await tick();
  assert.doesNotMatch(t.root.textContent,/OLD_PRIVATE_PAYLOAD|fixture-model/);assert.match(t.root.textContent,/NEW_OWNER_MODEL/);t.panel.stop();
}
{
  const t=stand();t.state.eventPage={generation:'g-one',events:[{seq:3,kind:'tool_result',tool:'SHOULD_NOT_PAINT'}],next_offset:99,has_more:false};
  t.panel.start();await tick();await open(t);assert.match(t.root.textContent,/Нарушена последовательность истории/);assert.doesNotMatch(t.root.textContent,/SHOULD_NOT_PAINT/);t.panel.stop();
}
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
{
  const t=stand({compact:true});
  t.state.eventPage={generation:'g-one',events:[{seq:1,kind:'tool_start',call_id:'same',iteration:1,tool:'run',input_ref:{sha256:sha}}],next_offset:100,has_more:true};
  t.panel.start();await tick();const {unit}=await open(t);
  const action=walk(unit).find(n=>n.className.includes('forge-action st'));
  assert(action,'a real worker call is a folded action');assert.equal(action.open,false);
  assert.match(action.textContent,/запрос записан/);
  action.open=true;const args=find(action,'Аргументы');
  t.state.eventPage={generation:'g-one',events:[
    {seq:2,kind:'tool_dispatch',call_id:'same',iteration:1,tool:'run',receipt:{handler:'forge_worker._dispatch',status:'entered'}},
    {seq:3,kind:'tool_result',call_id:'same',iteration:1,tool:'run',input_ref:{sha256:sha},output_ref:{sha256:sha}},
    {seq:4,kind:'tool_start',call_id:'same',iteration:2,tool:'inspect'},
    {seq:5,kind:'tool_result',call_id:'same',iteration:2,tool:'inspect',output_ref:{sha256:sha}},
    {seq:6,kind:'tool_start',call_id:'same',iteration:2,tool:'inspect'},
  ],next_offset:500,has_more:false};
  await find(unit,'Следующие шаги').click();await tick();
  const actions=walk(unit).filter(n=>n.className.includes('forge-action st'));
  assert.equal(actions.length,3,'repeated provider IDs and iterations remain distinct calls');
  assert.equal(actions[0],action);assert.equal(action.open,true,'receipts update the existing expanded action');
  assert.equal(find(action,'Аргументы'),args,'arrival of a result preserves argument control focus');
  assert.match(action.textContent,/результат получен/);assert.match(action.textContent,/Харнесс · forge_worker\._dispatch/);
  assert(t.calls.some(c=>c.path.includes('after_seq=1')),'continuation uses the exact journal cursor');
  t.panel.stop();
}
{
  const t=stand({compact:true});
  t.state.tasks[0].agents=Array.from({length:20},(_,i)=>({...task().agents[0],id:'participant-'+i,subtask:'Narrow participant '+i}));
  t.panel.start();await tick();
  const units=walk(t.root).filter(n=>n.className.includes('forge-unit') && n.tag==='details');
  assert.equal(units.length,20,'compact roster never caps participant count');
  assert(units.every(n=>!n.open),'participant details start collapsed');
  units[19].open=true;await units[19].emit('toggle');await tick();
  assert(t.calls.some(c=>c.path.includes('unit_id=participant-19')),'last participant can load actions');
  await t.panel.refresh();await tick();
  assert.equal(units[19].open,true,'refresh preserves expanded participant');
  t.panel.stop();
}
assert.match(main,/if \(from === "plans" && id !== "plans"\) plans\.dispose\(\);/);
{
  const t=stand({compact:true});const unit=t.state.tasks[0].agents[0];
  unit.status='done';unit.started=Date.parse('2026-01-01T00:00:00Z')/1000;unit.finished='2026-01-01T00:01:30Z';
  t.panel.start();await tick();
  assert.match(t.root.textContent,/ждёт решения агента/,'terminal result is distinct from its acceptance');
  assert.equal(walk(t.root).find(n=>n.className==='forge-unit-time').textContent,'1 мин 30 с');
  unit.current_disposition='integrated';await t.panel.refresh();await tick();assert.match(t.root.textContent,/принят/);
  t.panel.stop();
}
assert.match(main,/if \(from === 'talk' && id !== 'talk'\) panel\.dispose\(\);/);
console.log('Forge production panel: pagination, inert content, stable expansion, failure preservation, abort/late-response fences, controls/generation/unknown delivery, one poll lifetime PASS');
