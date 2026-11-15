import { el } from './dom';
import { fmtTime, runTitle, runControlNote } from './text';
import { t as uiText } from '../lang/lang';
import { forgePanel } from './window/views/forge-panel';
import { mountActionSteps } from './window/panel';
import { workActivity,type WorkActivity } from './window/work-activity';
import type { RunDetail } from './steps';
import './window/views/forge-panel.css';
import './window/styles/turns.css';
import './window/styles/work-activity.css';

type Run={id:string;kind?:string;operator?:boolean;status?:string;terminal_status?:string;created_at?:string;updated_at?:string;goal_head?:string;display_title?:unknown};
type Request=<T>(path:string,body?:unknown,signal?:AbortSignal)=>Promise<T>;
type State={runner?:{alive?:boolean;busy?:boolean;run?:string}};
const PAGE_SIZE=40;

/** Use runner ownership when available, otherwise the freshest running manifest. */
export function currentRunId(runs:Run[],state:State,now=Date.now()):string {
  if(state.runner?.alive)return state.runner.busy ? state.runner.run || '' : '';
  return runs.filter(r=>r.status==='running').filter(r=>{const age=now-Date.parse(r.updated_at || '');return Number.isFinite(age)&&age>=-5000&&age<600000})
    .sort((a,b)=>Date.parse(b.updated_at!)-Date.parse(a.updated_at!))[0]?.id || '';
}

