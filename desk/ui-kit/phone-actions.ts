import { el } from './dom';
import { fmtTime } from './text';
import { forgePanel } from './window/views/forge-panel';
import { mountActionSteps } from './window/panel';
import { workActivity,type WorkActivity } from './window/work-activity';
import type { RunDetail } from './steps';
import './window/views/forge-panel.css';
import './window/styles/turns.css';
import './window/styles/work-activity.css';

type Run={id:string;kind?:string;status?:string;terminal_status?:string;created_at?:string;updated_at?:string;goal_head?:string};
type Request=<T>(path:string,body?:unknown,signal?:AbortSignal)=>Promise<T>;
type State={runner?:{alive?:boolean;busy?:boolean;run?:string}};

/** The desktop roster, review/tests and action renderer at a phone's width. */
export function phoneActions(root:HTMLElement,request:Request,scope:()=>string,describe?:(run:Run)=>string,options:{history?:boolean}={}) {
  const history=options.history!==false;
  root.className='phone-actions';
  const work=el('section'),forge=el('section','turns-forge'),head=el('div','turns-head');
  const title=el('span','turns-title','Ходы'),count=el('span','turns-n');head.append(title,count);
  const list=el('div','turns'),message=el('p','muted');list.setAttribute('role','list');message.setAttribute('role','status');
  root.append(work,forge);
  if(history)root.append(head,list,message);
  const renderWork=workActivity(work,{reviewers:false});
  const teams=forgePanel(forge,async<T>(path:string,signal:AbortSignal,body?:unknown)=>{
    if(body!==undefined)throw Error('Управление Forge с этого экрана недоступно');
    return request<T>(path.replace('/api/forge/','/api/phone/forge/'),undefined,signal);
  },2500,{compact:true,readOnly:true,scope});
  const rows=new Map<string,ReturnType<typeof makeRow>>();
  let active=false,busy=false,epoch=0,timer:ReturnType<typeof setTimeout>|undefined;
  let controller:AbortController|undefined,owner=scope(),liveRun='';
  let activity:WorkActivity={};
  async function detail(row:ReturnType<typeof makeRow>,fresh=false) {
    if(!row.open||(!fresh&&row.version===row.run.updated_at&&row.version!==undefined))return;
    const captured=epoch,capturedScope=scope();
    try {
      const value=await request<RunDetail>('/api/run/'+encodeURIComponent(row.run.id),undefined,controller?.signal);
      if(!active||captured!==epoch||capturedScope!==scope()||!row.open||value.id!==row.run.id)return;
      row.paint(value);row.version=row.run.updated_at||row.run.id;
      const iterations=value.iterations||[];
      row.meta.textContent=`Шаги: ${iterations.length}`;
    }catch(error){if(active&&captured===epoch&&!row.body.firstChild)row.body.textContent=error instanceof Error?error.message:String(error)}
  }
  function makeRow(run:Run) {
    const node=el('article','turn'),head=document.createElement('button');head.type='button';head.className='turn-head';head.setAttribute('aria-expanded','false');
    const time=el('span','turn-time'),dot=el('span','turn-dot'),text=el('span','turn-text');
    const title=el('span','turn-title'),meta=el('span','turn-meta'),body=el('div','turn-body st-list');body.hidden=true;
    text.append(title,meta);head.append(time,dot,text);node.append(head,body);node.setAttribute('role','listitem');
    const row={node,head,title,time,meta,body,open:false,paint:mountActionSteps(body),version:undefined as string|undefined,run};
    head.addEventListener('click',()=>{row.open=!row.open;node.classList.toggle('open',row.open);body.hidden=!row.open;head.setAttribute('aria-expanded',String(row.open));if(row.open)void detail(row,true)});
    return row;
  }
  function paint(runs:Run[]) {
    const keep=new Set(runs.map(run=>run.id));
    for(const [id,row] of rows)if(!keep.has(id)&&!row.open){row.node.remove();rows.delete(id)}
    let cursor=list.firstChild;
    for(const run of runs) {
      let row=rows.get(run.id);if(!row){row=makeRow(run);rows.set(run.id,row)}
      row.run=run;row.time.textContent=fmtTime(run.created_at||'');
      row.title.textContent=describe?.(run)||run.goal_head||({chat_turn:'Сообщение',wake:'Пробуждение',task_window:'Работа'}[run.kind||'']||'Ход агента');
      const live=run.id===liveRun;
      row.node.dataset.status=live?'live':run.status==='failed'||run.terminal_status==='failed'?'failed':'done';
      if(cursor===row.node)cursor=cursor.nextSibling;else list.insertBefore(row.node,cursor);
      if(live&&!row.open){row.open=true;row.body.hidden=false;row.node.classList.add('open');row.head.setAttribute('aria-expanded','true')}
      if(row.open)void detail(row,live);
    }
    count.textContent=String(runs.length);message.textContent=runs.length?'':'Ходов пока нет.';
  }
  async function refresh() {
    if(!active||busy||!root.isConnected)return;
    if(owner!==scope()){epoch++;controller?.abort();owner=scope();rows.clear();list.replaceChildren();activity={};renderWork({})}
    busy=true;const captured=epoch;
    const capturedScope=scope();
    controller=new AbortController();
    const results=await Promise.allSettled([
      history?request<Run[]>('/api/runs?limit=40',undefined,controller.signal):Promise.resolve([] as Run[]),
      history?request<State>('/api/state',undefined,controller.signal):Promise.resolve({} as State),
      request<WorkActivity>('/api/phone/activity',undefined,controller.signal),
    ]);
    if(!active||captured!==epoch||capturedScope!==scope()){if(captured===epoch)busy=false;return}
    const [runs,state,work]=results;
    if(state.status==='fulfilled')liveRun=state.value.runner?.alive&&state.value.runner?.busy?state.value.runner.run||'':'';
    if(work.status==='fulfilled'){activity=work.value;renderWork(activity);teams.setReviewers(activity)}
    else {const stale={...activity,update:activity.update?{...activity.update,live:false,tests:activity.update.tests?{...activity.update.tests,live:false}:undefined}:undefined,reviewers:activity.reviewers?.map(row=>({...row,live:false}))};renderWork(stale);teams.setReviewers(stale)}
    if(history){if(runs.status==='fulfilled')paint(runs.value);else message.textContent='Не удалось обновить ходы. Уже прочитанные записи сохранены.';}
    busy=false;if(active)timer=setTimeout(()=>void refresh(),2500);
  }
  return {
    start(){if(active)return;active=true;epoch++;teams.start();void refresh()},
    stop(){active=false;epoch++;busy=false;clearTimeout(timer);controller?.abort();teams.stop()},
  };
}
