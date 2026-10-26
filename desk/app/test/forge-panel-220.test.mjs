// Executes the real production Forge panel, with deterministic DOM/transport.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {randomUUID} from 'node:crypto';

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
const factory=new Function('document','crypto','ApiError','channelURL','setTimeout','clearTimeout',code+';return {forgePanel};');
const sha='d'.repeat(64);
function task() {return {id:'code-fixture',goal:'Implement a parser → independent review → repair',status:'active',agents:[{id:'agent-fixture',role:'worker',model:'fixture-model',subtask:'Preserve authored edits while fixing parser.py',status:'unknown',request_status:'running',custody:'unknown',observation:{generation:'g-one',phase:'tool',tool:'coding_verify',observed_at:'2026-10-26T02:20:00Z'},result_ref:{kind:'result',sha256:sha}}]};}
function stand() {
  const root=new Element('section'),calls=[],timers=new Map();let seq=0;
  const state={tasks:[task()],error:'',eventError:'',delay:null,payloadDelay:null,commandError:false,commandState:'queued',receiptDelay:null};
  const content='<script>inert output</script>\n'+'complete text 🐍\n'.repeat(900);
  const read=async(path,signal,body)=>{
    calls.push({path,signal,body});
    if(body) {if(state.commandError)throw Error('lost reply');return {command_id:body.command_id,state:state.commandState};}
    if(path==='/api/forge/tasks'){if(state.delay)return await state.delay;if(state.error)throw Error(state.error);return structuredClone(state.tasks);}
    const url=new URL(path,'http://fixture.invalid');
    if(url.pathname==='/api/forge/control') {
      if(url.searchParams.has('task_id'))return {commands:[]};
      if(state.receiptDelay)return await state.receiptDelay;
      return {command_id:url.searchParams.get('command_id'),state:state.commandState,result:{status:'unknown'}};
    }
    if(url.pathname==='/api/forge/events'){
      if(state.eventError)throw Error(state.eventError);
      const offset=Number(url.searchParams.get('offset'));
      return {events:offset?[]:[{seq:1,kind:'tool_result',tool:'coding_verify',at:'2026-10-26T02:20:00Z',output_ref:{sha256:sha}}],next_offset:100,has_more:false,generation:'g-one'};
    }
    if(url.pathname==='/api/forge/payload'){
      if(state.payloadDelay)return await state.payloadDelay;
      const offset=Number(url.searchParams.get('offset')),end=Math.min(offset+8000,content.length);
      return {sha256:sha,text:content.slice(offset,end),offset,total_chars:content.length,next_offset:end<content.length?end:null};
    }
    throw Error(path);
  };
  const impl=factory(document,{randomUUID},Error,path=>path,(fn)=>{const id=++seq;timers.set(id,fn);return id;},id=>timers.delete(id));
  const panel=impl.forgePanel(root,read,10);
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
  assert.match(t.root.textContent,/инструмент: coding_verify/);assert.match(t.root.textContent,/живость процесса не проверялась/);
  const {team,unit}=await open(t);
  await find(unit,'Результат вызова').click();await tick();
  const payload=walk(unit).find(n=>n.tag==='pre');
  assert.equal(payload.textContent,t.content.slice(0,8000));
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
  release([task()]);await tick();
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
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
assert.match(main,/if \(from === "plans" && id !== "plans"\) plans\.dispose\(\);/);
console.log('Forge production panel: pagination, inert content, stable expansion, failure preservation, abort/late-response fences, controls/generation/unknown delivery, one poll lifetime PASS');
