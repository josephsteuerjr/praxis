import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/version.ts',import.meta.url),'utf8')).replace(/export /g,'');
function stand(dirty=false) {
  const window=new EventTarget(),session=new Map(),device=new Map([['frame.device','kept-device']]);
  const calls={updates:0,deletes:0,reloads:[]};
  if(dirty)window.addEventListener('helene:shell-update',event=>event.preventDefault());
  const location={pathname:'/m/',href:'https://fixture.invalid/m/#device=kept-device',replace:url=>calls.reloads.push(url)};
  const factory=new Function('document','window','fetch','location','sessionStorage','localStorage','navigator','caches','Event','URL',source+';return checkShellVersion;');
  const check=factory({querySelector:()=>({getAttribute:()=>'/m/assets/index-old.js'})},window,
    async()=>({ok:true,text:async()=>'<script type="module" src="/m/assets/index-new.js"></script>'}),location,
    {getItem:key=>session.get(key),setItem:(key,value)=>session.set(key,value)},
    {getItem:key=>device.get(key),clear:()=>assert.fail('must never clear device storage')},
    {serviceWorker:{getRegistrations:async()=>[{update:async()=>calls.updates++}]}},
    {keys:async()=>['unrelated-cache'],delete:async()=>calls.deletes++},Event,URL);
  return {check,calls,device,session};
}
{
  const t=stand();assert.equal(await t.check(),true);assert.equal(t.calls.updates,1);
  assert.equal(t.device.get('frame.device'),'kept-device');assert.equal(t.calls.deletes,0);
  const target=new URL(t.calls.reloads[0]);assert.equal(target.origin,'https://fixture.invalid');
  assert.equal(target.hash,'#device=kept-device');assert.equal(target.searchParams.get('v'),'new');
  assert.equal(await t.check(),false);assert.equal(t.calls.reloads.length,1,'same build never reloads twice');
}
{
  const t=stand(true);assert.equal(await t.check(),false);assert.equal(t.calls.reloads.length,0);
  assert.equal(t.session.size,0,'deferred update remains eligible after saving the draft');
  assert.equal(t.device.get('frame.device'),'kept-device');
}
console.log('Actual shell updater: device/origin/fragment retained, no foreign cache deletion, dirty-form deferral and one reload PASS');
