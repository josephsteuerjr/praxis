import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown');
const temporary=mkdtempSync(join(tmpdir(),'helene-native-error-'));
try{
 const file=p=>JSON.stringify(resolve(desk,p).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),'export * as native from '+file('ui-kit/native-error.ts')+';\nexport * as language from '+file('lang/lang.ts')+';\nexport * as api from '+file('ui-kit/window/api.ts')+';');
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});
 await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.localStorage={getItem(){return null},setItem(){}};globalThis.location={search:'',origin:'http://fixture'};
 const raw='Literal Русский 中文 <script>{path}</script>';
 const error=Object.assign(new Error(raw),{native_error:{schema:'helene.native-error.v1',code:'window_assets',values:{path:raw}}});
 let calls=0;
 globalThis.window={addEventListener(){},__HELENE__:{shell:'python',platform:'fixture',async invoke(){calls++;throw error},win(){}}};
 const {native,language,api}=await import(pathToFileURL(join(temporary,'actual.mjs')).href);
 const before=JSON.stringify(error.native_error),byUI=new Map();
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const projected=native.nativeError(error);
  assert(projected instanceof language.UiError);assert(projected.message.includes(raw));assert.equal(error.message,raw);
  if(byUI.has(ui))assert.equal(projected.message,byUI.get(ui));else byUI.set(ui,projected.message);
  await assert.rejects(api.electron.invoke('fixture'),e=>e instanceof language.UiError&&e.message===projected.message);
  assert.equal(JSON.stringify(error.native_error),before);
 }
 assert.equal(calls,16);assert.equal(new Set(byUI.values()).size,4);
 for(const value of [new Error(raw),Object.assign(new Error(raw),{native_error:{schema:'unknown',code:'agent_missing',values:{}}}),
  {native_error:{schema:'helene.native-error.v1',code:'unknown',values:{}}},
  {native_error:{schema:'helene.native-error.v1',code:'window_assets',values:{path:{}}}},
  {native_error:{schema:'helene.native-error.v1',code:'agent_missing',values:{extra:raw}}}])assert.equal(native.nativeError(value),value);
 console.log(JSON.stringify({ok:true,pairs:16,nativeInvocations:calls,rawPreserved:true}));
}finally{rmSync(temporary,{recursive:true,force:true});}
