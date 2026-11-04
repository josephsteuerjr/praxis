import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {stripTypeScriptTypes} from 'node:module';
class Node {
 constructor(tag,cls='',text=''){this.tagName=tag;this.className=cls;this.textContent=text;this.children=[];this.isConnected=true;}
 append(...nodes){this.children.push(...nodes);}
 removeAttribute(key){delete this[key];}
}
let status={id:'whisper',ready:true,installed:false,download_bytes:10},polls=0,clicks=0,timers=[];
const shell=async command=>{
 if(command==='components_status'){polls++;return [structuredClone(status)];}
 assert.equal(command,'component_install');clicks++;
 status.progress={state:'running',stage:'unpacking',active:true,files:2,total_files:8};return {state:'running',active:true};
};
const output=stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/window/components-card.ts',import.meta.url),'utf8')
 .replace("import { shell } from './api';","const {shell}=require('./api');")
 .replace("import { button, card, el } from '../dom';","const {button,card,el}=require('../dom');")
 .replaceAll('export function ','function '),{mode:'transform'})+'\nexports.componentsCard=componentsCard;';
const context={exports:{},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{},
 require:name=>name==='./api'?{shell}:{el:(...args)=>new Node(...args),card:(title,body)=>body,
 button:(label,cls,click)=>Object.assign(new Node('button',cls,label),{click})}};
vm.runInNewContext(output,context);
const settle=async()=>{for(let n=0;n<12;n++)await Promise.resolve();};
const body=context.exports.componentsCard();await settle();
const rows=body.children[1],row=rows.children[0],button=row.children[3];
await button.click();await settle();
assert.equal(clicks,1);assert.equal(button.disabled,true);assert.match(row.children[1].textContent,/2 из 8 файлов/);
assert.doesNotMatch(body.children[2].textContent,/Компонент установлен/);assert.equal(row.children[2].value,.25);
status.progress={state:'failed',active:false,error:'fixture network stopped'};
timers.shift()();await settle();
assert.equal(button.disabled,false);assert.equal(button.textContent,'Повторить');assert.match(row.children[4].textContent,/network stopped/);
await button.click();await settle();
status={...status,installed:true,progress:{state:'completed',stage:'completed',active:false}};
timers.shift()();await settle();
assert.equal(rows.children[0],row);assert.equal(button.hidden,true);assert.match(row.children[0].textContent,/установлено/);
assert.equal(row.children[2].hidden,true);
status={...status,installed:false,progress:{state:'running',stage:'downloading',active:true,bytes:1,total_bytes:4}};
await button.click();await settle();body.isConnected=false;
const before=polls;for(const timer of timers.splice(0))timer();await settle();assert.equal(polls,before);
console.log('Component installation: actual control IPC/progress/failure/retry/completion/disposal PASS');
