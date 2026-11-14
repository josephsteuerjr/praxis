import assert from 'node:assert/strict';
import {test} from 'node:test';
import './fixtures/ru-localization.mjs';
const {setLocale,UiError}=await import('../../lang/lang.ts');
const {humanError}=await import('../../ui-kit/text.ts');
const {collectConnection}=await import('../../ui-kit/window/server-connection.ts');
test('actionable connection validation survives error rendering in every UI language',()=>{
  try{for(const lang of ['ru','en','fr','zh']){
    setLocale(lang);
    for(const draft of [{mode:'remote'},{mode:'remote',base:'invalid'},{mode:'remote',base:'https://example.test'}]){
      const out={marker:'original'};const message=collectConnection(out,draft);assert.ok(message);
      assert.deepEqual(humanError(new UiError(message)),{text:message,detail:''});assert.deepEqual(out,{marker:'original'});
    }
    // Untrusted runtime diagnostics still use the readable summary/detail split.
    for(const raw of ['TypeError: fixture failure','[WinError 5] Access is denied','Failed to fetch']){
      const result=humanError(new Error(raw));assert.notEqual(result.text,raw);assert.equal(result.detail,raw);
    }
  }}finally{setLocale('ru');}
});
