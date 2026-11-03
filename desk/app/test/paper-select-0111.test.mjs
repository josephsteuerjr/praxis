// Execute the actual shared picker: real native select remains the config owner.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const document=new EventTarget(),window=new EventTarget();window.visualViewport=new EventTarget();
class Element extends EventTarget {
  constructor(tag){super();this.tagName=tag.toUpperCase();this.children=[];this.parentElement=null;this.attrs=new Map();this.className='';this.style={};this.dataset={};this.hidden=false;this.disabled=false;this.tabIndex=0;this._text='';this.title='';
    this.classList={add:value=>{this.className+=' '+value},remove:value=>{this.className=this.className.split(' ').filter(v=>v!==value).join('')}};}
  set textContent(value){this._text=String(value);this.children=[]}get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(...nodes){for(const node of nodes){node.remove();this.children.push(node);node.parentElement=this}}
  before(node){const p=this.parentElement,i=p.children.indexOf(this);node.remove();p.children.splice(i,0,node);node.parentElement=p}
  replaceWith(node){const p=this.parentElement,i=p.children.indexOf(this);node.remove();p.children.splice(p.children.indexOf(this),1,node);node.parentElement=p;this.parentElement=null}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);this.parentElement=null}
  setAttribute(k,v){this.attrs.set(k,String(v))}getAttribute(k){return this.attrs.get(k)||null}removeAttribute(k){this.attrs.delete(k)}
  contains(node){return node===this||this.children.some(child=>child.contains(node))}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='select'&&child instanceof Select?[child]:[]),...child.querySelectorAll(selector)])}
  closest(selector){let n=this;while(n){if(selector==='[role="dialog"]'&&n.getAttribute('role')==='dialog')return n;n=n.parentElement}return null}
  get isConnected(){return root.contains(this)}
  focus(){document.activeElement=this}scrollIntoView(){}getBoundingClientRect(){return {left:20,top:100,bottom:142,width:280}}get scrollHeight(){return 160}
  showPopover(){this.popoverOpen=true}hidePopover(){this.popoverOpen=false}
  click(){this.dispatchEvent(new Event('click'))}
}
class Select extends Element {
  constructor(){super('select');this.options=[];this.selectedIndex=0;this.multiple=false;this.size=0;this.labels=[{querySelector:()=>({textContent:'Connection'})}]}
  get selectedOptions(){return this.options[this.selectedIndex]?[this.options[this.selectedIndex]]:[]}
  get value(){return this.options[this.selectedIndex]?.value||''}set value(value){this.selectedIndex=this.options.findIndex(o=>o.value===value)}
}
class Group extends Element {}
document.createElement=tag=>new Element(tag);
const root=new Element('section'),select=new Select();root.append(select);
for(const [value,label,disabled] of [['automatic','Automatic',false],['blocked','Disabled',true],['stable','Permanent <HTTPS>',false]]){
  const option=new Element('option');Object.assign(option,{value,label,disabled});Object.defineProperty(option,'selected',{get:()=>select.options[select.selectedIndex]===option});select.options.push(option);select.append(option);
}
const source=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/paper-select.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'')).replace(/export /g,'');
const observer={};class MutationObserver{constructor(fn){observer.scan=fn}observe(){}disconnect(){observer.closed=true}}
const factory=new Function('document','window','MutationObserver','HTMLOptGroupElement','innerWidth','innerHeight',source+';return mountPaperSelects;');
const mount=factory(document,window,MutationObserver,Group,420,720),dispose=mount(root);
const walk=node=>[node,...node.children.flatMap(walk)];
const button=walk(root).find(n=>n.className==='paper-select-trigger');
const menu=()=>walk(root).find(n=>n.className==='paper-select-menu');
const rows=()=>walk(menu()).filter(n=>n.getAttribute('role')==='option');
function key(value){const event=new Event('keydown',{cancelable:true});Object.defineProperty(event,'key',{value});document.dispatchEvent(event)}
let changes=0,inputs=0;select.addEventListener('change',()=>changes++);select.addEventListener('input',()=>inputs++);
assert.equal(button.getAttribute('aria-label'),'Connection');assert.equal(select.value,'automatic');
button.click();assert.equal(button.getAttribute('aria-expanded'),'true');assert.equal(menu().closest('[role="dialog"]'),null);
key('ArrowDown');assert.equal(document.activeElement.textContent,'Permanent <HTTPS>','keyboard skips disabled entries');
key('Enter');assert.equal(select.value,'stable');assert.equal(changes,1);assert.equal(inputs,1);assert.equal(menu(),undefined);
assert.match(button.textContent,/Permanent <HTTPS>/,'labels remain inert text');
button.click();rows()[1].click();assert.equal(select.value,'stable');assert.equal(changes,1,'disabled row cannot alter real config');
key('Escape');assert.equal(menu(),undefined);assert.equal(document.activeElement,button);
button.click();rows()[2].click();assert.equal(changes,1,'selecting same item does not emit another save/change');
select.disabled=true;observer.scan();await Promise.resolve();assert.equal(button.disabled,true);button.click();assert.equal(menu(),undefined);
select.disabled=false;observer.scan();await Promise.resolve();button.click();key('Tab');assert.equal(document.activeElement,button);assert.equal(menu(),undefined);
const cache=new Element('section');
for(let n=0;n<5;n++){
  cache.append(select.parentElement);observer.scan();await Promise.resolve();
  assert.equal(cache.children[0],select,'closing cached settings restores exactly one native control');
  root.append(select);observer.scan();await Promise.resolve();
  assert.equal(walk(root).filter(node=>node.className==='paper-select-trigger').length,1,'reopening never duplicates picker buttons');
  assert.equal(select.value,'stable','cached unsaved selection survives reopening');
}
dispose();assert.equal(root.children[0],select);assert.equal(select.getAttribute('aria-hidden'),null);assert.equal(observer.closed,true);
console.log('Actual paper select: native config handlers, keyboard, disabled options, inert labels, focus, cleanup PASS');
