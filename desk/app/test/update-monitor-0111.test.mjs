import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const code=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/review-activity.ts',import.meta.url),'utf8'))
  .replace(/^import .*$/gm,'').replace(/export /g,'');
let now=20000,scope='a',value={update:{id:'p',state:'waiting_agent',version:'rc',editing:{completed:2,total:5}}},failure=false;
const seen=[],badge={hidden:true,setAttribute(){}};
const {reviewActivity}=new Function('publishActivity','Date',code+';return {reviewActivity}')(function(...args){seen.push(args)},{now:()=>now});
const poll=reviewActivity(badge,async()=>{if(failure)throw Error('offline');return value},()=>scope);
await poll();assert.equal(badge.hidden,false);assert.equal(badge.textContent,'Исправления · 2/5');
now+=4000;value={update:{id:'p',state:'verification_requested',tests:{completed:6,total:12}}};
await poll();assert.equal(badge.textContent,'tests · 6/12');
now+=4000;value={running:2,reviewers:[{id:'one',live:true}],update:{id:'p',state:'reviewing'}};
await poll();assert.equal(badge.textContent,'review · 2');
now+=4000;failure=true;await poll();assert.equal(badge.hidden,false);assert.match(badge.textContent,/нет связи/);
assert.equal(seen.at(-1)[1].reviewers[0].live,false);
now+=4000;scope='b';failure=false;value={};await poll();assert.equal(badge.hidden,true);
for(const state of ['cancelled','completed','superseded']) {
  now+=4000;value={update:{id:'old',state},reviewers:[{id:'done',execution:'done',live:false}]};
  await poll();assert.equal(badge.hidden,true,`terminal ${state} is history, not active work`);
  assert.equal(seen.at(-1)[1].update.state,state,'terminal receipt remains available to action history');
  now+=16000;failure=true;await poll();assert.equal(badge.hidden,true,'offline polling must not resurrect terminal badge');
  failure=false;
}
now+=4000;value={update:{id:'new',state:'waiting_agent',error:'repair required'}};
await poll();assert.equal(badge.hidden,false);assert.equal(badge.textContent,'Обновление · нужна помощь');
now+=4000;value={running:1,update:{id:'old',state:'cancelled'}};
await poll();assert.equal(badge.hidden,false);assert.equal(badge.textContent,'review · 1');
console.log('Idle correction, actual tests/review, stale visibility and profile isolation PASS');
