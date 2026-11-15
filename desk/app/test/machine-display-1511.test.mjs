import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown'),temporary=mkdtempSync(join(tmpdir(),'helene-machine-words-'));
try{
 const file=path=>JSON.stringify(resolve(desk,path).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),'export * as machine from '+file('ui-kit/machine-status.ts')+';\nexport * as language from '+file('lang/lang.ts')+';');
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.window={addEventListener(){}};globalThis.localStorage={getItem(){return null},setItem(){}};
 const {machine,language}=await import(pathToFileURL(join(temporary,'actual.mjs')).href),raw='Literal Русский 中文 **{port}** <script>inert</script>';
 const spec=JSON.parse(readFileSync(resolve(desk,'resources/machine-display.json'),'utf8'));
 const make=(key,values={})=>({schema:'helene.machine-display.v1',key,values});
 const recipes=Object.entries(spec).map(([key,fields])=>make(key,key==='join'?{parts:[make('body.bridge_name'),raw]}:Object.fromEntries(fields.map(field=>[field,raw]))));
 const before=JSON.stringify(recipes),byUI=new Map();
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const captions=recipes.map(recipe=>machine.machineWords(recipe));assert.ok(captions.every(Boolean));
  recipes.forEach((recipe,i)=>{
   const expected=recipe.key==='join'?language.t('machine.body.bridge_name')+'; '+raw:language.t('machine.'+recipe.key,recipe.values);
   assert.equal(captions[i],expected);assert.equal(machine.machineCaption({reason:raw,display_reason:recipe},'reason'),expected);
  });
  if(byUI.has(ui))assert.deepEqual(captions,byUI.get(ui));else byUI.set(ui,captions);
  assert.equal(JSON.stringify(recipes),before);assert.equal(machine.machineCaption({reason:raw},'reason'),raw);
 }
 assert.equal(new Set([...byUI.values()].map(JSON.stringify)).size,4);
 const cyclic=make('join',{parts:[]});cyclic.values.parts.push(cyclic);
 for(const invalid of [null,{},[],cyclic,{schema:'unknown',key:'body.initial',values:{}},make('toString'),
  make('body.initial',{extra:0}),make('body.connected',{port:NaN,session:1,kind:raw}),make('join',{parts:Array(33).fill(raw)}),
  make('body.connected',{'port,session,kind':raw}),make('body.ports',{first:{schema:'other',key:'body.initial',values:{}},last:1})]){
  assert.equal(machine.machineWords(invalid),'');assert.equal(machine.machineCaption({reason:raw,display_reason:invalid},'reason'),raw);
 }
 language.setLocale('en');assert.ok(machine.machineWords(make('why.unloaded',{modules:raw})).includes('unconfirmed'));
 assert.equal(machine.machineCaption({reason:language.t('machine.body.initial')},'reason'),language.t('machine.body.initial'));
 console.log(JSON.stringify({ok:true,codes:recipes.length,pairs:16,legacyLiteral:true,rawDiagnostics:true}));
}finally{rmSync(temporary,{recursive:true,force:true});}
