import './fixtures/ru-localization.mjs';
import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../src/voicecard.ts',import.meta.url),'utf8');
const ownedSource=readFileSync(new URL('../../ui-kit/window/voice-owned.ts',import.meta.url),'utf8');
const voiceOwnedText=new Function('t',stripTypeScriptTypes(ownedSource.replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'')+';return voiceOwnedText;')(uiText);
const fragment=source.slice(source.indexOf('export function speechStatusText'),source.indexOf('\nfunction mb'));
const say=new Function('voiceOwnedText',stripTypeScriptTypes(fragment).replace('export function','function')+';return speechStatusText;')(voiceOwnedText);
const state={ready:true,enabled:true,worker_ready:false};
for(const [phase,text] of [['loading','загружает'],['warming_voice','без звука'],['paused','паузе'],['unknown','Не удалось проверить']]) {
  assert.ok(say({...state,worker_phase:phase}).includes(text));
  assert.ok(!say({...state,worker_phase:phase}).includes('Голос готов'));
}
assert.ok(say({...state,worker_phase:'error',worker_error:'неизвестный голос'}).includes('неизвестный голос'));
assert.ok(say({...state,worker_phase:'busy',device_actual:'cpu'}).includes('озвучивает ответ · CPU'));
assert.ok(say({...state,worker_ready:true,worker_phase:'ready'}).includes('прогреты'));
assert.ok(say({...state,worker_phase:'ready'}).includes('Ожидаю'));
assert.ok(say({...state,ready:false,why:'выключен'}).includes('выключен'));
console.log('Speech status: installed, loading, warming, ready, busy, paused, error and unknown stay distinct');
