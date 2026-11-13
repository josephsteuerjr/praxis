import { praxisAPI } from './praxis-voice';
import { paperDialog, paperButton } from './paper-dialog';

type Service = { available:boolean; active_state:string; sub_state:string; pid:number };
type Reply = { accepted?:boolean; changed?:boolean; service?:Service };
type Action = 'start'|'stop';

export function mountXTTSService(root:HTMLElement):()=>void {
  const aborter=new AbortController();
  let disposed=false,busy=false,timer:number|undefined,state:Service|undefined;
  let pending:Action|undefined,pendingAt=0,readVersion=0,confirmation:HTMLDialogElement|undefined;
  root.innerHTML=`<h2 class="screen-title">Службы</h2><div class="task-row">
    <div class="task-head"><b>XTTS</b> <span data-service-state role="status">Проверяю…</span></div>
    <p class="muted">Голосовая модель на сервере. Запуск службы и включение озвучки управляются отдельно.</p>
    <div class="actions"><button type="button" class="chip" data-service-start disabled>Запустить XTTS</button>
    <button type="button" class="chip" data-service-stop disabled>Остановить XTTS</button>
    <button type="button" class="chip" data-service-refresh>Обновить состояние</button></div>
    <p class="muted" data-service-note role="status" aria-live="polite"></p></div>`;
  const start=root.querySelector<HTMLButtonElement>('[data-service-start]')!;
  const stop=root.querySelector<HTMLButtonElement>('[data-service-stop]')!;
  const status=root.querySelector<HTMLElement>('[data-service-state]')!;
  const note=root.querySelector<HTMLElement>('[data-service-note]')!;
  const labels:Record<string,string>={active:'Работает',inactive:'Остановлена',activating:'Запускается…',deactivating:'Останавливается…',failed:'Ошибка службы'};
  function paint() {
    if(disposed)return;
    const value=state?.active_state;
    if(pending&&((pending==='start'&&value==='active')||(pending==='stop'&&value==='inactive'))) {
      note.textContent=pending==='start'?'Служба запущена. Голосовая модель может ещё прогреваться.':'Служба остановлена.';
      pending=undefined;
    }
    status.textContent=state?.available?(labels[value||'']||'Состояние не подтверждено'):'Состояние недоступно';
    if(pending)status.textContent=Date.now()-pendingAt>30000?'Результат пока не подтверждён':pending==='start'?'Запуск запрошен…':'Остановка запрошена…';
    const waiting=!!pending&&Date.now()-pendingAt<30000;
    start.disabled=busy||waiting||!state?.available||value==='active'||value==='activating'||value==='deactivating';
    stop.disabled=busy||waiting||!state?.available||!['active','activating'].includes(value||'');
  }
  async function refresh() {
    const version=++readVersion;
    try {
      const result=await praxisAPI<Service>('/api/xtts/service',undefined,aborter.signal);
      if(disposed||version!==readVersion)return;state=result;paint();
    } catch {
      if(disposed||version!==readVersion)return;state=undefined;paint();note.textContent='Не удалось проверить службу. Обновите состояние.';
    }
  }
  async function confirmStop():Promise<boolean> {
    const view=paperDialog('Остановить службу XTTS?');confirmation=view.dialog;
    view.body.textContent='Текущая генерация звука прервётся. Выбранный голос сохранится.';
    let accepted=false;
    view.footer.append(paperButton('Отмена',()=>view.dialog.close()),paperButton('Остановить XTTS',()=>{accepted=true;view.dialog.close()},true));
    return new Promise(resolve=>view.dialog.addEventListener('close',()=>{confirmation=undefined;resolve(accepted)},{once:true}));
  }
  async function change(action:Action) {
    if(busy||disposed)return;busy=true;paint();
    try {
      if(action==='stop'&&!await confirmStop())return;
      if(disposed)return;
      readVersion++;
      note.textContent='Передаю просьбу…';
      const result=await praxisAPI<Reply>('/api/xtts/service',{action},aborter.signal);
      if(disposed)return;
      readVersion++;
      pending=action;pendingAt=Date.now();if(result.service)state=result.service;
      note.textContent='Просьба принята. Проверяю состояние службы…';paint();
      await refresh();
    } catch {
      if(!disposed){state=undefined;paint();note.textContent='Результат передачи не подтверждён. Обновите состояние перед повтором.';}
    } finally {busy=false;paint();}
  }
  start.addEventListener('click',()=>void change('start'));
  stop.addEventListener('click',()=>void change('stop'));
  root.querySelector('[data-service-refresh]')!.addEventListener('click',()=>void refresh());
  const poll=async()=>{if(disposed)return;await refresh();if(!disposed)timer=window.setTimeout(poll,5000)};
  void poll();
  return ()=>{disposed=true;aborter.abort();if(timer!==undefined)clearTimeout(timer);confirmation?.close()};
}

export function installXTTSServiceControls():()=>void {
  let node:HTMLElement|undefined,dispose:(()=>void)|undefined,scheduled=false;
  const reconcile=()=>{
    scheduled=false;
    const heading=document.querySelector('#screen .system-heading');
    const active=!!document.querySelector('[data-tab="system"][aria-current="true"]');
    if(node&&(!node.isConnected||!active)){dispose?.();node.remove();node=undefined;dispose=undefined;}
    if(active&&heading&&!node){node=document.createElement('section');node.id='praxis-xtts-service';node.setAttribute('aria-label','Служба XTTS');heading.after(node);dispose=mountXTTSService(node);}
  };
  const observer=new MutationObserver(()=>{if(!scheduled){scheduled=true;queueMicrotask(reconcile)}});
  observer.observe(document.querySelector('#app')||document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['aria-current']});
  reconcile();return ()=>{observer.disconnect();dispose?.();node?.remove()};
}
