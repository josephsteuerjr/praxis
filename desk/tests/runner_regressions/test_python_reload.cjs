'use strict';
// Offline regression against the actual Application.bridge return expression.
// No window, server, backend or owner data is opened.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const python = process.env.RELOAD_PYTHON || path.resolve(root, '../runtime/python.exe');
const source = process.env.RELOAD_SOURCE || path.join(root, 'python_app.py');
const extract = String.raw`
import ast,json,sys
from types import SimpleNamespace
from pathlib import Path
tree=ast.parse(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Application')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='bridge')
ret=next(n for n in method.body if isinstance(n,ast.Return))
print(eval(compile(ast.Expression(ret.value),sys.argv[1],'eval'),dict(json=json,sys=sys,self=SimpleNamespace(channel={},token='test-token'),saved={},seen=1)))
`;
const generated = spawnSync(python, ['-c', extract, source], {encoding:'utf8'});
assert.equal(generated.status, 0, generated.stderr);
const script = generated.stdout;
function harness({defer=false,failure=null}={}) {
  const listeners = new Map(), requests = [], writes = new Map();
  let reloads=0, release, errors=[];
  let failMode=failure;
  class Storage {
    setItem(k,v){writes.set(String(k),String(v));}
    removeItem(k){writes.delete(String(k));}
  }
  const store = new Storage();
  const window = {
    localStorage:store,
    location:{reload(){reloads++;}},
    addEventListener(type, fn, capture){listeners.set(type,{fn,capture});},
    dispatchEvent(){}
  };
  const gate = new Promise(resolve=>{release=resolve;});
  const context = {
    window,Storage,console:{error(e){errors.push(String(e));}},CustomEvent:class {},setTimeout(){},
    fetch: async (url,options)=>{
      if(url.endsWith('/events'))return new Promise(()=>{});
      const body=JSON.parse(options.body);requests.push(body);
      if(defer)await gate;
      if(failMode==='network')throw new Error('offline');
      return {ok:true,json:async()=>({result:{ok:failMode!=='rejected',error:'save refused',seen:1,values:{}}})};
    }
  };
  vm.runInNewContext(script,context);
  function key(fields) {
    const e={key:'',code:'',altKey:false,shiftKey:false,ctrlKey:false,metaKey:false,isComposing:false,repeat:false,prevented:false,preventDefault(){this.prevented=true;},...fields};
    listeners.get('keydown')?.fn(e);return e;
  }
  return {window,key,store,writes,requests,release,listeners,errors,recover(){failMode=null;}, get reloads(){return reloads;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  let count=0;
  for(const shortcut of [
    {key:'F5'}, {key:'r',code:'KeyR',ctrlKey:true},
    {key:'r',code:'KeyR',metaKey:true}, {key:'R',ctrlKey:true},
    {key:'к',code:'KeyR',ctrlKey:true}
  ]) {
    const h=harness();assert.equal(h.listeners.get('keydown')?.capture,true);
    assert.equal(h.key(shortcut).prevented,true);await flush();
    assert.equal(h.reloads,1);assert.deepEqual(h.requests,[]);count++;
  }
  for(const shortcut of [
    {key:'r'}, {key:'F5',altKey:true}, {key:'F5',shiftKey:true},
    {key:'F5',ctrlKey:true}, {key:'r',ctrlKey:true,shiftKey:true},
    {key:'r',ctrlKey:true,altKey:true}, {key:'r',ctrlKey:true,isComposing:true},
    {key:'Enter'}, {key:'F12'}, {key:'b',ctrlKey:true}
  ]) {
    const h=harness();assert.equal(h.key(shortcut).prevented,false);
    await flush();assert.equal(h.reloads,0);count++;
  }
  {
    const h=harness();assert.equal(h.key({key:'F5',repeat:true}).prevented,true);
    await flush();assert.equal(h.reloads,0);
    h.key({key:'F5'});h.key({key:'r',ctrlKey:true});await flush();
    assert.equal(h.reloads,1);count++;
  }
  {
    const h=harness({defer:true});
    h.store.setItem('frame.draft.chat-1','unsent owner message');
    h.key({key:'F5'});await flush();assert.equal(h.reloads,0);
    // A later input while persistence is pending must also drain before reload.
    h.store.setItem('frame.draft.chat-1','newer unsent owner message');
    h.release();await flush();
    assert.equal(h.reloads,1);
    assert.equal(h.writes.get('frame.draft.chat-1'),'newer unsent owner message');
    assert.deepEqual(h.requests.map(x=>x.command),['window_preferences','window_preferences']);
    assert.equal(h.requests[1].args.value,'newer unsent owner message');count++;
  }
  for(const failure of ['network','rejected']) {
    const h=harness({failure});
    h.store.setItem('frame.draft.chat-1','new owner draft');
    h.store.setItem('frame.draft.chat-2','another owner draft');
    h.key({key:'F5'});await flush();
    assert.equal(h.reloads,0);
    assert.equal(h.writes.get('frame.draft.chat-1'),'new owner draft');
    assert(h.errors.some(e=>e.includes('Reload cancelled')));
    // A successful retry for this key clears its failure, not other keys.
    h.recover();h.store.setItem('frame.draft.chat-1','new owner draft');
    h.key({key:'r',ctrlKey:true});await flush();assert.equal(h.reloads,0);
    h.store.setItem('frame.draft.chat-2','another owner draft');
    h.key({key:'F5'});await flush();assert.equal(h.reloads,1);count++;
  }
  {
    const h=harness({defer:true});
    assert.equal(h.window.__HELENE__.reloadCapability,'safe-renderer-v2');
    h.store.setItem('draft.native','unsent');
    h.window.__HELENE__.requestReload();h.key({key:'F5'});
    await flush();assert.equal(h.reloads,0);
    h.release();await flush();assert.equal(h.reloads,1);count++;
  }
  {
    const h=harness({failure:'network'});
    h.store.setItem('draft.native','unsent');
    h.window.__HELENE__.requestReload();await flush();assert.equal(h.reloads,0);
    assert(h.errors.some(e=>e.includes('Reload cancelled')));count++;
  }
  assert(!/webview\.start\([^\n]*debug\s*=\s*True/.test(fs.readFileSync(source,'utf8')));
  console.log(`PASS ${count} bootstrap reload cases; no native keyboard/window/backend invoked`);
})().catch(error=>{console.error(error);process.exitCode=1;});
