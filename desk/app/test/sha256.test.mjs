import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const code=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/sha256.ts',import.meta.url),'utf8')).replace(/^export /gm,'');
const sha256=new Function(code+'\nreturn sha256;')();
test('portable byte digest agrees with native SHA-256 across padding boundaries and audio-sized data',()=>{
 for(const bytes of [Buffer.alloc(0),Buffer.from('abc'),...Array.from({length:130},(_,length)=>randomBytes(length)),randomBytes(1024*1024)]){
  assert.equal(sha256(bytes),createHash('sha256').update(bytes).digest('hex'),'byte length '+bytes.length);
 }
});
