// Actual save callbacks and writeConfig, with an isolated config store.
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const src = readFileSync(new URL('../../ui-kit/window/views/settings-frame.ts', import.meta.url), 'utf8');
const dir = mkdtempSync(join(tmpdir(), 'helene-review-settings-'));
try {
  const normal = src.indexOf('button("Сохранить", "primary", async () => {');
  const force = src.indexOf('button("Перезаписать своим", "quiet", async () => {');
  const body = (from, end) => src.slice(src.indexOf('async () => {', from), src.indexOf(end, from)+6);
  const normalFn = body(normal, '\n    }),\n    saveOut,');
  const forceFn = body(force, '\n    }),\n  );\n  conflictBox.append');
  assert.ok(normal > 0 && force > 0 && normalFn.endsWith('}') && forceFn.endsWith('}'));
  const moduleFile = join(dir, 'stand.ts');
  writeFileSync(moduleFile, src.slice(src.indexOf('export const RESTART_BLOCKS'), src.indexOf('export interface Config')) + `
export function stand(config, draft, options = {}) {
  let c = structuredClone(config), disk = structuredClone(config), seenMtime = '100';
  let saves=0; const container={dataset:{settingsRevision:'1',settingsDirty:'true'}};
  const cfg={needs_local_setup:options.first}; const S={agent:'Agent'};
  const built={collect:()=>'',note:()=>''}, saveOut={className:'',textContent:''}, restartBtn={hidden:true}, conflictBox={hidden:true};
  const keepBlock=(a,b)=>({...a,...b}), collectConnection=()=>'', validateOwnerContext=()=>{};
  const humanError=e=>({text:e.message});
  const shell=async(name,args)=>{
    if(name==='config_load') return {config:structuredClone(disk),mtime_ns:seenMtime};
    if(name==='config_save') {
      if(options.stale && args.mtimeNs) return {ok:false,code:'stale'};
      disk=JSON.parse(args.config); saves++; return {ok:true,mtime_ns:'200'};
    }
    throw new Error(name);
  };
  ${src.slice(src.indexOf('class StaleConfig'), src.indexOf('  const conflictBox = el("div");'))}
  const normal=${normalFn}; const force=${forceFn};
  return {normal,force,saveOut,restartBtn,conflictBox,get disk(){return disk},get saves(){return saves}};
}
`);
  const {stand} = await import(pathToFileURL(moduleFile));
  const base={model:{key:'synthetic'},owner_context:{messages:600,custom_marker:'keep'}};
  for (const action of ['normal','force']) {
    const same=stand(base,structuredClone(base)); await same[action]();
    assert.equal(same.restartBtn.hidden,true,action+' no-op');
    const changed=structuredClone(base); changed.owner_context.messages=900;
    const actual=stand(base,changed); await actual[action]();
    assert.equal(actual.restartBtn.hidden,false,action+' actual change');
    assert.match(actual.saveOut.textContent,/контекст владельца.*перезапуск/);
    assert.equal(actual.disk.owner_context.custom_marker,'keep');
    const immediate=stand(changed,structuredClone(changed)); await immediate[action]();
    assert.equal(immediate.restartBtn.hidden,true,action+' accepted own immediate write');
    const first=stand({model:{}},{model:{}},{first:true}); await first[action]();
    assert.equal(first.restartBtn.hidden,true); assert.match(first.saveOut.textContent,/ключ модели пуст/);
  }
  const changed=structuredClone(base); changed.owner_context.messages=900;
  const foreign=stand(base,changed,{stale:true}); await foreign.normal();
  assert.equal(foreign.saves,0); assert.equal(foreign.conflictBox.hidden,false);
  await foreign.force(); assert.equal(foreign.saves,1); assert.equal(foreign.restartBtn.hidden,false);
  console.log('adversarial-settings: 10 save paths PASS');
} finally { rmSync(dir,{recursive:true,force:true}); }
