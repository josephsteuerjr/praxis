import './fixtures/ru-localization.mjs';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const language=await import(new URL('../../lang/lang.ts',import.meta.url));
const source=readFileSync(new URL('../../setup/ui/src/scenes/install.ts',import.meta.url),'utf8');
const from=source.indexOf('export function installCancelled('),to=source.indexOf('\nexport class InstallScene',from);
assert.ok(from>0&&to>from);
const cancelled=new Function('uiText',stripTypeScriptTypes(source.slice(from,to)).replace('export function','function')+';return installCancelled;')(language.t);
const native=readFileSync(new URL('../../setup/src/install.rs',import.meta.url),'utf8');
const match=/pub const CANCELLED: &str = ("[^"]*");/.exec(native);assert.ok(match);
for(const lang of ['ru','en','fr','zh']){
 language.setLocale(lang);
 assert.equal(cancelled(JSON.parse(match[1])),true,'native cancellation stays cancellation in '+lang);
 assert.equal(cancelled(language.t('ui.setup.ui.src.scenes.install.23a2a9bfcf')+' · fixture'),true);
 assert.equal(cancelled('PermissionError: fixture denied'),false);
 assert.equal(cancelled('INSTALL_RESULT_UNKNOWN'),false);
}
language.setLocale('ru');
console.log('Native and preview cancellation retain their outcome in every interface language');
