// Exercise the real phone/miniapp component with deterministic local events.
import { mountPhone } from "../../ui-kit/phone";
import type { RunDetail } from "../../ui-kit/steps";
import "../../ui-kit/phone.css";

const wait = window.setTimeout.bind(window);
const settle = async () => { for (let i=0;i<5;i++) await new Promise(r => wait(r, 0)); };
const scheduled = new Map<number, {fn: TimerHandler; ms: number}>(); let seq=0;
window.setTimeout = ((fn: TimerHandler, ms=0) => { scheduled.set(++seq,{fn,ms});return seq; }) as typeof window.setTimeout;
window.clearTimeout = (id?: number) => { scheduled.delete(id || 0); };
let events: EventTarget;
class Source extends EventTarget { static CLOSED=2; readyState=1; constructor(_url: string) { super(); events=this; } close(){this.readyState=2;} }
window.EventSource = Source as unknown as typeof EventSource;
const publish = () => events.dispatchEvent(new MessageEvent("message",{data:JSON.stringify({t:"run",run_id: phase===2 ? "second" : "first"})}));
const now = new Date().toISOString();
const first = { id:"first",kind:"chat_turn",status:"running",chat_id:"123",chat_title:"Проверка миниаппа",created_at:new Date(Date.now()-2*3600_000).toISOString(),updated_at:now,goal_head:"OLD CONVERSATION START" };
const next = {...first,id:"second",created_at:now};
let phase=0;
const iterations = Array.from({length:16},(_,i)=>({call_id:`model-${i}`,status:"completed",text:i===0?"Первое пояснение к работе":"",tools:[{call_id:`tool-${i}`,tool:"read_file",args:{path:`chapter-${i}.md`},status:"received",result:{head:`Результат действия ${i}\n`+"Полный текст результата. ".repeat(25)}}]}));
const current: RunDetail={id:"first",manifest:{status:"running",created_at:now},origin:{source:"snapshot",text:"CURRENT REQUEST: проверить главы и сохранить замечания. "+"Подробность исходного действия. ".repeat(25)},iterations};
const completed: RunDetail={...current,manifest:{...current.manifest,status:"done",terminal:{status:"done"}},outcome:{text:"COMPLETE RESULT: все главы проверены. "+"Ни одна строка не потеряна. ".repeat(30),note:"done"}};
const second: RunDetail={id:"second",manifest:{status:"running",created_at:now},origin:{source:"snapshot",text:"NEW REQUEST: подготовить следующую проверку"},iterations:[]};
let holdNext=false; let release:(()=>void)|undefined;
window.fetch = (async(input: RequestInfo | URL) => {
  const path=new URL(String(input),location.origin).pathname;
  let data:unknown;
  if(path==="/api/state") data={agent:"Праксис · тест",level:"ok",phrase:"На связи",anatomy:false,runner:{alive:false,busy:false,run:"",since:0},brain:{last_call_at:Date.now()/1000}};
  else if(path==="/api/runs") data=phase===2 ? [next,{...first,status:"done"}] : [{...first,status:phase===1?"done":"running"}];
  else if(path==="/api/chats") data=[{peer_id:"123",title:"Проверка миниаппа",kind:"telegram",messages:2}];
  else if(path.startsWith("/api/chat-turns/")) data=phase ? [{run_id:"first",in:current.origin!.text,out:completed.outcome!.text}] : [];
  else if(path==="/api/run/first") {
    data=phase ? completed : current;
    if(holdNext){holdNext=false;return new Promise<Response>(resolve=>{release=()=>resolve(Response.json(data));});}
  }
  else if(path==="/api/run/second") data=second;
  else if(path==="/api/usage") data={status:"unavailable",reason:"Искусственный сценарий: счётчик не подключён"};
  else if(path==="/api/allowances") data={providers:[]};
  else throw new Error("Unexpected request: "+path);
  return Response.json(data);
}) as typeof window.fetch;
const checks:string[]=[];
const check=(ok:unknown,name:string)=>{if(!ok)throw new Error(name);checks.push(name);};
const root=document.querySelector<HTMLElement>("#app")!;
mountPhone(root,{platform:"telegram",theme:()=>new URLSearchParams(location.search).get("theme")==="dark"?"dark":"light",agentFallback:"Праксис · тест",auth:{storageKey:"gui-cycle-test",hasCredential:()=>false,redeem:async()=>({result:"none"}),pairScreen:()=>({title:"Fixture",text:"Fixture",retry:false})}});
try {
  await settle();
  const card=root.querySelector<HTMLElement>("#turn-live")!;
  check(card?.dataset.run==="first","Actual miniapp shows the running card");
  check(card?.dataset.status==="running" && root.querySelector('#top-phrase')!.textContent!.includes('Ведёт ход'),"An older run with fresh activity stays live");
  check(card.textContent!.includes("CURRENT REQUEST")&&!card.textContent!.includes("OLD CONVERSATION START"),"The trigger comes from this run, not conversation head");
  const earlier=card.querySelector<HTMLDetailsElement>('[data-detail="earlier"]')!;
  earlier.open=true;
  const origin=card.querySelector<HTMLDetailsElement>('[data-detail="origin"]')!;
  origin.open=true;
  const focused=earlier.querySelector<HTMLElement>("summary")!; focused.focus();
  check(card.querySelectorAll('.action-tool').length===iterations.length,"All actions remain available beyond the live preview limit");
  phase=1;publish();await settle();
  check(root.querySelector("#turn-live")===card,"Completion keeps the same card DOM");
  check(card.dataset.status==="done"&&card.textContent!.includes("COMPLETE RESULT"),"Completion retains the actions and adds the result");
  check(card.querySelector<HTMLDetailsElement>('[data-detail="earlier"]')!.open&&card.querySelector<HTMLDetailsElement>('[data-detail="origin"]')!.open,"Completion preserves expanded earlier actions and source text");
  check(document.activeElement===card.querySelector('[data-detail="earlier"] > summary'),"Completion preserves keyboard focus");
  const result=card.querySelector<HTMLDetailsElement>('[data-detail="outcome"]')!;result.open=true;
  check(result.querySelector('.run-reading')!.textContent===completed.outcome!.text,"The full saved outcome is readable");
  holdNext=true;publish();await settle();
  check(!!release,"A late response is held for the race check");
  phase=2;publish();await settle();release!();await settle();
  check(root.querySelector<HTMLElement>("#turn-live")!.dataset.run==="second","An older late response cannot replace the new active run");
  const history=root.querySelector<HTMLButtonElement>('[data-ev="first"] .ev-head')!;history.click();await settle();
  check(root.querySelector('[data-ev="first"]')!.textContent!.includes("COMPLETE RESULT"),"The completed result remains available from history after a new run starts");
  // Leave the last finished run expanded for visual inspection of long content.
  document.body.dataset.testStatus="passed";
  const report=document.createElement('pre');report.id="test-results";report.hidden=true;report.textContent=JSON.stringify({passed:checks.length,checks});root.append(report);
} catch(e) {
  document.body.dataset.testStatus="failed";
  const report=document.createElement('pre');report.id="test-results";report.textContent=String(e)+"\n"+checks.join("\n");root.prepend(report);throw e;
}
