import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { stripTypeScriptTypes } from 'node:module';

const privateRoot=mkdtempSync(join(tmpdir(),'helene-harness-panel-'));
try {
  const observation=join(privateRoot,'observation.json');
  const consumer=fileURLToPath(new URL('../../tests/t_harness_journey_2910.py',import.meta.url));
  execFileSync(process.env.HELENE_TEST_PYTHON || 'python',['-B',consumer,'--consumer',privateRoot,observation],{
    cwd:privateRoot,windowsHide:true,timeout:60000,stdio:'pipe',env:{...process.env,PRAXIS_TEST:'1',PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',
      HOME:privateRoot,USERPROFILE:privateRoot,LOCALAPPDATA:join(privateRoot,'local'),APPDATA:join(privateRoot,'appdata')},
  });
  const {detail,actual_process}=JSON.parse(readFileSync(observation,'utf8'));
  const kit=new URL('../../ui-kit/',import.meta.url);
  const moduleURL=source=>'data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64');
  const text=moduleURL(readFileSync(new URL('text.ts',kit),'utf8'));
  const dom=moduleURL(readFileSync(new URL('dom.ts',kit),'utf8').replace('"./text"',JSON.stringify(text)));
  const steps=await import(moduleURL(readFileSync(new URL('steps.ts',kit),'utf8').replace('"./text"',JSON.stringify(text)).replace('"./dom"',JSON.stringify(dom))));
  const source=stripTypeScriptTypes(readFileSync(new URL('window/panel.ts',kit),'utf8'));
  const fragment=source.slice(source.indexOf('function stepRows('),source.indexOf('\nfunction openLink('));
  const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
  const render=new Function('clean','icon','esc','clip','fmtK','toolIcon','ACTIONS','toolTraceHTML','frameStripHTML','terminalLabel','textBox','subject','openLink',
    fragment+';return stepRows;')(s=>s,()=>'',esc,steps.clip,String,()=>'',steps.ACTIONS,steps.toolTraceHTML,()=>'',steps.terminalLabel,steps.textBox,steps.subject,()=>'');
  const row=render(detail,false).find(row=>row.key==='tool:real-call');
  assert(row,'actual durable call reaches production side-panel renderer');
  assert.match(row.html,/Харнесс ·/);assert.match(row.html,/Запуск процесса · квитанция/);
  assert.match(row.html,/REAL &lt;observed>/);assert.match(row.html,/harness &lt;requested>/);
  assert.match(row.html,/Текст для следующего шага модели/);
  assert(row.html.includes(esc(actual_process.argv[0])),'actual native executable is available in the trace');
  assert.doesNotMatch(row.html,/<observed>|<requested>/,'recorded source/output remain inert');
  assert(!row.cls.includes(' open'),'new actions remain compact');
  console.log('Actual native process -> durable receipts -> desktop reader -> production panel PASS');
} finally {
  const checked=resolve(privateRoot);
  if(!checked.startsWith(resolve(tmpdir())+ '\\') && !checked.startsWith(resolve(tmpdir())+'/'))throw Error('Temporary root left its parent');
  rmSync(checked,{recursive:true,force:true});
}
