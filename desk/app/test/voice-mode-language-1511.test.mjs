import './fixtures/ru-localization.mjs';
import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const {t,setLocale}=await import(new URL('../../lang/lang.ts',import.meta.url));
function load(name,returned){const s=readFileSync(new URL('../../ui-kit/window/'+name,import.meta.url),'utf8');return new Function('t',stripTypeScriptTypes(s.replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'')+';return '+returned+';')(t);}
const {modeOwnedText,localizeMode}=load('mode-owned.ts','{modeOwnedText,localizeMode}');
const voiceOwnedText=load('voice-owned.ts','voiceOwnedText');
const input={name:'sandbox',title:'Песочница',text:'AUTHORED mode text 日本語',ladder_title:'Интерактивный',choices:[{name:'sandbox',title:'Песочница',text:'AUTHORED choice 日本語',sandbox:true,needs_admin:false}],private_field:'AUTHORED private 日本語'};
const before=JSON.stringify(input);
for(const lang of ['ru','en','fr','zh']){
 setLocale(lang);
 const projected=localizeMode(input);
 assert.equal(projected.title,t('mode.sandbox'));assert.equal(projected.choices[0].title,t('mode.sandbox'));
 assert.equal(projected.text,input.text);assert.equal(projected.choices[0].text,input.choices[0].text);
 assert.equal(projected.private_field,input.private_field);assert.equal(projected.name,'sandbox');
 assert.equal(modeOwnedText('AUTHORED имя 日本語'),'AUTHORED имя 日本語');
 assert.equal(voiceOwnedText('small — быстрее и легче'),t('voice.model.small.title'));
 assert.equal(voiceOwnedText('AUTHORED voice 日本語'),'AUTHORED voice 日本語');
 assert.equal(voiceOwnedText('голос агента выключен'),t('voice.reason.off'));
 assert.equal(JSON.stringify(input),before);
 const runtime=t('voice.recognition.runtime.missing',{size:'123'});
 assert.ok(runtime.includes('XTTS'));assert.ok(runtime.includes('123'));
 assert.ok(!runtime.includes('обе половины'));
 assert.ok(t('voice.recognition.cpu',{where:'AUTHORED path'}).includes('AUTHORED path'));
}
setLocale('ru');
console.log('Voice/mode owned captions follow UI locale while canonical names and authored values stay exact');