/** The desktop roster, review/tests and action renderer at a phone's width. */
export function phoneActions(root:HTMLElement,request:Request,scope:()=>string,describe?:(run:Run)=>string,options:{history?:boolean;agentsOnly?:boolean;onReset?:(id:string,status:string)=>void}={}) {
  const history=options.history!==false;
  root.className='phone-actions';
  const work=el('section'),forge=el('section','turns-forge'),head=el('div','turns-head');
  const title=el('span','turns-title',uiText('phone.actions.history')),count=el('span','turns-n');head.append(title,count);
  const list=el('div','turns'),message=el('p','muted');list.setAttribute('role','list');message.setAttribute('role','status');
  const current=el('section','phone-current'),currentList=el('div','turns'),currentMessage=el('p','muted',uiText('phone.actions.current_loading'));
  currentMessage.setAttribute('role','status');
  current.append(el('h2','phone-current-heading',uiText('phone.actions.current')),currentMessage,currentList);
  const previous=document.createElement('details');previous.className='phone-history';
  const more=document.createElement('button');more.type='button';more.className='btn phone-history-more';more.textContent=uiText('phone.actions.more');more.hidden=true;
  more.addEventListener('click',()=>void loadOlder());
  const previousSummary=document.createElement('summary');previousSummary.append(title,document.createTextNode(' · '),count);previous.append(previousSummary,list,message,more);
  const agents=document.createElement('details');agents.className='phone-agents';
  agents.append(el('summary','',uiText('phone.actions.agents')),forge);
  root.append(work);if(!options.agentsOnly)root.append(current);
  if(history&&!options.agentsOnly)root.append(previous);
  const operators=document.createElement('details');operators.className='phone-operators muted';
  const operatorList=el('div','turns'),operatorMessage=el('p','muted'),operatorMore=el('button','btn',uiText('phone.actions.more_checks'));
  operatorList.setAttribute('role','list');operatorMessage.setAttribute('role','status');operatorMore.type='button';operatorMore.hidden=true;
  operators.append(el('summary','',uiText('phone.actions.operators')),operatorList,operatorMessage,operatorMore);
  if(history&&!options.agentsOnly)root.append(operators);
  root.append(options.agentsOnly?forge:agents);
  const renderWork=workActivity(work,{reviewers:false});
  const teams=forgePanel(forge,async<T>(path:string,signal:AbortSignal,body?:unknown)=>{
    if(body!==undefined)throw Error(uiText('phone.actions.forge_readonly'));
    const value=await request<T>(path.replace('/api/forge/','/api/phone/forge/'),undefined,signal);
    // `open` меняется сразу по щелчку, а `toggle` приходит позже: ответ между ними не должен
    // заполнить уже закрытый список. Остановка панели делает его ответ недействительным.
    if(!options.agentsOnly&&!agents.open)teams.stop();
    return value;
  },2500,{compact:true,readOnly:true,scope});
  const rows=new Map<string,ReturnType<typeof makeRow>>();
  let active=false,busy=false,epoch=0,timer:ReturnType<typeof setTimeout>|undefined;
  let controller:AbortController|undefined,owner=scope(),liveRun='';
  let activity:WorkActivity={};
  let currentState:State={};let lastRuns:Run[]=[];
  let resetMessage='';
  let olderController:AbortController|undefined,olderBusy=false,pagingStarted=false,hasMore=false,before='',historyError='';
  let operatorController:AbortController|undefined,operatorBusy=false,operatorBefore='',operatorEpoch=0;
  const operatorRows=new Map<string,ReturnType<typeof makeRow>>();
  async function loadOperators(older=false) {
    if(!active||operatorBusy||!operators.open)return;
    const captured=epoch,capturedScope=scope(),requestEpoch=++operatorEpoch;operatorBusy=true;operatorMore.disabled=true;
    operatorMessage.textContent=uiText('phone.actions.checks_loading');operatorController=new AbortController();
    try {
      const page=await request<Run[]>(`/api/runs?audience=operator&limit=${PAGE_SIZE+1}${older?'&before='+encodeURIComponent(operatorBefore):''}`,undefined,operatorController.signal);
      if(!active||captured!==epoch||capturedScope!==scope()||!operators.open||requestEpoch!==operatorEpoch)return;
      if(!Array.isArray(page)||page.some(r=>!r.operator||(older&&r.id>=operatorBefore)))throw Error(uiText('phone.actions.checks_invalid'));
      if(!older){operatorRows.clear();operatorList.replaceChildren()}
      const shown=page.slice(0,PAGE_SIZE);
      for(const run of shown){
        let row=operatorRows.get(run.id);
        if(!row){row=makeRow(run);operatorRows.set(run.id,row);operatorList.append(row.node)}
        row.run=run;row.title.textContent=runTitle(run)||uiText('run.title.operator_short');row.time.textContent=fmtTime(run.created_at||'');
        row.node.dataset.status=run.status==='failed'?'failed':'done';
        const controls=row.node.querySelector<HTMLElement>('.actions');if(controls)controls.hidden=true;
      }
      operatorBefore=shown.at(-1)?.id||operatorBefore;operatorMore.hidden=page.length<=PAGE_SIZE;
      operatorMessage.textContent=operatorRows.size?'':uiText('phone.actions.checks_empty');
    }catch{if(active&&captured===epoch&&operators.open&&requestEpoch===operatorEpoch){operatorMessage.textContent=uiText('phone.actions.checks_failed')}}
    finally{if(captured===epoch&&requestEpoch===operatorEpoch){operatorBusy=false;operatorMore.disabled=false}}
  }
  operators.addEventListener('toggle',event=>{if(event.target!==operators)return;if(operators.open)void loadOperators();else {operatorEpoch++;operatorBusy=false;operatorController?.abort()}});
  operatorMore.addEventListener('click',()=>void loadOperators(true));
  agents.addEventListener('toggle',event=>{
    if(event.target!==agents)return;
    if(active&&agents.open)teams.start();else teams.stop();
  });
  async function detail(row:ReturnType<typeof makeRow>,fresh=false) {
    if(!row.open||(!fresh&&row.version===row.run.updated_at&&row.version!==undefined))return;
    const captured=epoch,capturedScope=scope(),requestVersion=++row.requestVersion;
    try {
      const value=await request<RunDetail>('/api/run/'+encodeURIComponent(row.run.id),undefined,controller?.signal);
      if(!active||captured!==epoch||capturedScope!==scope()||!row.open||value.id!==row.run.id||requestVersion!==row.requestVersion)return;
      row.paint(value);row.version=row.run.updated_at||row.run.id;
      const iterations=value.iterations||[];
      row.meta.textContent=uiText('phone.actions.steps',{count:String(iterations.length)});
    }catch(error){if(active&&captured===epoch&&!row.body.firstChild)row.body.textContent=error instanceof Error?error.message:String(error)}
  }
  function makeRow(run:Run) {
    const node=el('article','turn'),head=document.createElement('button');head.type='button';head.className='turn-head';head.setAttribute('aria-expanded','false');
    const time=el('span','turn-time'),dot=el('span','turn-dot'),text=el('span','turn-text');
    const title=el('span','turn-title'),meta=el('span','turn-meta'),body=el('div','turn-body st-list');body.hidden=true;
    text.append(title,meta);head.append(time,dot,text);node.append(head,body);node.setAttribute('role','listitem');
    const controls=el('div','actions'),cancel=el('button','chip',uiText('phone.actions.reset')),notice=el('p','muted');
    cancel.type='button';cancel.title=uiText('phone.actions.reset_hint');
    notice.setAttribute('role','status');controls.append(cancel);node.insertBefore(controls,body);node.insertBefore(notice,body);
    const row={node,head,title,time,meta,body,open:false,autoOpened:false,requestVersion:0,paint:mountActionSteps(body),version:undefined as string|undefined,run};
    cancel.addEventListener('click',async()=>{
      if(cancel.disabled)return;
      const captured=epoch,capturedScope=scope();cancel.disabled=true;notice.textContent=uiText('phone.actions.reset_wait');
      try {
        const result=await request<{ok:boolean;confirmed?:boolean;note?:string;display_note?:unknown;receipt?:{status?:string}}>(`/api/run/${encodeURIComponent(row.run.id)}/cancel`,{});
        if(!active||captured!==epoch||capturedScope!==scope())return;
        notice.textContent=runControlNote(result)||result.note||uiText('phone.actions.reset_unknown');
        if(!result.confirmed)cancel.disabled=false;
        else {
          const status=result.receipt?.status||'cancelled',id=row.run.id;
          if(liveRun===id){liveRun='';resetMessage=notice.textContent;notice.textContent='';}
          row.run={...row.run,status};row.version=undefined;
          lastRuns=lastRuns.map(run=>run.id===id?row.run:run);
          if(currentState.runner?.run===id)currentState={runner:{...currentState.runner,busy:false,run:''}};
          options.onReset?.(id,status);paint(lastRuns);
        }
      } catch {if(active&&captured===epoch){notice.textContent=uiText('phone.actions.reset_error');cancel.disabled=false}}
    });
    Object.assign(row,{cancel,controls});
    head.addEventListener('click',()=>{row.open=!row.open;node.classList.toggle('open',row.open);body.hidden=!row.open;head.setAttribute('aria-expanded',String(row.open));if(row.open)void detail(row,true)});
    return row;
  }
  function paint(runs:Run[]) {
    const keep=new Set(runs.map(run=>run.id));
    for(const [id,row] of rows)if(!keep.has(id)){row.node.remove();rows.delete(id)}
    let historyCount=0;
    for(const run of runs) {
      let row=rows.get(run.id);if(!row){row=makeRow(run);rows.set(run.id,row)}
      row.run=run;row.time.textContent=fmtTime(run.created_at||'');
      const controls=row.node.querySelector<HTMLElement>('.actions');
      if(controls)controls.hidden=['done','failed','cancelled'].includes(run.status||run.terminal_status||'');
      row.title.textContent=describe?.(run)||runTitle(run)||uiText(run.kind==='chat_turn'?'run.title.message':run.kind==='wake'?'run.title.wake':run.kind==='task_window'?'run.title.task':'run.title.turn');
      const live=run.id===liveRun;
      row.node.dataset.status=live?'live':run.status==='failed'||run.terminal_status==='failed'?'failed':run.status==='running'?'unknown':'done';
      const parent=live?currentList:list;if(row.node.parentElement!==parent)parent.append(row.node);
      if(!live){const expected=list.children[historyCount++];if(expected!==row.node)list.insertBefore(row.node,expected||null);}
      if(live&&!row.autoOpened){row.autoOpened=true;row.open=true;row.body.hidden=false;row.node.classList.add('open');row.head.setAttribute('aria-expanded','true')}
      if(row.open)void detail(row,live);
    }
    count.textContent=String(historyCount)+(hasMore?'+':'');message.textContent=historyError||(historyCount?'':uiText('phone.actions.empty'));
    more.hidden=!hasMore;more.disabled=olderBusy;more.textContent=olderBusy?uiText('phone.actions.loading'):uiText('phone.actions.more');
    if(liveRun)resetMessage='';
    currentMessage.textContent=liveRun?'':resetMessage||uiText('phone.actions.no_current');
  }
  function mergeRuns(incoming:Run[]) {
    const merged=new Map(lastRuns.map(run=>[run.id,run]));
    for(const run of incoming)merged.set(run.id,run);
    lastRuns=[...merged.values()].sort((a,b)=>a.id===b.id?0:a.id>b.id?-1:1);
  }
  async function loadOlder() {
    if(!active||olderBusy||!hasMore||!before)return;
    const captured=epoch,capturedScope=scope(),cursor=before;
    olderBusy=true;pagingStarted=true;historyError='';olderController=new AbortController();paint(lastRuns);
    try {
      const page=await request<Run[]>(`/api/runs?limit=${PAGE_SIZE+1}&before=${encodeURIComponent(cursor)}`,undefined,olderController.signal);
      if(!active||captured!==epoch||capturedScope!==scope())return;
      if(!Array.isArray(page)||page.some(run=>!run.id||run.id>=cursor))throw Error(uiText('phone.actions.page_invalid'));
      const rows=page.slice(0,PAGE_SIZE);mergeRuns(rows);hasMore=page.length>PAGE_SIZE;before=rows.at(-1)?.id||cursor;
    }catch(error){if(active&&captured===epoch&&capturedScope===scope())historyError=uiText('phone.actions.page_failed')}
    finally{if(captured===epoch){olderBusy=false;more.disabled=false;if(active&&capturedScope===scope())paint(lastRuns)}}
  }
  async function refresh() {
    if(!active||busy||!root.isConnected)return;
    if(owner!==scope()){epoch++;controller?.abort();olderController?.abort();operatorController?.abort();operatorBusy=false;operatorRows.clear();operatorList.replaceChildren();operators.open=false;operatorBefore='';operatorMore.hidden=true;operatorMessage.textContent='';olderBusy=false;pagingStarted=false;hasMore=false;before='';historyError='';resetMessage='';more.hidden=true;owner=scope();rows.clear();list.replaceChildren();currentList.replaceChildren();lastRuns=[];currentState={};liveRun='';activity={};renderWork({});if(!options.agentsOnly){agents.open=false;teams.stop()}}
    busy=true;const captured=epoch;
    const capturedScope=scope();
    controller=new AbortController();
    const results=await Promise.allSettled([
      !options.agentsOnly?request<Run[]>(`/api/runs?limit=${PAGE_SIZE+1}`,undefined,controller.signal):Promise.resolve([] as Run[]),
      !options.agentsOnly?request<State>('/api/state',undefined,controller.signal):Promise.resolve({} as State),
      request<WorkActivity>('/api/phone/activity',undefined,controller.signal),
    ]);
    if(!active||captured!==epoch||capturedScope!==scope()){if(captured===epoch)busy=false;return}
    const [runs,state,work]=results;
    currentState=state.status==='fulfilled'?state.value:{};
    if(runs.status==='fulfilled'){
      const head=runs.value.slice(0,PAGE_SIZE);mergeRuns(head);liveRun=currentRunId(head,currentState);
      if(!head.some(run=>run.id===liveRun))liveRun='';
      if(!pagingStarted){hasMore=runs.value.length>PAGE_SIZE;before=lastRuns.at(-1)?.id||'';}
    }
    if(work.status==='fulfilled'){activity=work.value;renderWork(activity);teams.setReviewers(activity)}
    else {const stale={...activity,update:activity.update?{...activity.update,live:false,tests:activity.update.tests?{...activity.update.tests,live:false}:undefined}:undefined,reviewers:activity.reviewers?.map(row=>({...row,live:false}))};renderWork(stale);teams.setReviewers(stale)}
    if(!options.agentsOnly){if(runs.status==='fulfilled')paint(lastRuns);else {message.textContent=uiText('phone.actions.update_failed');currentMessage.textContent=uiText('phone.actions.current_failed');}}
    busy=false;if(active)timer=setTimeout(()=>void refresh(),2500);
  }
  return {
    start(){if(active)return;active=true;epoch++;if(options.agentsOnly||agents.open)teams.start();void refresh();if(operators.open)void loadOperators()},
    stop(){active=false;epoch++;busy=false;olderBusy=false;operatorBusy=false;more.disabled=false;clearTimeout(timer);controller?.abort();olderController?.abort();operatorController?.abort();teams.stop()},
  };
}
