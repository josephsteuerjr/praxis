import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {test} from 'node:test';
import './fixtures/ru-localization.mjs';
const {setLocale,t}=await import('../../lang/lang.ts');

const read=file=>readFileSync(new URL(file,import.meta.url),'utf8');
const load=async source=>import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
// Exercise production declarations before the saved language is initialized.
setLocale('en');
const lookSource=read('../../ui-kit/window/look.ts');
const look=await load(lookSource.slice(lookSource.indexOf('interface Choice'),lookSource.indexOf('export interface Look')));
const frameSource=read('../../ui-kit/window/views/settings-frame.ts');
const settings=await load(frameSource.slice(frameSource.indexOf('export const RESTART_BLOCKS'),frameSource.indexOf('export type SettingsRestart')));
const textSource=read('../../ui-kit/text.ts');
const dates=await load(textSource.slice(textSource.indexOf('export function isToday'),textSource.indexOf('export function fmtDay')));

test('appearance and restart captions follow a language chosen after module import',()=>{
  const ids=JSON.stringify(look.PAPER_DAY.map(row=>[row.id,row.color]));
  for(const lang of ['ru','en','fr','zh']){
    setLocale(lang);
    assert.equal(look.PAPER_DAY[0].name,t('ui.ui.kit.window.look.6938395185'));
    assert.equal(look.FEELS[0].hint,t('ui.ui.kit.window.look.becc31db10'));
    assert.equal(JSON.stringify(look.PAPER_DAY.map(row=>[row.id,row.color])),ids);
    assert.deepEqual(settings.blocksNeedingRestart({}, {network:{mode:'changed'}}),[t('ui.ui.kit.window.views.settings.frame.47c853d7e7')]);
  }
  setLocale('ru');
});
test('today is a calendar fact in every UI language, not a translated label comparison',()=>{
  const now=new Date(),yesterday=new Date(now);yesterday.setDate(yesterday.getDate()-1);
  for(const lang of ['ru','en','fr','zh']){
    setLocale(lang);
    assert.equal(dates.isToday(now.toISOString()),true);
    assert.equal(dates.isToday(yesterday.toISOString()),false);
    assert.equal(dates.isToday('invalid'),false);
  }
  setLocale('ru');
});
