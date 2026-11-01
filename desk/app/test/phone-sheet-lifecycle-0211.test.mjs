import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../../ui-kit/phone.ts',import.meta.url),'utf8');
const section=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const functions=stripTypeScriptTypes(section('  function openDialog(', '  function openSettings(')+section('  function openSheet()','  function closeSheet()')+section('  function closeSheet()','  veil.addEventListener('));
function fixture(){
  const frames=[],timers=new Map();let sequence=0;
  const document={hidden:false,activeElement:null};
  class Element extends EventTarget {
    constructor(){super();this.classes=new Set();this.attributes=new Map();this.children=[];this.hidden=true;this.isConnected=true;this.classList={add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),contains:name=>this.classes.has(name)}}
    append(...children){this.children.push(...children)}
    replaceChildren(...children){this.children=children}
    setAttribute(name,value){this.attributes.set(name,value)}
    removeAttribute(name){this.attributes.delete(name)}
    focus(){document.activeElement=this}
  }
  const sheet=new Element(),veil=new Element(),sheetHead=new Element(),sheetBody=new Element(),origin=new Element();document.activeElement=origin;
  const views={start(){},stop(){}};
  const make=new Function('sheet','veil','sheetHead','sheetBody','HTMLElement','document','el','requestAnimationFrame','setTimeout','clearTimeout','nowView','actionsView','opts','drawMore',`
    let sheetKind='',dialogFocus=null,sheetEpoch=0,sheetCloseTimer;const tab='now',running=true;
    ${functions}
    return {openDialog,openSheet,closeSheet,kind:()=>sheetKind};
  `);
  const app=make(sheet,veil,sheetHead,sheetBody,Element,document,()=>new Element(),fn=>frames.push(fn),fn=>{const id=++sequence;timers.set(id,fn);return id},id=>timers.delete(id),views,views,{onSheet(){}},()=>{});
  return {app,sheet,veil,sheetBody,document,origin,frames:()=>{for(const fn of frames.splice(0))fn()},timers:()=>{for(const [id,fn] of [...timers]){timers.delete(id);fn()}}};
}
test('closing before the opening frame cannot reopen the sheet or steal restored focus',()=>{
  const f=fixture();f.app.openDialog('settings','Настройки');f.app.closeSheet();f.frames();f.timers();
  assert.equal(f.sheet.classList.contains('show'),false);assert.equal(f.sheet.hidden,true);assert.equal(f.document.activeElement,f.origin);
});
test('an earlier close cannot hide or clear the newly opened actions',()=>{
  const f=fixture();f.app.openDialog('settings','Настройки');f.frames();f.app.closeSheet();f.app.openDialog('actions','Действия');f.sheetBody.append('new actions');f.frames();f.timers();
  assert.equal(f.sheet.hidden,false);assert.equal(f.sheet.classList.contains('show'),true);assert.deepEqual(f.sheetBody.children,['new actions']);assert.equal(f.app.kind(),'actions');
});
test('only the current opening frame may focus its close button',()=>{
  const f=fixture();f.app.openDialog('settings','Настройки');f.app.openDialog('actions','Действия');f.frames();
  assert.equal(f.document.activeElement.attributes.get('aria-label'),'Закрыть действия');assert.equal(f.sheet.attributes.get('aria-label'),'Действия');
});
test('settled closure clears dialog semantics, contents and backdrop',()=>{
  const f=fixture();f.app.openDialog('actions','Действия');f.frames();f.sheetBody.append('rows');f.app.closeSheet();f.timers();
  assert.equal(f.sheet.hidden,true);assert.equal(f.veil.hidden,true);assert.equal(f.sheetBody.children.length,0);assert.equal(f.sheet.attributes.has('role'),false);assert.equal(f.sheet.classList.contains('phone-dialog'),false);
});
test('the more sheet also ignores its obsolete opening frame after closing',()=>{
  const f=fixture();f.app.openSheet();f.app.closeSheet();f.frames();f.timers();assert.equal(f.sheet.hidden,true);assert.equal(f.sheet.classList.contains('show'),false);
});
