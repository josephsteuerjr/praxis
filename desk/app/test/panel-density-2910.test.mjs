import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const kit = new URL('../../ui-kit/', import.meta.url);
const mod = source => 'data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64');
const text = mod(readFileSync(new URL('text.ts',kit),'utf8'));
const dom = mod(readFileSync(new URL('dom.ts',kit),'utf8').replace('"./text"',JSON.stringify(text)));
const steps = await import(mod(readFileSync(new URL('steps.ts',kit),'utf8').replace('"./text"',JSON.stringify(text)).replace('"./dom"',JSON.stringify(dom))));
const source=stripTypeScriptTypes(readFileSync(new URL('window/panel.ts',kit),'utf8'));
const fn=source.slice(source.indexOf('function stepRows('),source.indexOf('\nfunction openLink('));
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const render=new Function('clean','icon','esc','clip','fmtK','toolIcon','ACTIONS','toolTraceHTML','frameStripHTML','terminalLabel','textBox','subject',fn+';return stepRows;')(
  s=>s,s=>'',esc,steps.clip,String,()=>'',steps.ACTIONS,steps.toolTraceHTML,()=>'',steps.terminalLabel,steps.textBox,steps.subject);
const detail={id:'fixture',manifest:{status:'running'},iterations:[{call_id:'m',tools:[{
  call_id:'real',tool:'shell',args:{cmd:'echo <request>'},execution:{handler:'agent.tool_shell',status:'entered'},
  operations:[{kind:'process',argv:['bash','-lc','echo <actual>'],status:'returned',exit_code:0}],result:{head:'done'},
}]}]};
const row=render(detail).find(row=>row.key==='tool:real');
assert(!row.cls.includes(' open'),'new actions occupy a compact row');
assert.match(row.html,/aria-expanded="false"/);
assert.match(row.html,/class="st-detail" hidden/);
assert.match(row.html,/Харнесс · процесс bash · завершён/);
assert.match(row.html,/title="bash -lc echo &lt;actual> · returned"/,'exact observed invocation remains available');
assert.match(row.html,/Исполнение харнесса/);
assert.doesNotMatch(row.html,/<actual>/,'observed command remains inert');
console.log('Production panel: compact defaults, visible harness execution and inert full trace PASS');
