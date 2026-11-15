import './fixtures/ru-localization.mjs';
import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const {t,setLocale}=await import(new URL('../../lang/lang.ts',import.meta.url));
const contract=JSON.parse(readFileSync(new URL('../../ui-kit/contract.json',import.meta.url),'utf8'));
function load(file,returned,extra={}){const source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');return new Function('uiText',...Object.keys(extra),stripTypeScriptTypes(source).replace(/export /g,'')+';return '+returned+';')(t,...Object.values(extra));}
const machineSource=readFileSync(new URL('../../ui-kit/machine-status.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
const machineFields=JSON.parse(readFileSync(new URL('../../resources/machine-display.json',import.meta.url),'utf8'));
const {machineCaption}=new Function('fields','uiText',stripTypeScriptTypes(machineSource).replace(/export /g,'')+';return {machineCaption};')(machineFields,t);
const computer=load('computer.ts','{liveLine,TCC_ROWS,TCC_NOTE}',{contract,machineCaption,fmtTimeSec:value=>'AUTHORED timestamp '+value});
const permissions=load('remote-computer.ts','permissions');
const extension=load('extensionscard.ts','{adaptNote,when}',{locale:()=>current});
let current='ru';
const row={name:'AUTHORED расширение 日本語',version:'1.2.3',dir:'C:/AUTHORED_日本語',reason:'AUTHORED <script>inert</script>',api:'helene.ext/0'};
for(const lang of ['ru','en','fr','zh']){
 current=lang;setLocale(lang);
 assert.equal(computer.TCC_ROWS()[0][2],{ru:'Запись экрана и системного звука',en:'Screen & System Audio Recording',fr:'Enregistrement de l’écran et de l’audio système',zh:'屏幕与系统音频录制'}[lang]);
 assert.equal(computer.TCC_ROWS()[0][0],'screen_recording');
 assert.equal(computer.TCC_ROWS()[1][0],'accessibility');
 assert.ok(computer.TCC_NOTE().includes('Helene'));
 const unverified=computer.liveLine({enabled:true,connected:true,platform:'linux'},true);
 const unavailable=computer.liveLine({enabled:true,connected:true,platform:'linux',console:false,hints:['AUTHORED hint 日本語']},true);
 const known=computer.liveLine({enabled:true,connected:true,platform:'windows',port:6060,identity:{kind:'AUTHORED token',session_id:41}},true);
 assert.equal(unverified.ok,null);assert.equal(unavailable.ok,false);assert.equal(known.ok,true);
 assert.ok(unavailable.text.includes('AUTHORED hint 日本語'));assert.ok(known.text.includes('6060'));assert.ok(known.text.includes('41'));assert.ok(known.text.includes('AUTHORED token'));
 assert.equal(computer.liveLine({enabled:true,connected:false,reason:'AUTHORED error 日本語'},true).text,'AUTHORED error 日本語');
 const display_reason={schema:'helene.machine-display.v1',key:'body.remote_unconfirmed',values:{}};
 assert.equal(computer.liveLine({enabled:true,connected:false,reason:'RAW диагностическое',display_reason},true).text,t('machine.body.remote_unconfirmed'));
 const labels=permissions();assert.deepEqual(labels.map(row=>row[0]),['computer.read','computer.apps','computer.files','computer.process']);
 const note=extension.adaptNote(row,{api:'helene.ext/1',helene:'fixture'},undefined);
 for(const value of ['AUTHORED расширение 日本語','C:/AUTHORED_日本語','<script>inert</script>','helene.ext/0','helene.ext/1','extension.json'])assert.ok(note.includes(value));
 const expected=new Date(1710000000*1000).toLocaleString(lang,{hour:'2-digit',minute:'2-digit',day:'2-digit',month:'2-digit'});
 assert.equal(extension.when(1710000000),expected);
 if(lang!=='ru'){assert.ok(!labels.some(row=>row[1].includes('Смотреть')));assert.ok(!note.startsWith('Адаптируй'));assert.ok(!computer.TCC_ROWS()[0][2].includes('Запись'));}
}
setLocale('ru');
console.log('Computer snapshots, TCC permissions, remote scope IDs and extension notes stay correct across UI language changes');
