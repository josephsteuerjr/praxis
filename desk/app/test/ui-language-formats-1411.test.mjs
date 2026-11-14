import assert from 'node:assert/strict';
import {test} from 'node:test';
import './fixtures/ru-localization.mjs';
const {setLocale}=await import('../../lang/lang.ts');
const {foldWords}=await import('../../ui-kit/memory-fold.ts');
const {agendaWhen}=await import('../../ui-kit/window/agenda-time.ts');
test('memory quantities use the selected language while authored notes stay literal',()=>{
  const st={room:'fixture',hot:21,keep:250,offer_at:400,hard_at:500,offer:null,pending:null,receipt:null};
  try{for(const [language,noun] of [['en','messages'],['fr','messages'],['ru','сообщение'],['zh','条消息']]){
    setLocale(language);const words=foldWords(st);assert.equal(words.quantity,'21 '+noun);
    assert.ok(words.counter.includes(words.quantity));
    const original='AUTHORED русский 日本語 <b> original';assert.equal(foldWords({...st,operation:{state:'running',note:original}}).note,original);
    assert.equal(foldWords({...st,hot:1234}).quantity.startsWith((1234).toLocaleString(language)),true);
  }}finally{setLocale('ru');}
});
test('agenda display follows UI locale without changing the instant or authored deadlines',()=>{
  const instant='2026-11-14T18:20:00Z';
  try{for(const language of ['en','fr','zh','ru']){
    setLocale(language);assert.equal(agendaWhen(instant),new Date(instant).toLocaleString(language,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}));
    assert.equal(agendaWhen('после обеда 日本語'),'после обеда 日本語');assert.equal(agendaWhen('2026-99-99T99:99'),'2026-99-99T99:99');
  }}finally{setLocale('ru');}
});
