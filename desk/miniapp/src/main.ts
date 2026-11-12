// Authenticate first, then load the exact desktop program and its settings.
import './styles.css';
import { mountTelegramShell, type TelegramWebApp } from './telegram-shell';

declare global { interface Window { Telegram?: {WebApp?:TelegramWebApp} } }
const tg=window.Telegram?.WebApp;
const base=(window.DESK_CONFIG_OVERRIDE||window.DESK_CONFIG)?.base||'';
if(location.search||location.hash)history.replaceState(null,'',location.pathname);
mountTelegramShell(tg);
const app=document.querySelector<HTMLElement>('#app')!;
app.hidden=true;
const login=document.createElement('section');login.className='program-login';
const title=document.createElement('h1');title.textContent='Hélène';
const note=document.createElement('p');
const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить вход';
login.append(title,note,retry);document.body.append(login);
let starting=false;
async function start() {
  if(starting)return;starting=true;retry.disabled=true;
  try {
    if(!tg?.initData)throw Error('Открой меню своего Telegram-бота или отправь /app.');
    note.textContent='Подключаю программу…';
    const response=await fetch(base+'/pair/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg.initData})});
    if(!response.ok)throw Error(response.status===403?'Вход не подтверждён. Открой меню своего бота заново.':'Программа пока не отвечает. Проверь подключение и повтори вход.');
    const paired=await response.json() as {key?:string;agent?:string};
    if(!paired.key)throw Error('Ключ сеанса не получен. Открой меню бота заново.');
    let selected=sessionStorage.getItem('helene.program.agent')||'';
    async function call(command:string,args:Record<string,unknown>) {
      const result=await fetch(base+'/api/program/invoke',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+paired.key},body:JSON.stringify({command,arguments:args,...(selected?{agent:selected}:{})})});
      if(!result.ok)throw Error((await result.text()).trim()||'Программа не ответила на действие.');
      return result.json();
    }
    async function invoke(command:string,args:Record<string,unknown>={}) {
      // Window and browser actions belong to the device showing the program.
      if(command==='restart_self'){
        if(args.full!==true){location.reload();return null}
        // The whole programme restarts on the computer through its own window;
        // this page reconnects once the new session can answer.
        await call('restart_self',{full:true});
        setTimeout(()=>location.reload(),8000);return null;
      }
      if(command==='voice_shortcuts')return {active:false,error:'В миниаппе сочетания работают внутри открытого окна.'};
      if(command==='voice_status'||command==='python_ready')return {ok:true};
      if(command==='open_path'&&/^https?:\/\//i.test(String(args.path||''))){window.open(String(args.path),'_blank','noopener,noreferrer');return null}
      if(command==='open_login_page') {
        const url=await invoke('relay_login_url',args);
        if(typeof url!=='string'||!url.startsWith('https://'))throw Error('Ссылка входа ещё не готова');
        window.open(url,'_blank','noopener,noreferrer');return null;
      }
      const value=await call(command,args);
      if(command==='switch_agent'){
        sessionStorage.setItem('helene.program.agent',value.agent_id);location.reload();
      }
      return value;
    }
    let channel;
    try{channel=await invoke('program_bootstrap')}
    catch(error){if(!selected)throw error;selected='';sessionStorage.removeItem('helene.program.agent');channel=await invoke('program_bootstrap')}
    selected=channel.agent_id;
    window.DESK_CONFIG_OVERRIDE={...channel,base:base+channel.base,key:paired.key,product:'Hélène'};
    const appearance=await invoke('appearance_read') as {values:Record<string,string>;seen?:string};
    for(const [key,value] of Object.entries(appearance.values||{}))localStorage.setItem(key,value);
    // Look and theme are the owner's preference, shared with the computer's window
    // under the same conflict control; panel widths and zoom stay this phone's layout.
    {
      const shared=['helene.look','frame.theme'],store=localStorage;
      const set=Storage.prototype.setItem,remove=Storage.prototype.removeItem;
      let seen=appearance.seen??null,pending=Promise.resolve();
      const accept=(result:{values?:Record<string,string>;seen?:string})=>{
        for(const name of shared){if(result.values&&name in result.values)set.call(store,name,result.values[name]);else remove.call(store,name);}
        if(result.seen)seen=result.seen;
        window.dispatchEvent(new CustomEvent('helene:appearance',{detail:result}));
      };
      const persist=(key:string,value:string|null)=>{
        if(!shared.includes(key))return;
        pending=pending.then(async()=>{
          try{
            const result=await invoke('appearance_patch',{seen,patch:{[key]:value}}) as {ok?:boolean;values?:Record<string,string>;seen?:string};
            if(result.ok===false)accept(result);else if(result.seen)seen=result.seen;
          }catch(error){console.error(error);}
        });
      };
      Storage.prototype.setItem=function(key:string,value:string){set.call(this,key,value);if(this===store)persist(String(key),String(value));};
      Storage.prototype.removeItem=function(key:string){remove.call(this,key);if(this===store)persist(String(key),null);};
    }
    const info=await invoke('app_info') as {platform?:string};
    Object.assign(window,{__HELENE__:{shell:'python',presentation:'miniapp',platform:info.platform||'',invoke,
      win(){/* Telegram owns this window. */}}});
    app.hidden=false;
    await import('../../app/src/main');
    login.remove();
    tg.BackButton.onClick(()=>{
      app.classList.remove('narrow-navigation','narrow-activity');
      history.back();
    });
  }catch(error){app.hidden=true;note.textContent=error instanceof Error?error.message:String(error);starting=false;retry.disabled=false}
}
retry.addEventListener('click',()=>void start());
void start();
