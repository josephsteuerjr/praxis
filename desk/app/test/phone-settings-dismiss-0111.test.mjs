// Run the production gesture handler with real EventTarget touch delivery.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../../ui-kit/phone.ts',import.meta.url),'utf8');
const callback=source.match(/sheet\.addEventListener\("touchend", \(e\) => \{([\s\S]*?)\n  \}\);/)[1];
for(const kind of ['settings','more'])for(const scroll of [0,100]){
  let closes=0;
  const sheet=new EventTarget(),listener=new Function('sheetKind','sheetBody','dragY','closeSheet','return e=>{'+callback+'}')(kind,{scrollTop:scroll},0,()=>closes++);
  sheet.addEventListener('touchend',listener);
  const event=new Event('touchend');Object.defineProperty(event,'changedTouches',{value:[{clientY:180}]});sheet.dispatchEvent(event);
  assert.equal(closes,kind==='more'&&scroll===0?1:0,`${kind}, scroll ${scroll}`);
}
console.log('Production touch handler: settings survives pull-down; more retains swipe dismissal PASS');
