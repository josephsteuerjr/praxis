import assert from 'node:assert/strict';
import { forwardLaunchWhenReady } from '../../electron/src/launch.ts';
import { registerHooks } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
const hooks=registerHooks({resolve(specifier,context,next){
  try{return next(specifier,context);}catch(error){
    if(specifier.startsWith('.')) {
      const target=new URL(specifier+'.ts',context.parentURL);
      if(existsSync(target)) return next(target.href,context);
    }
    throw error;
  }
},load(url,context,next){
  if(url.endsWith('.json'))return {format:'module',shortCircuit:true,source:'export default '+readFileSync(new URL(url),'utf8')};
  return next(url,context);
}});
globalThis.window={DESK_CONFIG_OVERRIDE:{},addEventListener(){}};
const { liveLine }=await import('../src/computer.ts');

let hostCalls=0, panicCalls=0, readyCalls=0;
const never=()=>{readyCalls++; return new Promise(()=>{});};
const invoke=async()=>{hostCalls++; return 'host';};
assert.equal(await forwardLaunchWhenReady(['helene','--panic'],never,invoke,async()=>{panicCalls++; return 'stopped';}),'stopped');
assert.equal(readyCalls,0); assert.equal(hostCalls,0); assert.equal(panicCalls,1);
let ready;
const pending=new Promise(resolve=>{ready=resolve;});
const resume=forwardLaunchWhenReady(['helene','--resume'],()=>pending,invoke,async()=>{throw new Error('unexpected panic');});
await Promise.resolve(); assert.equal(hostCalls,0);
ready(); assert.equal(await resume,'host'); assert.equal(hostCalls,1);
await assert.rejects(forwardLaunchWhenReady(['helene','--resume'],async()=>{},async()=>{throw new Error('transfer busy');},async()=>{}),/transfer busy/);
for(const console of [undefined,null]) {
  const line=liveLine({enabled:true,available:true,connected:true,platform:'linux',console},true,true);
  assert.equal(line.ok,null); assert.match(line.text,/ещё не проверен/);
}
assert.equal(liveLine({enabled:true,connected:true,platform:'linux',console:false},true,true).ok,false);
assert.equal(liveLine({enabled:true,connected:true,platform:'linux',console:true},true,true).ok,true);
assert.equal(liveLine({enabled:true,connected:true,platform:'windows'},true,false).ok,true);
console.log('Linux panic during bootstrap, resume gate and readiness truth PASS');
hooks.deregister();
