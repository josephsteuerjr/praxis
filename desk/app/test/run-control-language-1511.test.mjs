// Producer recipes are rendered by the client, independently of frame language.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown');
const temporary=mkdtempSync(join(tmpdir(),'helene-run-control-'));
try {
 const file=p=>JSON.stringify(resolve(desk,p).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),'export * as text from '+file('ui-kit/text.ts')+';\nexport * as language from '+file('lang/lang.ts')+';');
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});
 await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.window={addEventListener(){}};globalThis.localStorage={getItem(){return null},setItem(){}};
 const {text,language}=await import(pathToFileURL(join(temporary,'actual.mjs')).href);
 const payloads=['confirmed','pending','invalid_id','ambiguous_id'].map(kind=>({note:'RAW Русский 中文',display_note:{schema:'helene.run-control.v1',kind}}));
 const saved=JSON.stringify(payloads),byUI=new Map();
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const captions=payloads.map(text.runControlNote);
  payloads.forEach((value,i)=>assert.equal(captions[i],language.t('run.control.'+value.display_note.kind)));
  if(byUI.has(ui))assert.deepEqual(captions,byUI.get(ui));else byUI.set(ui,captions);
  assert.equal(JSON.stringify(payloads),saved);
 }
 assert.equal(new Set([...byUI.values()].map(JSON.stringify)).size,4);
 for(const value of [null,{},'raw',{note:'Исполнение хода прервано.'},{display_note:{schema:'other',kind:'confirmed'}},
  {display_note:{schema:'helene.run-control.v1',kind:'UNKNOWN'}},{display_note:[]}])assert.equal(text.runControlNote(value),'');
 language.setLocale('en');const first=text.runControlNote(payloads[0]);language.setLocale('fr');assert.notEqual(text.runControlNote(payloads[0]),first);
 console.log(JSON.stringify({ok:true,pairs:16,actual:['text','lang'],rawPreserved:true}));
} finally {rmSync(temporary,{recursive:true,force:true});}
