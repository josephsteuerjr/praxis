// The full MiniApp exposes a programme bridge, but downloads belong to its viewer.
import './fixtures/ru-localization.mjs';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../../ui-kit/window/paper-files.ts',import.meta.url),'utf8');
const body=source.slice(source.indexOf('async function save('),source.indexOf('export function mountWindowMedia'));
const factory=new Function('inTauri','electron','window','browserSave','fileDialog','access','saveName',stripTypeScriptTypes(body)+';return save;');
const media={path:'media/artifact',kind:'file',name:'Original_日本語.txt'},blob=new Blob(['original bytes']);
for(const scenario of [
 {name:'desktop legacy picker',bridge:{shell:'python'},window:{},target:'native'},
 {name:'desktop system picker',bridge:{shell:'python'},window:{showSaveFilePicker(){}},target:'browser'},
 {name:'MiniApp without system picker',bridge:{shell:'python',presentation:'miniapp'},window:{},target:'browser'},
 {name:'ordinary browser',bridge:undefined,window:{},target:'browser'},
]){
 const calls=[];
 const save=factory(!!scenario.bridge,scenario.bridge,scenario.window,
  async(m,b)=>{assert.equal(m,media);assert.equal(b,blob);calls.push('browser')},
  async(access,options)=>{assert.equal(access,'native-access');assert.equal(options.save,media.name);calls.push('native');return null},
  (local,m,b)=>{assert.equal(local,false);assert.equal(m,media);assert.equal(b,blob);return 'native-access'},m=>m.name);
 await save(false,media,blob);assert.deepEqual(calls,[scenario.target],scenario.name);
}
console.log('Media save target: desktop native/system picker and MiniApp/browser viewer ownership PASS');
