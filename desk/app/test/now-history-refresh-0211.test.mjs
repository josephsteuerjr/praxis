import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import test from 'node:test';

const source=readFileSync(new URL('../../ui-kit/window/views/now.ts',import.meta.url),'utf8');
function windowFixture(view='now') {
  const S={view,runs:[],agentState:{phrase:'Ready',runner:{alive:true,busy:false}},evCache:new Map()};
  const container={html:'',writes:0,querySelector:()=>null,querySelectorAll:()=>[],
    set innerHTML(value){this.html=value;this.writes++;}};
  const compiled=stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,''));
  const factory=new Function('S','cfg','api','mountUsage','usageShell','frameStripHTML','activityHTML','updateActivity',
    'foreignHarness','runIsRecent','bindRuns','loadWords','runDetail','runRowHTML','esc','fmtDur','fmtTime','window',
    compiled+'\nreturn {render,runsChanged:typeof runsChanged === "function" ? runsChanged : ()=>{}};');
  const noop=()=>'';
  const now=factory(S,{},noop,noop,noop,noop,noop,noop,()=>false,()=>true,noop,async()=>{},async()=>undefined,
    r=>`<article>${r.id}</article>`,String,String,String,{setTimeout:()=>0});
  return {S,container,now};
}
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const run=(id,status='completed')=>({id,kind:'chat_turn',status});
test('a successful room retry populates an already mounted empty Now screen',async()=>{
  const {S,container,now}=windowFixture();await now.render(container);
  const previous=S.runs;S.runs=[run('existing-history')];now.runsChanged(previous);await flush();
  assert.match(container.html,/<article>existing-history<\/article>/);
});
test('unchanged idle polling preserves the rendered disclosures',async()=>{
  const {S,container,now}=windowFixture();S.runs=[run('existing-history')];await now.render(container);
  const writes=container.writes,previous=S.runs;S.runs=structuredClone(previous);
  now.runsChanged(previous);await flush();assert.equal(container.writes,writes);
});
test('terminal status changes refresh existing history without a live runner',async()=>{
  const {S,container,now}=windowFixture();S.runs=[run('existing-history','running')];await now.render(container);
  const writes=container.writes,previous=S.runs;S.runs=[run('existing-history','failed')];
  now.runsChanged(previous);await flush();assert.equal(container.writes,writes+1);
});
test('room polling cannot replace another selected screen',async()=>{
  const {S,container,now}=windowFixture();await now.render(container);S.view='talk';
  const writes=container.writes,previous=S.runs;S.runs=[run('existing-history')];
  now.runsChanged(previous);await flush();assert.equal(container.writes,writes);
});
test('wake-only and offscreen history changes avoid redrawing Now',async()=>{
  const {S,container,now}=windowFixture();S.runs=Array.from({length:42},(_,i)=>run(String(i)));
  await now.render(container);const writes=container.writes,previous=S.runs;
  S.runs=[...previous.slice(0,40),run('changed-old-history'),{id:'wake',kind:'wake',status:'completed'}];
  now.runsChanged(previous);await flush();assert.equal(container.writes,writes);
});
