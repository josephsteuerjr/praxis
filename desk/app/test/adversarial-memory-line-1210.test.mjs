import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';
const dir=mkdtempSync(join(tmpdir(),'helene-memory-line-review-'));
try {
  let data=new Map(), denied=false;
  globalThis.localStorage={getItem:k=>{if(denied)throw Error('denied');return data.get(k)||null},setItem:(k,v)=>{if(denied)throw Error('denied');data.set(k,v)},removeItem:k=>{if(denied)throw Error('denied');data.delete(k)}};
  for(const f of ['memory-fold.ts','memory-line.ts'])writeFileSync(join(dir,f),readFileSync(new URL('../../ui-kit/'+f,import.meta.url),'utf8').replace('"./memory-fold"','"./memory-fold.ts"'));
  const a=await import(pathToFileURL(join(dir,'memory-line.ts')).href+'?tab=a');
  const b=await import(pathToFileURL(join(dir,'memory-line.ts')).href+'?tab=b');
  a.hideMemoryLine('window'); assert.equal(a.memoryLineHidden('window'),true); assert.equal(b.memoryLineHidden('window'),true);
  b.showMemoryLines(); assert.equal(a.memoryLineHidden('window'),false,'restore in another tab');
  denied=true; a.hideMemoryLine('window'); assert.equal(a.memoryLineHidden('window'),true); assert.equal(a.memoryLineHidden('window-11111111'),false);
  a.showMemoryLines(); assert.equal(a.memoryLineHidden('window'),false);
  denied=false; data.set('helene-hidden-context-rooms','broken'); assert.equal(a.memoryLineHidden('window'),false);
  console.log('adversarial-memory-line: cross-tab, denied storage, rooms, malformed storage PASS');
} finally { delete globalThis.localStorage; rmSync(dir,{recursive:true,force:true}); }
