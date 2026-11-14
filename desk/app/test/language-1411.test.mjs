import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';

let source=readFileSync(new URL('../../lang/lang.ts',import.meta.url),'utf8');
for(const lang of ['ru','en','fr','zh']) {
  const catalog=JSON.parse(readFileSync(new URL(`../../lang/${lang}.json`,import.meta.url),'utf8'));
  source=source.replace(`import ${lang} from "./${lang}.json";`,`const ${lang} = ${JSON.stringify(catalog)};`);
}
const stored=new Map();
Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{
  getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,String(value))}});
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{languages:['de-DE','fr-FR']}});
globalThis.document={documentElement:{lang:''}};
const lang=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
assert.equal(lang.locale(),'fr');
for(const selected of ['ru','en','fr','zh']) {
  lang.saveLocale(selected);assert.equal(lang.locale(),selected);
  assert.equal(document.documentElement.lang,selected==='zh'?'zh-Hans':selected);
  assert.ok(lang.t('language.frame'));
  lang.initializeLocale('ru');assert.equal(lang.locale(),selected,'saved client choice survives an older injected bootstrap');
}
lang.saveLocale('');assert.equal(lang.locale(),'fr');
navigator.languages=['zh-Hant','de-DE'];assert.equal(lang.locale(),'en');
navigator.languages=['zh-CN'];assert.equal(lang.locale(),'zh');
assert.equal(lang.fromTag('rubbish'),'');assert.equal(lang.fromTag('english'),'');
assert.throws(()=>lang.setLocale('de'));
assert.throws(()=>lang.t('unknown.translation.key'));
assert.equal(stored.has('agent.frame_language'),false);
assert.equal(stored.has('agent.living_prose'),false);
console.log('Interface language: persisted choice, independent frame settings, script policy and unknown-key failure PASS');
