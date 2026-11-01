import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
class Element {
  constructor(tag) { Object.assign(this,{tag,children:[],attrs:{},className:'',textContent:'',open:false,hidden:false});this.classList={add:name=>this.className+=' '+name}; }
  append(...children) {for(const child of children){child.parent=this;this.children.push(child);}}
  replaceChildren(...children) {this.children=[];this.append(...children);}
  setAttribute(k,v) {this.attrs[k]=v;}
  remove() {this.parent.children=this.parent.children.filter(child=>child!==this);}
}
const source=readFileSync(new URL('../../ui-kit/window/work-activity.ts',import.meta.url),'utf8');
const code=stripTypeScriptTypes(source).replace(/export /g,'');
const document={createElement:tag=>new Element(tag)};
const {workActivity,publishActivity,observeActivity}=new Function('document',code+';return {workActivity,publishActivity,observeActivity}')(document);
const root=new Element('section'),render=workActivity(root);
const walk=node=>[node,...node.children.flatMap(walk)],find=cls=>walk(root).filter(node=>node.className.split(' ').includes(cls));
const reviewers=Array.from({length:20},(_,i)=>({id:`review/unit-${i}`,name:`Reviewer ${i}`,execution:'running',live:true,started_at:Date.now()/1000-90,observed_at:Date.now()/1000}));
const value={running:20,reviewers,update:{id:'one',role:'updater',state:'verification_requested',tests:{state:'running',live:true,completed:3,total:12,slots:12,tests_completed:74,active:[{slot:0,file:'t_real_<markup>.py'}]}}};
render(value);
assert.equal(find('work-reviewer').length,20,'no arbitrary two/three-worker limit');
const first=find('work-reviewer')[0];first.open=true;const tests=find('work-tests')[0];tests.open=true;
assert.equal(find('work-current')[0].textContent,'t_real_<markup>.py','filename is text, never markup');
assert.match(find('work-numbers')[0].textContent,/3 из 12 этапов.*74/);
assert.equal(find('work-state')[0].textContent,'работает · 1 мин 30 с');
render({...value,update:{...value.update,tests:{...value.update.tests,completed:4}}});
assert.equal(find('work-reviewer')[0],first);assert.equal(first.open,true);assert.equal(tests.open,true,'polling retains expansions');
render({...value,reviewers:reviewers.map(row=>({...row,live:false})),update:{...value.update,tests:{...value.update.tests,live:false}}});
assert.equal(find('work-current')[0].textContent,'нет свежего подтверждения');
assert.equal(find('work-state')[0].textContent,'нет свежего подтверждения');
let scope='owner-A';const stop=observeActivity(()=>scope,render);publishActivity('owner-A',value);assert.equal(root.hidden,false);
scope='owner-B';publishActivity('owner-A',value);assert.equal(root.hidden,true);assert.equal(find('work-reviewer').length,0,'foreign profile rows are removed');
stop();
console.log('20 reviewer rows, exact counters, inert text, retained expansions, stale receipts and scope change PASS');
