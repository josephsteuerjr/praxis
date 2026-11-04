import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {ready,close,setCoverageState} from './fixtures/desktop-coverage-data.mjs';
const url=await ready;
after(close);
const json=async(path,options)=>{const response=await fetch(url+path,options);assert.equal(response.status,200);return response.json();};
const native=cmd=>json('/__fixture/native',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});
test('native fixture preserves exact config_load and agents_list consumer envelopes',async()=>{
 const config=await native('config_load');assert.equal(config.config.agent.name,'Hélène');for(const key of ['path','tree','exe_dir','mtime_ns'])assert.ok(config[key]);
 const agents=await native('agents_list');assert.ok(Array.isArray(agents.agents));assert.equal(agents.current,'main');assert.ok(agents.agents.some(a=>a.id==='main'));
 assert.ok(Array.isArray(await native('components_status')));
});
test('empty fixture is deliberately empty, not a missing endpoint fallback',async()=>{
 setCoverageState({scene:'empty'});assert.deepEqual(await json('/api/runs'),[]);assert.deepEqual(await json('/api/md-tree'),[]);assert.deepEqual((await json('/api/agenda')).active,[]);setCoverageState();
});
test('file conflict is an actual HTTP409 and explicit config conflict is stale',async()=>{
 setCoverageState({scene:'settings-conflict',conflict:true});
 const response=await fetch(url+'/api/md',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:'soul/SOUL.md',text:'Synthetic',mtime_ns:'1'})});assert.equal(response.status,409);
 const config=await native('config_save');assert.equal(config.ok,false);assert.equal(config.code,'stale');setCoverageState();
});
test('remote edition fixture provides full agent config and devices, not blank error cards',async()=>{
 setCoverageState({scene:'remote'});assert.equal((await native('config_load')).config.mode,'remote');const config=await json('/api/agent-config');assert.equal(config.ok,true);assert.ok(config.config.model);const devices=await json('/api/computer/remote/state');assert.equal(devices.devices.length,2);setCoverageState();
});
test('declared error and delay branches are transport-visible',async()=>{
 setCoverageState({errorPaths:['/api/shadow']});assert.equal((await fetch(url+'/api/shadow')).status,503);setCoverageState({delayedPaths:['/api/shadow'],delayMs:60});const at=Date.now();await json('/api/shadow');assert.ok(Date.now()-at>=50);setCoverageState();
});
test('synthetic config.js and favicon never read an installed config or trigger500',async()=>{
 assert.equal((await fetch(url+'/config.js')).status,200);assert.equal((await fetch(url+'/favicon.ico')).status,204);
});
