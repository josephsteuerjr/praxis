import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../../resources/first-start.html',import.meta.url),'utf8');
function fixture(){
  const elements={},calls=[],timers=[];
  const document={getElementById(id){return elements[id]??={value:'',hidden:false,disabled:false,textContent:'',focus(){this.focused=true}};}};
  const context=vm.createContext({document,setTimeout(fn,delay){timers.push({fn,delay})},window:{__HELENE__:{
    async invoke(command,args){calls.push({command,args});return command==='first_start'?{stage:'starting'}:{created:true};}
  }}});
  const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];vm.runInContext(source,context);
  return {elements,calls,timers,context,show:value=>context.show(value)};
}

test('one automatic preparation leads to either progress, creation, or the existing agent',async()=>{
  const view=fixture();await Promise.resolve();
  assert.equal(view.calls.filter(c=>c.command==='first_start').length,1);
  assert.equal(view.show({stage:'data',label:'Переносим данные'}),true);
  assert.equal(view.elements.stage.textContent,'Переносим данные');
  assert.equal(view.show({stage:'create'}),false);
  assert.equal(view.elements.form.hidden,false);assert.equal(view.elements.preparing.hidden,true);
  assert.equal(view.elements.name.focused,true);
  view.show({stage:'ready'});view.show({stage:'ready'});await Promise.resolve();
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,1);
  assert.equal(view.elements.form.hidden,true);
});

test('minimal credential form sends only its values and clears the key before opening',async()=>{
  const view=fixture();await Promise.resolve();view.show({stage:'create'});
  for(const [id,value] of Object.entries({name:'Вера',owner:'Марк',provider:'openai',model:'fixture',key:'private-fixture',base:''})){
    view.context.document.getElementById(id).value=value;
  }
  await view.elements.form.onsubmit({preventDefault(){}});
  const created=view.calls.find(c=>c.command==='first_create');
  assert.deepEqual(JSON.parse(JSON.stringify(created.args)),{name:'Вера',owner:'Марк',provider:'openai',model:'fixture',key:'private-fixture',base_url:''});
  assert.equal(view.elements.key.value,'');
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,1);
  assert.ok(!Object.values(view.elements).some(el=>el.textContent.includes('private-fixture')));
});

test('a preparation failure cannot open an agent or claim completion',async()=>{
  const view=fixture();await Promise.resolve();
  assert.equal(view.show({stage:'error',label:'Не удалось завершить подготовку',error:'held old process'}),false);
  assert.equal(view.elements.error.textContent,'held old process');assert.equal(view.elements.progress.hidden,true);
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,0);
});

test('subscription installs with progress, sends no API credentials, and opens only after completion',async()=>{
  const view=fixture();await Promise.resolve();view.show({stage:'create'});
  for(const [id,value] of Object.entries({name:'Вера',owner:'Марк',provider:'chatgpt',model:'chosen-model',key:'unused-api-key',base:'https://unused.invalid'})){
    view.context.document.getElementById(id).value=value;
  }
  view.elements.provider.onchange();
  assert.equal(view.elements.key.required,false);assert.equal(view.elements.key.disabled,true);
  assert.equal(view.elements['api-key'].hidden,true);assert.equal(view.elements['subscription-note'].hidden,false);
  let finish;
  const invoke=view.context.window.__HELENE__.invoke;
  view.context.window.__HELENE__.invoke=(command,args)=>command==='first_create'
    ? (view.calls.push({command,args}),new Promise(resolve=>{finish=resolve}))
    : command==='first_status'?Promise.resolve({stage:'preparing_relay',label:'Устанавливаем praxis-relay',bytes:8,total_bytes:16}):invoke(command,args);
  const submitting=view.elements.form.onsubmit({preventDefault(){}});
  assert.equal(view.elements.form.hidden,true);assert.equal(view.elements.create.disabled,true);
  await view.context.creationProgress();
  assert.equal(view.elements.progress.value,8);assert.equal(view.elements.progress.max,16);
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,0);
  const created=view.calls.find(c=>c.command==='first_create');
  assert.equal(created.args.key,'');assert.equal(created.args.base_url,'');
  finish({created:true});await submitting;
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,1);
  assert.equal(view.elements.key.value,'');
});

test('failed subscription installation returns to the form without opening the app',async()=>{
  const view=fixture();await Promise.resolve();view.show({stage:'create'});
  view.elements.provider.value='chatgpt';view.elements.provider.onchange();
  view.context.window.__HELENE__.invoke=async command=>{if(command==='first_create')throw new Error('offline');};
  await view.elements.form.onsubmit({preventDefault(){}});
  assert.equal(view.elements.form.hidden,false);assert.equal(view.elements.create.disabled,false);
  assert.match(view.elements.error.textContent,/offline/);
  assert.equal(view.calls.filter(c=>c.command==='first_open').length,0);
});
