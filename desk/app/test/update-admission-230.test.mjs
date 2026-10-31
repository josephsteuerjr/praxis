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
calls.length=0;
await requestAgentUpdate(invoke,'https://fixture.invalid/legacy.zip','');
assert.deepEqual(calls,[{command:'update_start',args:{url:'https://fixture.invalid/legacy.zip',sha256:''}}],
  'unknown programme never silently receives preserve; backend derives eligible mode from its exact baseline');
for(const value of [null,{state:'admitted'},{state:'installer_opened',job:{id:'old-wizard'}},{state:'admitted',job:{id:'unowned'},observer_may_close:false}]){
  let n=0;await assert.rejects(()=>requestAgentUpdate(async()=>{n++;return value},'x','','clean'),/не подтверждено/);assert.equal(n,1);
}
let n=0;await assert.rejects(()=>requestAgentUpdate(async()=>{n++;throw Error('lost reply')},'x','','preserve'),/lost reply/);assert.equal(n,1,'unknown admission never retries or falls back to old install');
const frame=readFileSync(new URL('../../ui-kit/window/views/settings-frame.ts',import.meta.url),'utf8');
assert.match(frame,/requestAgentUpdate\(shell, updUrl, updSha, sourceMode\)/);
assert.doesNotMatch(frame,/forceToggle|forceExtensions|let forceExt/);
assert.ok(frame.indexOf('requestAgentUpdate(shell')<frame.indexOf('>("update_download"'),'Python durable admission is before legacy window-owned download');
console.log('Update admission230: exact one-job command, owner receipt, unknown no replay/fallback, obsolete action absent PASS');

// Execute the production Settings poller, including a prior request that replies
// after the new admission. Timers are observed, never run against real services.
const pollerBegin=frame.indexOf('  // Reconnecting the old UI reads the same operation,');
const pollerEnd=frame.indexOf('\n  const updateWork=',pollerBegin);
assert.ok(pollerBegin>=0 && pollerEnd>pollerBegin,'existing Settings poller and its actual observation state are available');
const poller=stripTypeScriptTypes(frame.slice(pollerBegin,pollerEnd));
const makeObserver=new Function('shell',`
 const pythonUpdate=true,activityScope=()=> 'private-selected',settingsScope='private-selected';
 const updOut={isConnected:true,textContent:'new admission'},cancelUpdate={},dlBtn={},timers=[];
 const setTimeout=(fn,ms)=>timers.push({fn,ms}),renderSettingsWork=()=>{},humanError=e=>({text:e.message});
 let progressUpdate;
 ${poller}
 return {poll:restoreUpdateProgress,observe:id=>observedUpdateId=id,
   submit:()=>beginUpdateObservation(),updOut,timers,dlBtn};
`);
let reply;
const stale=makeObserver(()=>new Promise(resolve=>{reply=resolve}));
const pending=stale.poll();
stale.observe('accepted-restore'); // Admission finished while old request waited.
reply({id:'previous-completed',stage:'completed',label:'Old completed'});
await pending;
assert.equal(stale.updOut.textContent,'new admission','old terminal reply never labels the new operation');
assert.equal(stale.timers.length,1,'mismatched terminal reply cannot stop observation');

for(const bound of ['', 'accepted-restore']){
 const observer=makeObserver(async()=>({id:'accepted-restore',stage:'requested',label:'Задание принято'}));
 observer.observe(bound);await observer.poll();
 assert.equal(observer.updOut.textContent,'Задание принято');
 assert.equal(observer.timers.length,1,'accepted and lost-reply/rejoined windows keep polling pending current job');
 assert.equal(observer.dlBtn.disabled,true);

 const replies=[],overlap=makeObserver(()=>new Promise(resolve=>replies.push(resolve)));
 overlap.observe(bound);
 const oldRead=overlap.poll(),newRead=overlap.poll();
 replies[1]({id:'accepted-restore',stage:'requested',label:'Current queued Restore'});await newRead;
 replies[0]({id:'previous-completed',stage:'completed',label:'Previous completed Restore'});await oldRead;
 assert.equal(overlap.updOut.textContent,'Current queued Restore','older reply cannot overwrite a newer applied read, including unbound lost reply/rejoin');
 assert.equal(overlap.timers.length,1,'only the newer active response owns its next poll');
}
const earlier=[],generation=makeObserver(()=>new Promise(resolve=>earlier.push(resolve)));
const beforeSubmission=generation.poll();generation.submit();
earlier[0]({id:'previous-completed',stage:'completed',label:'Before new submission'});await beforeSubmission;
assert.equal(generation.updOut.textContent,'new admission','new submission invalidates already issued reads before admission reply');
assert.equal(generation.timers.length,0,'invalidated read does not create a second schedule');
const completed=makeObserver(async()=>({id:'accepted-restore',stage:'completed',label:'Current completed'}));
completed.observe('accepted-restore');await completed.poll();
assert.equal(completed.updOut.textContent,'Current completed');assert.equal(completed.timers.length,0);
const finishes=[],terminal=makeObserver(()=>new Promise(resolve=>finishes.push(resolve)));
terminal.observe('accepted-restore');
const earlierActive=terminal.poll(),laterTerminal=terminal.poll();
finishes[1]({id:'accepted-restore',stage:'completed',label:'Matching current completed'});await laterTerminal;
finishes[0]({id:'accepted-restore',stage:'requested',label:'Earlier current queued'});await earlierActive;
assert.equal(terminal.updOut.textContent,'Matching current completed','a matching terminal receipt is not rolled back by an older active response');
assert.equal(terminal.timers.length,0,'older active response does not restart a completed operation poll');
const errors=[],orderedError=makeObserver(()=>new Promise((resolve,reject)=>errors.push({resolve,reject})));
const oldError=orderedError.poll(),newSuccess=orderedError.poll();
errors[1].resolve({id:'accepted-restore',stage:'requested',label:'Current queued Restore'});await newSuccess;
errors[0].reject(Error('old read failed'));await oldError;
assert.equal(orderedError.updOut.textContent,'Current queued Restore','an older request error cannot replace newer progress');
assert.match(frame,/observedUpdateId = admitted\?\.state === 'admitted' \? admitted\.operation_id/);
assert.match(frame,/Задание принято\. Ход восстановления появится здесь\./);
console.log('Restore progress: operation identity and ordered generations, unbound/bound overlap, new submission invalidation, lost reply/rejoin, matching completion and stale error PASS');
