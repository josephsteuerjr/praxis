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

// 2.8.2 (owner, 16.11): the 5px strips stayed over the screen edge of a maximized window.
const {MAXIMIZED_CLASS,syncMaximized,installHeaderToggle}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
const classes=new Set();
const doc={documentElement:{classList:{toggle:(name,on)=>{on?classes.add(name):classes.delete(name);return on;},contains:(name)=>classes.has(name)}}};
const asked=[];
const shell=(answer)=>async(command,args)=>{asked.push([command,args]);return answer;};
assert.equal(await syncMaximized(shell({maximized:true}),doc),true);
assert.ok(classes.has(MAXIMIZED_CLASS));
assert.deepEqual(asked[0],['python_window',{action:'state'}]);
assert.equal(await syncMaximized(shell({maximized:false}),doc),false);
assert.ok(!classes.has(MAXIMIZED_CLASS));
// Only an explicit true hides the handles: an older shell answers null.
for(const answer of [null,undefined,{maximized:'yes'},{},'true']){
  classes.add(MAXIMIZED_CLASS);
  assert.equal(await syncMaximized(shell(answer),doc),false);
  assert.ok(!classes.has(MAXIMIZED_CLASS));
}
const css=readFileSync(new URL('../../ui-kit/window/styles/app.css',import.meta.url),'utf8');
assert.match(css,new RegExp(`html\.${MAXIMIZED_CLASS} \.native-resize \{ display:none; \}`));
// Double click on the drag region toggles; a click inside a button of the header does not.
let toggles=0,listener=null;
installHeaderToggle(()=>toggles++,'#chrome, .chrome-drag',{addEventListener:(type,fn)=>{assert.equal(type,'dblclick');listener=fn;}});
const hit=(matches,button=0)=>listener({button,target:{matches:()=>matches},preventDefault(){}});
hit(true);hit(false);hit(true,2);listener({button:0,target:null,preventDefault(){}});
assert.equal(toggles,1);
console.log(JSON.stringify({ok:true,maximized:true}));
