import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown');
const temporary=mkdtempSync(join(tmpdir(),'helene-product-notice-'));
try{
 const file=p=>JSON.stringify(resolve(desk,p).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),'export * as text from '+file('ui-kit/text.ts')+';\nexport * as language from '+file('lang/lang.ts')+';');
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});
 await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.window={addEventListener(){}};globalThis.localStorage={getItem(){return null},setItem(){}};
 const {text,language}=await import(pathToFileURL(join(temporary,'actual.mjs')).href);
 const catalogs=Object.fromEntries(['ru','en','fr','zh'].map(code=>[code,JSON.parse(readFileSync(resolve(desk,'lang/'+code+'.json'),'utf8'))]));
 const raw='Literal Русский 中文 **{agent}** </script>',fields={speech_failed:[],speech_unknown:[],turn_incomplete:[],files_pending:['count','run'],paused:['run'],turn_failed:['run'],turn_cut:['run'],run_in_doubt:['run'],run_stopped:['run'],silence:['agent'],silence_reason:['agent','reason'],closed:[],closed_file:[],closed_unspoken:[],tree_busy:['agent','pid'],birth_silent:['agent'],birth_failed:['tries'],resume_refused:['reason'],note_gave_up:['name','tries','path'],note_rejected:['name','reason']};
 const messages=Object.entries(fields).map(([kind,keys])=>({system:true,text:raw,display_notice:{schema:'helene.product-notice.v1',kind,values:Object.fromEntries(keys.map(key=>[key,raw]))}})),before=JSON.stringify(messages),byUI=new Map();
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const captions=messages.map(message=>text.systemNoticeText(message));
  messages.forEach((message,i)=>{
   assert.equal(captions[i],language.t('notice.'+message.display_notice.kind,message.display_notice.values));
   if(Object.keys(message.display_notice.values).length)assert.ok(captions[i].includes(raw));
  });
  if(byUI.has(ui))assert.deepEqual(captions,byUI.get(ui));else byUI.set(ui,captions);
  assert.equal(JSON.stringify(messages),before);
 }
 assert.equal(new Set([...byUI.values()].map(JSON.stringify)).size,4);
 const valid=messages.find(row=>row.display_notice.kind==='silence_reason');
 for(const bad of [null,{},raw,{system:true,text:catalogs.ru['notice.speech_failed']},{...valid,system:false},
  {...valid,display_notice:{...valid.display_notice,kind:'toString'}},
  {...valid,display_notice:{...valid.display_notice,values:{'agent,reason':raw}}},
  {...valid,display_notice:{...valid.display_notice,values:{agent:raw,reason:NaN}}},
  {...valid,display_notice:{...valid.display_notice,values:{agent:raw,reason:{}}}},
  {...valid,display_notice:{...valid.display_notice,extra:raw}}])assert.equal(text.systemNoticeText(bad),'');
 language.setLocale('en');const first=text.systemNoticeText(valid);language.setLocale('fr');assert.notEqual(text.systemNoticeText(valid),first);
 console.log(JSON.stringify({ok:true,codes:20,pairs:16,rawPreserved:true}));
}finally{rmSync(temporary,{recursive:true,force:true});}
