import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../../ui-kit/window/native-resize.ts',import.meta.url),'utf8');
const {EDGES,MIN_WIDTH,MIN_HEIGHT,nextSize}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
const start={width:1200,height:800};
assert.deepEqual([...EDGES].sort(),['e','n','ne','nw','s','se','sw','w']);
// Dragging an edge outward grows the window; the opposite side is fixed by the shell.
assert.deepEqual(nextSize('e',start,50,30),{width:1250,height:800});
assert.deepEqual(nextSize('w',start,-50,30),{width:1250,height:800});
assert.deepEqual(nextSize('s',start,50,30),{width:1200,height:830});
assert.deepEqual(nextSize('n',start,50,-30),{width:1200,height:830});
assert.deepEqual(nextSize('se',start,10,20),{width:1210,height:820});
assert.deepEqual(nextSize('nw',start,10,20),{width:1190,height:780});
assert.deepEqual(nextSize('ne',start,10,20),{width:1210,height:780});
assert.deepEqual(nextSize('sw',start,10,20),{width:1190,height:820});
assert.deepEqual(nextSize('se',start,-5000,-5000),{width:MIN_WIDTH,height:MIN_HEIGHT});
assert.deepEqual(nextSize('e',start,0.6,0),{width:1201,height:800});
console.log(JSON.stringify({ok:true,edges:EDGES.length}));
