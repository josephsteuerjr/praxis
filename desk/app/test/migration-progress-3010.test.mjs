import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

test('migration counters show measured work, not an idle zero on every phase',()=>{
  const html=readFileSync(new URL('../../resources/python-migration.html',import.meta.url),'utf8');
  const fn=html.match(/^function progress\(v\)\{[^\n]+\}$/m)?.[0];
  assert.ok(fn);
  const elements=Object.fromEntries(['work','status','detail','progress'].map(id=>[id,{
    textContent:'',value:null,removeAttribute(name){delete this[name];}
  }]));
  const context=vm.createContext({$:id=>elements[id],labels:{}});
  vm.runInContext(fn,context);
  context.progress({stage:'uninstalling_old',files:0,total_files:null,bytes:0});
  assert.equal(elements.detail.textContent,'');
  assert.equal(elements.progress.value,undefined);
  context.progress({stage:'removing_old',files:0,total_files:27});
  assert.equal(elements.detail.textContent,'0 / 27 файлов');
  assert.equal(elements.progress.value,0);assert.equal(elements.progress.max,27);
  context.progress({stage:'removing_old',files:8,file:'old.exe'});
  assert.equal(elements.detail.textContent,'old.exe · 8 файлов');
  context.progress({stage:'preparing_relay',files:0,bytes:1048576,total_bytes:2097152});
  assert.equal(elements.detail.textContent,'1.0 МиБ');assert.equal(elements.progress.value,1048576);
  context.progress({stage:'connecting_data',files:0,total_files:null,bytes:0});
  assert.equal(elements.detail.textContent,'');assert.equal(elements.progress.value,undefined);
});

function observer(invoke){
  const html=readFileSync(new URL('../../resources/python-migration.html',import.meta.url),'utf8');
  const source=html.slice(html.indexOf('async function observeMigration()'),html.indexOf("window.addEventListener('helene:migration-progress'"));
  const elements={},timers=[];
  const context=vm.createContext({$:id=>elements[id]??={hidden:true,textContent:''},invoke,labels:{},
    progress(){},step(){},showError(error){(elements['error-box']??={}).hidden=false;elements.error={textContent:String(error)};},
    setTimeout(fn,delay){timers.push({fn,delay});}});
  vm.runInContext("let observedJob='admitted';"+source,context);
  return {context,elements,timers,current:()=>vm.runInContext('observedJob',context)};
}

test('an observer read failure keeps following the same job and never claims the worker stopped',async()=>{
  let reads=0;
  const view=observer(async(command,args)=>{
    assert.equal(command,'migration_status');assert.equal(args.id,'admitted');
    if(reads++===0)throw Error('old progress pointer');
    return {id:'admitted',state:'running',phase:'preparing',progress:null};
  });
  await view.context.observeMigration();
  assert.equal(view.current(),'admitted');assert.equal(view.elements.finish.hidden,true);
  assert.equal(view.elements.status.textContent,'Восстанавливаем наблюдение');
  assert.equal(view.timers.length,1);
  await view.timers.shift().fn();
  assert.equal(view.current(),'admitted');assert.equal(view.elements['error-box'].hidden,true);
  assert.equal(view.elements.status.textContent,'Установка продолжается');
  assert.equal(view.timers.length,1);
});

test('only actual durable failure or completion ends migration observation',async()=>{
  for(const state of ['attention','cancelled','completed']){
    const view=observer(async()=>({id:'admitted',state,error:state==='attention'?'actual worker error':'',
      diagnostic:state==='attention'?'actual-error.log':'',result:{}}));
    await view.context.observeMigration();
    assert.equal(view.current(),'');assert.equal(view.timers.length,0);
    assert.equal(view.elements.finish.hidden,false);
    assert.equal(view.elements['finish-title'].textContent,
      state==='attention'?'Установка требует внимания':state==='cancelled'?'Установка отменена':'Всё готово');
    if(state==='attention')assert.match(view.elements.error.textContent,/actual worker error\nactual-error.log/);
  }
});
