import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const dir=mkdtempSync(join(tmpdir(),'helene-diagnostics-review-'));
try {
  const read=file=>readFileSync(new URL('../../ui-kit/'+file,import.meta.url),'utf8');
  const text=read('text.ts'); const esc=text.slice(text.indexOf('export const esc'),text.indexOf('export function fmtTime'));
  const inspection=read('window/views/inspection-status.ts').replace(/^import .*;\r?\n/gm,'');
  const journal=read('window/views/journal.ts').replace(/^import .*;\r?\n/gm,'');
  writeFileSync(join(dir,'stand.ts'),esc+inspection+journal.slice(0,journal.indexOf('export async function render')));
  const {inspectionHTML,journalEvents}=await import(pathToFileURL(join(dir,'stand.ts')));
  const evil='<img src=x onerror=alert(1)>';
  const html=inspectionHTML({id:'test',state:'reported',checks:[null,{id:evil,status:'invented',evidence:evil}],goals:{bad:true},summary:evil});
  assert.ok(!html.includes(evil));assert.match(html,/&lt;img/);assert.match(html,/Проверено: 0/);
  assert.doesNotThrow(()=>inspectionHTML({id:'test',state:'pending',goals:[null,{id:'x',title:evil}],checks:{bad:true}}));
  assert.equal(journalEvents({llm:{bad:true},skips:[null,'bad']}).length,0);
  const events=journalEvents({llm:[null,{ts:2,err:'429 '+evil}],skips:[{ts:3,detail:'x'.repeat(20000)}]});
  assert.equal(events.length,2);assert.ok(events[0].description.length<=180);assert.equal(events[0].detail.length,20000);
  assert.match(events[1].description,/ограничил/);assert.equal(journalEvents(null).length,0);
  console.log('adversarial-diagnostics: malformed, malicious, long, partial and unknown statuses PASS');
} finally {rmSync(dir,{recursive:true,force:true});}
