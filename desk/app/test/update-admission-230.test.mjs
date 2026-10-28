// Real production admission helper; no shell service, network or app mutation.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../../ui-kit/window/update-admission.ts',import.meta.url),'utf8');
const code=stripTypeScriptTypes(source).replace(/export /g,'');
const {requestAgentUpdate}=new Function(code+';return {requestAgentUpdate}')();
const calls=[];
const receipt={state:'admitted',job:{id:'one-persistent-job'},observer_may_close:true};
const invoke=async(command,args)=>{calls.push({command,args});return receipt};
assert.equal(await requestAgentUpdate(invoke,'https://fixture.invalid/rc.zip','a'.repeat(64),'preserve'),receipt);
assert.deepEqual(calls,[{command:'update_start',args:{url:'https://fixture.invalid/rc.zip',sha256:'a'.repeat(64),sourceMode:'preserve'}}]);
for(const value of [null,{state:'admitted'},{state:'installer_opened',job:{id:'old-wizard'}},{state:'admitted',job:{id:'unowned'},observer_may_close:false}]){
  let n=0;await assert.rejects(()=>requestAgentUpdate(async()=>{n++;return value},'x','','clean'),/не подтверждено/);assert.equal(n,1);
}
let n=0;await assert.rejects(()=>requestAgentUpdate(async()=>{n++;throw Error('lost reply')},'x','','preserve'),/lost reply/);assert.equal(n,1,'unknown admission never retries or falls back to old install');
const frame=readFileSync(new URL('../../ui-kit/window/views/settings-frame.ts',import.meta.url),'utf8');
assert.match(frame,/requestAgentUpdate\(shell, updUrl, updSha, sourceMode\)/);
assert.doesNotMatch(frame,/forceToggle|forceExtensions|let forceExt/);
assert.ok(frame.indexOf('requestAgentUpdate(shell')<frame.indexOf('>("update_download"'),'Python durable admission is before legacy window-owned download');
console.log('Update admission230: exact one-job command, owner receipt, unknown no replay/fallback, obsolete action absent PASS');
