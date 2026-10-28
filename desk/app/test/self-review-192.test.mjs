import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

class Element {
  constructor(tag, className='', text='') { Object.assign(this,{tag,className,textContent:text,children:[],listeners:{},attrs:{},dataset:{},style:{},isConnected:true,disabled:false,value:''}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  getAttribute(key) { return this.attrs[key]; }
  addEventListener(name,action) { (this.listeners[name] ||= []).push(action); }
  async click() { if(!this.disabled)for(const action of this.listeners.click || [])await action(); }
}
const el=(tag,cls='',text='')=>new Element(tag,cls,text);
const button=(text,kind,action)=>{const node=el('button',kind,text);node.addEventListener('click',action);return node;};
const field=(text,value,input)=>{const node=el('label','',text);node.value=value;node.input=value=>{node.value=value;input(value);};return node;};
const toggle=(text,value,input)=>{const node=button(text,'',()=>{node.value=!node.value;input(node.value)});node.value=value;return node;};
const card=(title,...children)=>{const node=el('section','',title);node.append(...children);return node;};
const choice=(items,value,input)=>{const node=el('div');for(const item of items)node.append(button(item.title,'',()=>input(item.value)));return {el:node};};
const source=readFileSync(new URL('../../ui-kit/window/views/self-review-card.ts',import.meta.url),'utf8');
const code=stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'');
const factory=new Function('button','card','choice','el','field','toggle','api','post','humanError',code+';return {selfReviewCard,validateReviewPolicy,reviewVerdict};');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const walk=node=>[node,...node.children.flatMap(walk)];
const find=(root,text)=>walk(root).find(node=>node.textContent===text);
const text=root=>walk(root).map(node=>node.textContent).join('\n');
const policy={after_update:false,after_self_edit:true,depth:'normal',models:[],max_parallel:1,max_iters:20,extra:'retain'};
const subject='c'.repeat(64);
function stand({available=true,reviews=[],saveError='',postError=''}={}) {
  const calls=[],accepted=[],draft={mode:'local'}, config={ok:true,config:{self_review:structuredClone(policy)},mtime_ns:'1'};
  const status={schema:'helene.self-review.status.v1',capability:{supported:available,available,reason:'fixture'},runtime:{self_edit_supported:available},reviews,commands:[]};
  let reads=0, delay;
  const api=async path=>{reads++;return structuredClone(path==='/api/self-review'?status:config)};
  const post=async(path,body)=>{
    calls.push({path,body});
    if(path==='/api/agent-config') {
      if(delay)await delay;
      if(saveError)throw Error(saveError);
      return {ok:true,mtime_ns:'2'};
    }
    if(postError)throw Error(postError);
    return {ok:true,command_id:'command-fixture',state:'queued'};
  };
  const impl=factory(button,card,choice,el,field,toggle,api,post,error=>({text:error.message}));
  const node=impl.selfReviewCard({draft,freshness:{accept:(...args)=>accepted.push(args)}});
  return {node,calls,accepted,draft,status,impl,get reads(){return reads;},delay(value){delay=value}};
}
{
  const test=stand();await tick();assert.equal(test.reads,2);
  assert.match(text(test.node),/совмещения изменённого агента/);
  assert.equal(find(test.node,'Сохранить саморевью').disabled,true);
  find(test.node,'После обычного обновления').click();
  await find(test.node,'Сохранить саморевью').click();
  assert.equal(test.calls[0].body.mtime_ns,'1');
  assert.equal(test.calls[0].body.config.self_review.extra,'retain');
  assert.equal(test.draft.self_review.after_update,true);
  assert.deepEqual(test.accepted,[['1','2']]);
  find(test.node,'Повторить уже завершённую проверку тех же исходников').click();
  await find(test.node,'Запросить ревью').click();await tick();
  assert.equal(test.calls.at(-1).body.retry,true);
  assert.match(text(test.node),/в очереди/);assert.match(text(test.node),/ещё не выполненное действие/);
  assert.equal(find(test.node,'Запросить ревью').disabled,true);
  assert.equal(test.reads,2,'no hidden polling or automatic receipt replay');
  await find(test.node,'Обновить статус').click();await tick();
  assert.equal(find(test.node,'Запросить ревью').disabled,false);
}
{
  const test=stand({saveError:'409 conflict'});await tick();
  find(test.node,'Модели критиков').input('reviewer-a, reviewer-b');
  await find(test.node,'Сохранить саморевью').click();
  assert.match(text(test.node),/409 conflict/);assert.equal(test.draft.self_review,undefined);
  assert.equal(find(test.node,'Модели критиков').value,'reviewer-a, reviewer-b');
  assert.equal(find(test.node,'Сохранить саморевью').disabled,false);
}
{
  const test=stand();await tick();let release;
  test.delay(new Promise(resolve=>{release=resolve}));
  find(test.node,'После обычного обновления').click();
  const saving=find(test.node,'Сохранить саморевью').click();
  find(test.node,'Модели критиков').input('later-edit');
  release();await saving;
  assert.match(text(test.node),/Более поздний ввод ещё не сохранён/);
  assert.equal(test.draft.self_review.models.length,0);
  assert.equal(find(test.node,'Сохранить саморевью').disabled,false);
}
{
  const test=stand({available:false});await tick();
  assert.equal(find(test.node,'Запросить ревью').disabled,true);
  assert.match(text(test.node),/Автоматическое ревью самоправок не поддержано/);
}
{
  const test=stand({postError:'lost response'});await tick();
  await find(test.node,'Запросить ревью').click();await tick();
  assert.match(text(test.node),/Результат передачи не подтверждён/);
  await find(test.node,'Запросить ревью').click();assert.equal(test.calls.length,1);
}
{
  const review={id:'review-1',required:true,execution:'running',state:'pending',verdict:'clear',subject_sha256:subject,dispositions:[]};
  const test=stand({reviews:[review]});await tick();
  assert.doesNotMatch(text(test.node),/Замечаний к предмету проверки нет/);
  const select=walk(test.node).find(node=>node.tag==='select');
  assert.equal(select.children[0].disabled,true);
  await find(test.node,'Записать решение').click();assert.equal(test.calls.length,0);
  find(test.node,'Обоснование решения').input('Concrete remaining issue');
  await find(test.node,'Записать решение').click();await tick();
  assert.equal(test.calls[0].body.subject_sha256,subject);
  assert.equal(test.calls[0].body.decision,'fix');
  assert.equal(test.calls[0].body.reason,'Concrete remaining issue');
  await find(test.node,'Обновить статус').click();await tick();
  await find(test.node,'Запросить отмену проверки').click();await tick();
  assert.deepEqual(test.calls.at(-1).body,{action:'cancel',id:'review-1'});
}
{
  const test=stand();test.node.isConnected=false;await tick();assert.equal(test.reads,0);
  const impl=test.impl;
  assert.match(impl.validateReviewPolicy({...policy,max_parallel:0}),/от 1 до 8/);
  assert.match(impl.validateReviewPolicy({...policy,max_iters:NaN}),/от 1 до 200/);
  assert.match(impl.validateReviewPolicy({...policy,models:['https://wrong']}),/URL/);
  assert.equal(impl.validateReviewPolicy(policy),'');
}
console.log('self-review UI: CAS, unknown preservation, queue/failure, no polling, unavailable, disposition/cancel, validation PASS');
