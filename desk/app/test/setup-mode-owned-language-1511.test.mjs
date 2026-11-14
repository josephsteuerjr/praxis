import './fixtures/ru-localization.mjs';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const {t,setLocale,locale}=await import(new URL('../../lang/lang.ts',import.meta.url));
const source=readFileSync(new URL('../../ui-kit/window/mode-owned.ts',import.meta.url),'utf8');
const {localizeMode}=new Function('t',stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'')+';return {localizeMode};')(t);
const scope={key:'computer.files',title:'Файлы',text:'Читать, писать и пересылать файлы по абсолютному пути — любые, что доступны твоей учётке, мимо ограды.',authored:'AUTHORED 日本語 <script>'};
const state={name:'sandbox',title:'Песочница',text:'AUTHORED mode',choices:[],service:{name:'AUTHORED service',title:'Работать в фоне',text:'Запущенные агенты продолжат работу после закрытия Элен.',toggles:[{key:'service.session0',title:'Разрешить агенту нулевую сессию',warning:'AUTHORED warning'}]},computer_option:{title:'Управление компьютером',scopes:[scope]},raw:scope.text};
const before=JSON.stringify(state);
const found=readFileSync(new URL('../../setup/ui/src/scenes/found.ts',import.meta.url),'utf8');
const dateSource=found.slice(found.indexOf('export function humanDay('),found.indexOf('/** Что это за находка'));
const humanDay=new Function('locale',stripTypeScriptTypes(dateSource).replace('export function','function')+';return humanDay;')(locale);
for(const lang of ['ru','en','fr','zh']){
 setLocale(lang);const view=localizeMode(state);
 assert.equal(view.computer_option.scopes[0].title,t('ui.ui.kit.window.main.9088ca9c0d'));
 assert.equal(view.computer_option.scopes[0].authored,scope.authored);
 assert.equal(view.service.name,state.service.name);
 assert.equal(view.service.toggles[0].key,'service.session0');
 assert.equal(view.service.toggles[0].warning,'AUTHORED warning');
 assert.equal(view.raw,scope.text);
 if(lang!=='ru'){assert.ok(!/[А-Яа-яЁё]/.test(view.service.text));assert.ok(!/[А-Яа-яЁё]/.test(view.computer_option.scopes[0].text));}
 assert.equal(JSON.stringify(state),before);
 const year=new Date().getFullYear();const expected=new Intl.DateTimeFormat(lang,{day:'numeric',month:'long',timeZone:'UTC'}).format(new Date(Date.UTC(year,8,27,12)));
 assert.equal(humanDay(year+'-09-27'),expected);
 assert.equal(humanDay('2026-02-30'),'');assert.equal(humanDay('AUTHORED date'),'');assert.equal(humanDay('2026-13-01'),'');
}
setLocale('ru');console.log('Mode metadata projection preserves raw evidence and authored fields; installer dates validate and follow UI locale');
