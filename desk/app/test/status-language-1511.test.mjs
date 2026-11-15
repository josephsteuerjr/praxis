import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown'),temporary=mkdtempSync(join(tmpdir(),'helene-state-words-'));
try{
 const file=path=>JSON.stringify(resolve(desk,path).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),'export * as status from '+file('ui-kit/status.ts')+';\nexport * as language from '+file('lang/lang.ts')+';');
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.window={addEventListener(){}};globalThis.localStorage={getItem(){return null},setItem(){}};
 const {status,language}=await import(pathToFileURL(join(temporary,'actual.mjs')).href),raw='Literal Русский 中文 **{count}** <script>inert</script>';
 const catalogs=Object.fromEntries(['ru','en','fr','zh'].map(code=>[code,JSON.parse(readFileSync(resolve(desk,'lang/'+code+'.json'),'utf8'))]));
 const recipes=Object.keys(catalogs.en).filter(key=>key.startsWith('status.')&&key!=='status.reset_time').map(key=>{
  const kind=key.slice(7),values=kind.startsWith('quota_')?{until:1710000000}:kind==='wake_wait'?{at:1710000000}:
    Object.fromEntries([...catalogs.en[key].matchAll(/\{(\w+)\}/g)].map(match=>[match[1],12345]));
  return {schema:'helene.agent-state.v1',kind,values};
 });assert.equal(recipes.length,34);
 const before=JSON.stringify(recipes),byUI=new Map();
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const captions=recipes.map(recipe=>status.statusWords(recipe));assert.ok(captions.every(Boolean));
  recipes.forEach((recipe,i)=>{
   const key='status.'+recipe.kind;
   if(recipe.kind.startsWith('quota_')||recipe.kind==='wake_wait'){
    const clock=new Date(1710000000*1000).toLocaleTimeString(language.locale(),{hour:'2-digit',minute:'2-digit'});
    assert.ok(captions[i].includes(clock));
   }else{
    const formatted=Object.fromEntries(Object.entries(recipe.values).map(([key,value])=>[key,value.toLocaleString(language.locale(),{maximumFractionDigits:0})]));
    assert.equal(captions[i],language.t(key,formatted));
   }
   const projected=recipe.kind.startsWith('action.')?status.stateActionLabel({label:raw,display_label:recipe}):recipe.kind.startsWith('alarm.')?status.stateAlarmText({text:raw,display_text:recipe}):status.agentStatePhrase({phrase:raw,display_phrase:recipe});
   assert.equal(projected,captions[i]);
  });
  if(byUI.has(ui))assert.deepEqual(captions,byUI.get(ui));else byUI.set(ui,captions);
  assert.equal(JSON.stringify(recipes),before);
  assert.equal(status.agentStatePhrase({phrase:raw}),raw);assert.equal(status.stateActionLabel({label:raw}),raw);assert.equal(status.stateAlarmText({text:raw}),raw);
 }
 assert.equal(new Set([...byUI.values()].map(JSON.stringify)).size,4);
 for(const invalid of [null,{},[],{schema:'unknown',kind:'online',values:{}},{schema:'helene.agent-state.v1',kind:'toString',values:{}},
  {schema:'helene.agent-state.v1',kind:'online',values:{extra:0}},{schema:'helene.agent-state.v1',kind:'quota_fallback',values:{until:NaN}},
  {schema:'helene.agent-state.v1',kind:'wake_wait',values:{at:1e30}},{schema:'helene.agent-state.v1',kind:'alarm.debt',values:{'minutes,count':1}}])assert.equal(status.statusWords(invalid),'');
 assert.equal(status.stateActionLabel({label:raw,display_label:{schema:'helene.agent-state.v1',kind:'online',values:{}}}),raw);
 assert.equal(status.agentStatePhrase({phrase:raw,display_phrase:{schema:'helene.agent-state.v1',kind:'action.restart',values:{}}}),raw);
 language.setLocale('en');assert.ok(status.statusWords({schema:'helene.agent-state.v1',kind:'quota_fallback',values:{until:0}}).includes('configured'));
 assert.equal(status.agentStatePhrase({phrase:catalogs.ru['status.online']}),catalogs.ru['status.online']);
 console.log(JSON.stringify({ok:true,codes:34,pairs:16,legacyLiteral:true,configuredNotExecution:true}));
}finally{rmSync(temporary,{recursive:true,force:true});}
