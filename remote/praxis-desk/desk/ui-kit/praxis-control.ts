import {el} from './dom';
type Request=<T>(path:string,body?:unknown,signal?:AbortSignal)=>Promise<T>;
type State={ok:boolean;available?:boolean;running?:boolean;stopped?:boolean;stop_requested?:boolean;note?:string};

export function mountPraxisControl(root:HTMLElement,request:Request) {
  const title=el('h2','','Управление Пракс'),note=el('p','muted','Проверяю состояние…');
  note.setAttribute('role','status');note.setAttribute('aria-live','polite');
  const actions=el('div','actions'),stop=el('button','chip','Остановить Пракс'),start=el('button','chip','Запустить Пракс');
  stop.type=start.type='button';stop.disabled=start.disabled=true;
  const help=el('p','muted','Остановка сразу завершает процесс Пракс и держится до запуска. История и результаты действий сохраняются.');
  actions.append(stop,start);root.replaceChildren(title,note,actions,help);
  let active=true,busy=false,timer:ReturnType<typeof setTimeout>|undefined;
  const controller=new AbortController();
  function paint(value:State) {
    note.textContent=value.note||'Состояние не получено.';
    note.className=value.ok?'muted':'err';
    stop.disabled=busy||!value.available||value.stopped===true;
    start.disabled=busy||!value.available||(value.running===true&&!value.stop_requested);
  }
  async function refresh() {
    if(!active||busy)return;
    try {const value=await request<State>('/api/praxis/control',undefined,controller.signal);if(active)paint(value)}
    catch {if(active)paint({ok:false,note:'Не удалось проверить управление Пракс. Обновлю состояние.'})}
    finally {if(active)timer=setTimeout(()=>void refresh(),1500)}
  }
  async function act(action:'start'|'stop') {
    if(busy||!active)return;
    busy=true;clearTimeout(timer);stop.disabled=start.disabled=true;
    note.textContent=action==='stop'?'Останавливаю Пракс…':'Запускаю Пракс…';
    try {const value=await request<State>('/api/praxis/control',{action},controller.signal);if(active){busy=false;paint(value)}}
    catch {if(active){busy=false;paint({ok:false,note:'Результат не подтверждён. Проверяю состояние перед повтором.'})}}
    finally {busy=false;if(active)timer=setTimeout(()=>void refresh(),500)}
  }
  stop.addEventListener('click',()=>void act('stop'));start.addEventListener('click',()=>void act('start'));
  void refresh();
  return ()=>{active=false;clearTimeout(timer);controller.abort()};
}
