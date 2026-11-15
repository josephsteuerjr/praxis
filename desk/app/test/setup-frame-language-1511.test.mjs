import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const url=code=>'data:text/javascript,'+encodeURIComponent(code);
const catalogs=Object.fromEntries(['ru','en','fr','zh'].map(lang=>[lang,JSON.parse(readFileSync(new URL('../../lang/'+lang+'.json',import.meta.url),'utf8'))]));
globalThis.window={__TAURI_INTERNALS__:{}};
globalThis.location=new URL('http://fixture/');
globalThis.localStorage={getItem:()=> 'fr',setItem(){}};
function node(tag){
 const value={tag,children:[],listeners:{},attrs:{},value:'',textContent:'',classList:{add(){},toggle(){}},
  append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
  setAttribute(key,value){this.attrs[key]=String(value);},getAttribute(key){return this.attrs[key];},
  addEventListener(event,callback){(this.listeners[event]??=[]).push(callback);},
  querySelector(selector){for(const child of this.children){if(child.tag===selector)return child;const found=child.querySelector?.(selector);if(found)return found;}return null;},
  focus(){}};
 return value;
}
globalThis.document={documentElement:{lang:'fr'},createElement:node};
let langSource=readFileSync(new URL('../../lang/lang.ts',import.meta.url),'utf8');
langSource=langSource.replace(/import (\w+) from "\.\/(\w+)\.json";/g,(_,name,lang)=>'const '+name+'='+JSON.stringify(catalogs[lang])+';');
const languageURL=url(stripTypeScriptTypes(langSource));
const language=await import(languageURL);
const stock={};
for(const [lang,file] of [['ru','SOUL.md'],['en','SOUL.en.md'],['fr','SOUL.fr.md'],['zh','SOUL.zh.md']])
 stock[lang]=readFileSync(new URL('../../resources/'+file,import.meta.url),'utf8');
let setupSource=stripTypeScriptTypes(readFileSync(new URL('../../setup/ui/src/setup.ts',import.meta.url),'utf8'));
setupSource=setupSource.replace('"../../../lang/lang"',JSON.stringify(languageURL));
for(const [file,lang] of [['SOUL.md','ru'],['SOUL.en.md','en'],['SOUL.fr.md','fr'],['SOUL.zh.md','zh']])
 setupSource=setupSource.replace('"../../../resources/'+file+'?raw"',JSON.stringify(url('export default '+JSON.stringify(stock[lang])+';')));
setupSource=setupSource.replace('"@tauri-apps/api/core"',JSON.stringify(url('export const invoke=(command,args)=>globalThis.NATIVE_INVOKE(command,args);')))
 .replace('"@tauri-apps/api/event"',JSON.stringify(url('export const listen=async()=>()=>{};')));
const setupURL=url(setupSource),module=await import(setupURL),{setup,constitutionFor}=module;
const baseURL=url('export class FormScene {constructor(root){this.root=root;}mount(...nodes){this.root.replaceChildren(...nodes);}}');
const formURL=url(stripTypeScriptTypes(readFileSync(new URL('../../setup/ui/src/scenes/form.ts',import.meta.url),'utf8')));
async function scene(file){
 let source=stripTypeScriptTypes(readFileSync(new URL('../../setup/ui/src/scenes/'+file+'.ts',import.meta.url),'utf8'));
 source=source.replace('"../../../../lang/lang"',JSON.stringify(languageURL))
  .replace('"./base"',JSON.stringify(baseURL)).replace('"./form"',JSON.stringify(formURL))
  .replace('"../setup"',JSON.stringify(setupURL));
 return import(url(source));
}
const {NameScene}=await scene('name'),{ConstitutionScene}=await scene('constitution');
const raw='AUTHORED $& 日本語 <script>inert</script>';

test('legacy setup keeps all16 interface/frame pairs independent and forwards exact choices',async()=>{
 assert.equal(setup.living_prose,true);assert.equal(setup.lang,'fr');
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  language.setLocale(ui);setup.frame_language=frame;setup.agent=raw;setup.owner=raw;
  const page=new NameScene(node('section'));page.beforeEnter();
  assert.equal(page.frameSelect.value,frame);
  page.frameSelect.value=frame;page.frameSelect.onchange();
  assert.equal(language.locale(),ui);assert.equal(setup.lang,'fr');
  const expected=stock[frame].replace(/\{\{agent\}\}/g,()=>raw).replace(/\{\{owner\}\}/g,()=>raw);
  assert.equal(constitutionFor(raw,raw),expected);
  setup.constitution=expected;setup.living_prose=false;
  let request;
  globalThis.NATIVE_INVOKE=async(command,args)=>{assert.equal(command,'install');request=structuredClone(args);return {dir:'fixture',exe:'fixture',service:'skipped',steps:[]};};
  await module.runInstall(()=>{});
  assert.equal(request.setup.frame_language,frame);assert.equal(request.setup.living_prose,false);
  assert.equal(request.setup.lang,'fr');assert.equal(request.setup.constitution,expected);
 }
});

test('existing and edited constitutions survive reentry and frame changes; untouched stock follows frame',()=>{
 setup.agent=raw;setup.owner=raw;setup.frame_language='en';setup.constitution='';setup.accepted=false;
 const fresh=new ConstitutionScene(node('section'));fresh.beforeEnter();
 assert.equal(fresh.editor.value,constitutionFor(raw,raw,'en'));
 setup.frame_language='zh';fresh.beforeEnter();
 assert.equal(fresh.editor.value,constitutionFor(raw,raw,'zh'));
 fresh.editor.value=raw;fresh.editor.listeners.input[0]();
 setup.frame_language='fr';fresh.beforeEnter();assert.equal(fresh.editor.value,raw);
 assert.equal(setup.constitution,raw);
 const restored=new ConstitutionScene(node('section'));restored.beforeEnter();
 assert.equal(restored.editor.value,raw);assert.equal(setup.constitution,raw);
});
