import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = readFileSync(new URL('../../ui-kit/window/panel-widths.ts', import.meta.url), 'utf8');
const { mountPanelWidths } = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
const store = new Map();
globalThis.localStorage = { getItem:k=>store.get(k) ?? null, setItem:(k,v)=>store.set(k,v) };
globalThis.window = { addEventListener(){}, removeEventListener(){} };
globalThis.MutationObserver = class { observe(){} disconnect(){} };
const classes = new Set(['with-panel']);
const widths = {rail:300,panel:380};
const handles=[];
globalThis.getComputedStyle = () => ({getPropertyValue:k=>widths[k.includes('rail')?'rail':'panel']+'px'});
globalThis.document = {createElement:()=>{
  const attrs={}, events={};
  return {setAttribute:(k,v)=>attrs[k]=v,getAttribute:k=>attrs[k],addEventListener:(k,f)=>events[k]=f,removeEventListener(){},remove(){},events,
    setPointerCapture(){},hasPointerCapture:()=>false,releasePointerCapture(){}};
}};
const app = {clientWidth:1000,classList:{contains:k=>classes.has(k),add:k=>classes.add(k),remove:k=>classes.delete(k)},
  querySelector:s=>({getBoundingClientRect:()=>({width:widths[s.slice(1)]})}),
  append:h=>handles.push(h),style:{setProperty:(k,v)=>widths[k.includes('rail')?'rail':'panel']=parseFloat(v)}};
const dispose=mountPanelWidths(app);
const rail=handles[0];
const press=key=>rail.events.keydown({key,preventDefault(){}});
handles[1].events.keydown({key:'Home',preventDefault(){}});
press('End');
assert.equal(widths.rail,1000-220-440,'End refreshes bounds and leaves readable central content');
press('End');
const maximum=Number(rail.getAttribute('aria-valuemax'));
assert.equal(widths.rail,maximum);
press('ArrowLeft');
assert.equal(widths.rail,Math.max(220,maximum-20),'shrink immediately from displayed capped width');
assert.equal(Number(store.get('frame.rail.width')),widths.rail);
rail.events.pointerdown({button:0,pointerId:1,clientX:300,preventDefault(){}});
rail.events.pointermove({pointerId:1,clientX:280});
rail.events.pointercancel();
assert.equal(classes.has('resizing-panels'),false);
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
assert.match(main,/if \(!event\.persisted\)\s*\{[^}]*disposeWidths\(\)/,'bfcache pagehide retains controls');
dispose();
console.log('PASS panel widths: capped keyboard, persistence, pointer cancellation, bfcache disposal guard');
