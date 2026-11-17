// 2.8.7 (Tim, Windows 10, 17.11): "окно не успевает за движением курсора". pywebview moved the
// frameless window from the renderer, one SetWindowPos per mousemove. On Windows the header and
// the edges now hand the drag to the system's own move/size loop (python_app.begin_native_drag).
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../../ui-kit/window/native-resize.ts',import.meta.url),'utf8');
const mod=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
const {installNativeDrag,installNativeResize,NATIVE_DRAG_PLATFORMS,NATIVE_RESIZE_PLATFORMS,DOUBLE_PRESS_MS,MAXIMIZED_CLASS}=mod;

// Only where the shell implements the system loop; Linux keeps the renderer's resize.
assert.deepEqual([...NATIVE_DRAG_PLATFORMS],['windows']);
for(const p of NATIVE_DRAG_PLATFORMS) assert.ok(NATIVE_RESIZE_PLATFORMS.includes(p));

// Header: one press = one begin_drag; a quick second press at the same spot = maximize toggle.
let clock=1000,toggles=0,listener=null;const asked=[];
const invoke=async(command,args)=>{asked.push([command,args]);return true;};
installNativeDrag(invoke,()=>toggles++,'#chrome, .chrome-drag',{addEventListener:(type,fn)=>{assert.equal(type,'mousedown');listener=fn;}},()=>clock);
let prevented=0;
const press=(matches,{x=100,y=10,button=0}={})=>listener({button,screenX:x,screenY:y,target:{matches:()=>matches},preventDefault(){prevented++;}});
press(true);
assert.deepEqual(asked,[['python_window',{action:'begin_drag',edge:'move'}]]);
clock+=DOUBLE_PRESS_MS-50;press(true,{x:102,y:11});
assert.equal(toggles,1);assert.equal(asked.length,1,'the second press of a double click does not start another drag');
clock+=100;press(true);clock+=DOUBLE_PRESS_MS+1;press(true);
assert.equal(toggles,1,'presses further apart than the double-click time are two drags');
assert.equal(asked.length,3);
clock+=100;press(true,{x:300});
assert.equal(toggles,1,'a second press far away is a new drag, not a double click');
assert.equal(asked.length,4);
// Buttons inside the header, other mouse buttons and empty targets are not drags.
press(false);press(true,{button:2});listener({button:0,target:null,preventDefault(){}});
assert.equal(asked.length,4);assert.equal(prevented,5);

// Edges: with system=true a press asks for the system size loop with that edge, no renderer resize.
const handles=[];const doc={
  querySelector:()=>null,
  createElement:()=>{const h={dataset:{},listeners:{},setAttribute(){},addEventListener(t,fn){this.listeners[t]=fn;},
    setPointerCapture(){throw new Error('the system loop owns the mouse, not the handle');}};handles.push(h);return h;},
  body:{appendChild(){}},
  documentElement:{classList:{toggle(){},contains:(name)=>name===MAXIMIZED_CLASS&&maximized}},
  defaultView:{addEventListener(){},setTimeout(){},clearTimeout(){},outerWidth:1200,outerHeight:800},
};
let maximized=false;asked.length=0;
installNativeResize(invoke,doc,true);
await Promise.resolve();
asked.length=0;
const se=handles.find(h=>h.className.endsWith('-se'));
se.listeners.pointerdown({button:0,pointerId:1,screenX:0,screenY:0,preventDefault(){}});
assert.deepEqual(asked,[['python_window',{action:'begin_drag',edge:'se'}]]);
maximized=true;se.listeners.pointerdown({button:0,pointerId:1,screenX:0,screenY:0,preventDefault(){}});
assert.equal(asked.length,1,'a maximized window has no edges to drag');

// The window wiring picks the system loop exactly on NATIVE_DRAG_PLATFORMS.
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
assert.match(main,/NATIVE_DRAG_PLATFORMS\.includes\(String\(bridge\.platform\)\)/);
assert.doesNotMatch(main,/installNativeResize\(invoke, document, true\)/);
assert.match(main,/installNativeResize\(invoke\);\s*installNativeDrag/,'Windows resizing uses fixed-edge API while header keeps its native loop');
console.log(JSON.stringify({ok:true,nativeDrag:true}));
