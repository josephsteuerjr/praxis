import { api, cfg, electron, inTauri, post, shell } from "./api";
import { button, el, field, toggle } from "../dom";
import { humanError } from "./lib";
import { phoneCard, type Config, type Loaded } from "./views/settings-frame";
import { collectConnection } from "./server-connection";
import { SERVER_TRANSFER_AVAILABLE, SERVER_TRANSFER_NOTICE } from './feature-availability';
import { sshAccessForm } from './server-access-form';
import { serverBridgeCard } from './server-settings';
import { useBridge } from './server-bridge-config';

let qrRenderer: (value:string)=>Promise<string> = async()=>{throw new Error("Не удалось подготовить QR. Обнови приложение.");};
let transferBusy=false;
export function setGuideQrRenderer(render:(value:string)=>Promise<string>) { qrRenderer=render; }

interface Session { config:Config; stamp?:string; remote:boolean }
async function load():Promise<Session> {
  if (inTauri) {
    const data=await shell<Loaded>("config_load");
    if (data.config.mode!=="remote") return {config:data.config,stamp:String(data.mtime_ns||""),remote:false};
  }
  const data=await api<{ok:boolean;why:string;config:Config;mtime_ns?:string}>("/api/agent-config");
  if (!data.ok) throw new Error(data.why);
  return {config:data.config,stamp:data.mtime_ns,remote:true};
}
async function save(patch:Partial<Config>, session:Session) {
  const fresh=await load();
  if(fresh.remote!==session.remote||fresh.stamp!==session.stamp)throw new Error("Настройки уже изменились. Открой этот шаг заново: новые значения не будут затёрты.");
  const next:Config={...fresh.config};
  for (const name of Object.keys(patch)) {
    const before=next[name]; const value=patch[name];
    next[name]=before && typeof before==="object" && value && typeof value==="object" ? {...before,...value} : value;
  }
  const reply= fresh.remote
    ? await post<{ok:boolean;error?:string}>("/api/agent-config",{config:Object.fromEntries(Object.keys(patch).map(key=>[key,next[key]])),mtime_ns:fresh.stamp})
    : await shell<{ok?:boolean;error?:string;code?:string}>("config_save",{config:JSON.stringify(next),mtimeNs:fresh.stamp});
  if (reply?.ok===false) throw new Error(reply.error||"Настройки изменились. Перечитай их и повтори.");
  Object.assign(session,await load());
}
// Transfer and return replies are composed by our own code in Russian: they ARE the reason.
// humanError would flatten them to «Не получилось.»; raw exceptions still go through it.
function reason(error:unknown):string {
  const raw=(error instanceof Error?error.message:String(error??"")).trim();
  if(/[А-Яа-яЁё]/.test(raw)&&!/^[A-Za-z_][A-Za-z_0-9]*(Error|Exception):|Traceback|File "/.test(raw))return raw;
  return humanError(error).text;
}
function receipt(box:HTMLElement,text:string,error=false) {
  box.className="receipt"+(error?" err":" ok"); box.textContent=text;
}
function steps(items:string[]) {
  const list=el("ol","field-hint"); items.forEach(text=>list.append(el("li","",text))); return list;
}
async function restart(box:HTMLElement) {
  const reply=await post<{ok:boolean;note?:string}>("/api/supervisor/restart",{target:"runner"});
  if (!reply.ok) throw new Error(reply.note||"Не удалось перезапустить агента.");
  receipt(box,"Настройки сохранены. Агент перезапускается; дождись статуса «На связи».");
}

export function mountConnectionGuide(host:HTMLElement) {
  const goals=[
    ["phone","Mini App Telegram","Меню твоего бота открывает агента по текущему адресу. Вручную переносить ссылку не нужно."],
    ["telegram","Telegram без VPN","Прокси помогает агенту подключиться к Telegram. Это отдельная настройка связи."],
    ["bridge","Серверный мост","Настрой прокси и постоянный HTTPS. Агент остаётся на этом компьютере."],
    ["server","Агент на сервере","Перенеси агента, чтобы он работал и при выключенном компьютере."],
  ];
  const tabs=el("div","choice images-options connection-goals"); tabs.setAttribute("role","group"); tabs.setAttribute("aria-label","Что подключить");
  const content=el("div","guide-content"); host.append(tabs,content);
  let serial=0;
  const pick=async(kind:string)=>{
    if(transferBusy)return;
    const current=++serial;
    tabs.querySelectorAll("button").forEach(node=>node.setAttribute("aria-pressed",String(node.dataset.goal===kind)));
    content.replaceChildren(el("p","field-hint","Проверяю текущие настройки…"));
    try {
      const native=(kind==="server"||kind==="bridge")&&inTauri?await shell<Loaded>("config_load"):null;
      const session:Session=native?{config:native.config,stamp:String(native.mtime_ns||""),remote:native.config.mode==="remote"}:await load();
      if(current!==serial||!host.isConnected)return;
      content.replaceChildren();
      if(kind==="phone")drawPhone(content,session,()=>void pick(kind));
      else if(kind==="telegram")drawTelegram(content,session);
      else if(kind==="bridge")drawBridge(content);
      else drawServer(content,session);
    } catch(error) {
      if(current!==serial||!host.isConnected)return;
      const out=el("p","receipt err",humanError(error).text);
      content.replaceChildren(out,button("Повторить","quiet",()=>void pick(kind)));
    }
  };
  for(const [kind,title,text] of goals) {
    const node=button("","quiet",()=>void pick(kind)); node.className="choice-item";node.dataset.goal=kind;
    node.append(el("span","choice-title",title),el("span","choice-text",text)); tabs.append(node);
  }
  void pick("phone");
}
function drawBridge(host:HTMLElement) {
  if(!inTauri){host.append(el('p','field-hint','Настрой серверный мост из программы Hélène.'));return;}
  const note=el('p','receipt');const restartButton=button('Перезапустить движок','quiet',async()=>{
    try{await restart(note);restartButton.hidden=true;}catch(error){receipt(note,humanError(error).text,true);}
  });restartButton.hidden=true;
  host.append(serverBridgeCard(async bridge=>{
    const session=await load();
    if(session.remote)throw new Error('Выбери местного агента: этот мост подключает программу на твоём компьютере.');
    const next=structuredClone(session.config);useBridge(next,bridge);
    const patch:Partial<Config>={};if(bridge.external)patch.phone=next.phone;
    if(bridge.ports.local_proxy_port)patch.network=next.network;
    await save(patch,session);restartButton.hidden=!bridge.ports.local_proxy_port;
    return restartButton.hidden?'Подключение сохранено. Hélène подготавливает вход с телефона.':'Подключение сохранено. Перезапусти движок кнопкой ниже, когда текущая работа закончится.';
  }),note,restartButton);
}
/** Daily server operations stay available after onboarding has finished. */
export async function mountServerManagement(host:HTMLElement) {
  const source=await shell<Loaded>('config_load');
  if(!host.isConnected)return;
  host.replaceChildren();drawServer(host,{config:source.config,stamp:String(source.mtime_ns||''),remote:source.config.mode==='remote'});
}
function drawPhone(host:HTMLElement,session:Session,reload:()=>void) {
  host.append(steps(["Подключи своего бота и укажи id владельца в карточке Telegram. Для каждого локального агента используй отдельного бота.",session.remote?"Mini App подключится к тому же агенту на сервере.":"Включи Mini App: Hélène подготовит подключение и обновит меню бота.","Открой меню бота или отправь /app. Бот выдаст свежую кнопку. Кнопки в старых сообщениях сохраняют прежний адрес.",session.remote?"Сервер должен оставаться включённым.":"Компьютер должен оставаться включённым и подключённым к интернету."]));
  const config=structuredClone(session.config);
  const card=phoneCard(config,!!config.phone?.enabled,config.phone?.mode||"",session.remote?cfg.base:"",qrRenderer,{showToggle:false});
  const out=el("p","receipt");
  if(!session.remote) {
    const apply=button(config.phone?.enabled?"Переподготовить подключение":"Подключить телефон","primary",async()=>{
      apply.disabled=true;receipt(out,"Подготавливаю подключение…");
      try{
        const patch:Partial<Config>={phone:{...config.phone,enabled:true,mode:config.phone?.mode||'automatic'}};
        if(config.network?.bridge_profile)patch.network=config.network;
        await save(patch,session);reload();
      }
      catch(error){receipt(out,humanError(error).text,true);apply.disabled=false;}
    });
    host.append(apply,out);
    if(config.phone?.enabled)host.append(button("Выключить подключение телефона","quiet",async()=>{
      try{await save({phone:{...config.phone,enabled:false}},session);reload();}
      catch(error){receipt(out,humanError(error).text,true);}
    }));
  }
  host.append(card);
}
function drawTelegram(host:HTMLElement,session:Session) {
  const tg={...session.config.telegram};const proxy={...tg.proxy};
  const out=el("div","guide-checks");const note=el("p","receipt");
  const owner=el("div","form-grid two");
  const token=field("Токен своего бота",String(tg.bot_token||""),value=>tg.bot_token=value,{type:"password",hint:"Открой @BotFather в Telegram, создай своего бота и скопируй выданный токен сюда."});
  const id=field("Твой Telegram ID",String(tg.owner_id||""),value=>tg.owner_id=value,{hint:"Открой @userinfobot в Telegram и нажми «Начать»: скопируй число Id. Оно нужно, чтобы миниапп пускал только тебя."});
  owner.append(token,id);
  const proxyFields=el("div","form-grid two");proxyFields.hidden=!proxy.enabled;
  proxyFields.append(field("Адрес подключения",String(proxy.url||""),value=>proxy.url=value,{placeholder:"https://…/helene/telegram",hint:"Адрес уже подготовленного подключения Hélène. Обычный SOCKS/MTProxy сюда не подходит."}),field("Ключ подключения",String(proxy.key||""),value=>proxy.key=value,{type:"password",hint:"Ключ выдаётся вместе с адресом; бот-токен сюда вставлять не нужно."}));
  const useProxy=toggle("Подключаться к Telegram через прокси",proxy.enabled===true,value=>{proxy.enabled=value;proxyFields.hidden=!value;});
  host.append(steps(["Подключи своего бота: вставь токен и свой Telegram ID. Если они уже указаны, заново вводить их не нужно.","Если Telegram работает через VPN или напрямую, прокси можно не включать. Для работы без VPN укажи адрес и ключ подготовленного подключения.","Нажми «Проверить связь». Фото и стикеры проверяются отдельно: старый шлюз может пропускать сообщения и не передавать файлы."]),owner,useProxy,proxyFields);
  const controls=el("div","actions");
  const check=button("Проверить связь","quiet",async()=>{
    check.disabled=true;receipt(note,"Проверяю связь…");
    try{
      const result=await post<{ok:boolean;bot_url?:string;checks:Array<{name:string;ok:boolean;message:string}>}>("/api/telegram/check",{telegram:{...tg,proxy}});
      out.replaceChildren(...result.checks.map(item=>el("p",item.ok?"receipt ok":"receipt err",item.name+": "+item.message)));
      if(result.bot_url){const link=el("a","btn btn-quiet","Открыть своего бота");link.href=result.bot_url;link.target="_blank";link.rel="noopener noreferrer";out.append(link);}
      receipt(note,result.ok?"Связь готова. Теперь сохрани настройки.":"Выше показано, что осталось настроить.",!result.ok);
    }catch(error){receipt(note,humanError(error).text,true);}finally{check.disabled=false;}
  });
  const apply=button("Сохранить и подключить","primary",async()=>{
    apply.disabled=true;
    try{
      receipt(note,"Проверяю данные перед сохранением…");
      const result=await post<{checks:Array<{name:string;ok:boolean;message:string}>}>("/api/telegram/check",{telegram:{...tg,proxy}});
      const blockers=result.checks.filter(row=>!row.ok&&row.name!=="Фото и стикеры");
      if(blockers.length)throw new Error(blockers.map(row=>row.message).join(" "));
      await save({telegram:{...tg,bot_token:String(tg.bot_token||"").trim(),proxy:{...proxy,url:String(proxy.url||"").trim(),key:String(proxy.key||"").trim()}}},session);await restart(note);
    }catch(error){receipt(note,humanError(error).text,true);}finally{apply.disabled=false;}
  });
  controls.append(check,apply);host.append(controls,note,out);
  if(tg.mode==="account")host.prepend(el("p","field-hint","Сейчас агент подключён как аккаунт Telegram. Не заменяй его ботом, если хочешь оставить этот режим. Адрес и ключ прокси ниже подходят и ему."));
  if(tg.bot_token||proxy.enabled)void check.click();
}
function drawServer(host:HTMLElement,session:Session) {
  host.append(steps(["Подготовь перенос: архив сохранит память, навыки, настройки и входы агента.","Укажи сервер. Приложение поможет перенести агента; исходная память останется на этом компьютере.","После проверки подключения окно будет работать с агентом на сервере. Местная копия останется остановленной, чтобы два агента не отвечали одним ботом."]));
  if(!inTauri){host.append(el("p","field-hint","Для переноса с компьютера открой этот шаг в установленном приложении Hélène."));return;}
  const buttons=el("div","actions");const body=el("div","server-move-guide");
  if(!session.remote)buttons.append(button("Новый сервер","primary",()=>{if(!transferBusy)drawNewServer(body);}));
  buttons.append(button("Сервер уже работает","quiet",()=>{if(!transferBusy)drawExistingServer(body);}));
  buttons.append(button("Новый агент на сервере","quiet",()=>{if(!transferBusy)drawFreshServer(body);}));
  buttons.append(button("Обновить сервер","quiet",()=>{if(!transferBusy)drawServerUpdate(body);}));
  const back=button("Вернуть агента с сервера","quiet",()=>{if(!transferBusy)drawReturn(body);});
  back.hidden=!session.remote;buttons.append(back);
  void shell<{pending?:boolean;return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"})
    .then(result=>{if(result.pending&&!result.return?.returned)back.hidden=false;}).catch(()=>{});
  if(session.remote)buttons.append(button("Открыть местную копию","quiet",async()=>{
    const out=el("p","receipt");body.replaceChildren(out);
    try{
      receipt(out,"Открываю сохранённую местную копию. После переноса она остановлена; сервер продолжит работать.");
      const fresh=await shell<Loaded>("config_load");
      const reply=await shell<{ok?:boolean;error?:string}>("config_save",{config:JSON.stringify({...fresh.config,mode:"local",server_locator:undefined}),mtimeNs:String(fresh.mtime_ns||"")});
      if(reply?.ok===false)throw new Error(reply.error||"Настройки не записались");
      await shell("restart_self");
    }catch(error){receipt(out,humanError(error).text,true);}
  }));
  host.append(buttons,body);if(session.remote)drawExistingServer(body);else drawNewServer(body);
}

async function attachServer(base:string,key:string,source:Loaded,locator?:string) {
  const fresh=await shell<Loaded>("config_load");
  if(fresh.path!==source.path)throw new Error("В окне выбран другой агент. Перенос сохранён; вернись к исходному агенту для подключения.");
  const config={...fresh.config,mode:"remote",base,key,server_locator:locator};
  const issue=collectConnection(config,config);if(issue)throw new Error(issue);
  const reply=await shell<{ok?:boolean;error?:string}>("config_save",{config:JSON.stringify(config),mtimeNs:String(fresh.mtime_ns||"")});
  if(reply?.ok===false)throw new Error(reply.error||"Подключение не сохранилось");
  await shell("restart_self");
}
function drawExistingServer(host:HTMLElement) {
  let base="",key="";const out=el("p","receipt");
  host.replaceChildren(el("h4","","Подключить уже работающего агента"),field("Адрес Hélène на сервере",base,value=>base=value,{placeholder:"https://…"}),field("Ключ окна",key,value=>key=value,{type:"password",hint:"Ключ выдаёт установка Hélène на сервере. Это не пароль SSH и не токен бота."}));
  const connect=button("Проверить и подключить","primary",async()=>{
    connect.disabled=true;
    try{
      const candidate:Record<string,unknown>={mode:"remote",base,key};const issue=collectConnection(candidate,candidate);if(issue)throw new Error(issue);
      const endpoint=new URL(String(candidate.base));if(endpoint.protocol!=="https:")throw new Error("Для подключения через интернет нужен защищённый HTTPS-адрес.");
      receipt(out,"Проверяю связь с агентом…");
      const response=await fetch(String(candidate.base)+"/api/state",{headers:{Authorization:"Bearer "+String(candidate.key)},redirect:"error",signal:AbortSignal.timeout(20000)});
      if(!response.ok)throw new Error(response.status===403?"Сервер не принял ключ окна.":"Сервер пока не ответил. Проверь адрес и повтори.");
      const state=await response.json();if(!state.agent)throw new Error("По этому адресу не обнаружен агент Hélène.");
      const source=await shell<Loaded>("config_load");
      if(source.config.mode!=="remote")await stopForTransfer(out);
      receipt(out,"Связь подтверждена. Переключаю окно к агенту на сервере…");
      await attachServer(String(candidate.base),String(candidate.key),source);
    }catch(error){receipt(out,humanError(error).text,true);connect.disabled=false;}
  });host.append(connect,out);
  void shell<Loaded>("config_load").then(source=>{
    if(source.config.mode!=="remote"||!host.isConnected)return;
    base=String(source.config.base||"");key=String(source.config.key||"");
    const inputs=host.querySelectorAll<HTMLInputElement>("input");if(inputs[0])inputs[0].value=base;if(inputs[1])inputs[1].value=key;
  }).catch(error=>receipt(out,humanError(error).text,true));
}
async function stopForTransfer(out:HTMLElement) {
  receipt(out,"Останавливаю Hélène на этом компьютере, чтобы местная копия не работала одновременно с серверной…");
  await shell("owner_control",{action:"panic"});
  for(let attempt=0;attempt<30;attempt++) {
    const state=await shell<{stopped:boolean;runner_alive:boolean|null;pid:number}>("owner_state");
    if(state.stopped&&(state.runner_alive===false||!state.pid))return;
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  throw new Error("Остановка ещё не подтвердилась. Перенос не начат — дождись остановки и повтори.");
}
function drawNewServer(host:HTMLElement) {
  if (!SERVER_TRANSFER_AVAILABLE) {
    const unavailable=el('p','receipt',SERVER_TRANSFER_NOTICE);unavailable.setAttribute('role','status');
    host.replaceChildren(el('h4','','Перенос на сервер'),unavailable);
    return;
  }
  const data:Record<string,unknown>={login:"root",port:22,install_docker:false,deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  let ready=false,busy=false;
  const out=el("p","receipt");const trust=el("div");trust.hidden=true;
  // Owner's mounted folders stay here unless chosen: a copy on the server is a deliberate decision.
  const review=el("div","return-review");let decisions:Array<{path:string;bring:boolean}>=[];
  const changed=()=>{ready=false;trust.hidden=true;delete data.fingerprint;delete data.confirmed_host;start.disabled=true;};
  const {grid,advanced}=sshForm(data,changed,out);const inner=advanced.querySelector<HTMLElement>('.fold-body')!;
  const deployment=data.deployment as Record<string,unknown>;
  const docker=toggle("Подготовить Docker, если его ещё нет",false,value=>data.install_docker=value);
  const check=button("Проверить сервер","quiet",()=>void probe());
  const start=button("Перенести и подключить","primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;start.disabled=true;check.disabled=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      const source=await shell<Loaded>("config_load");
      const tg=source.config.telegram||{};
      if(source.config.mode==="remote")throw new Error("Это окно уже подключено к серверу. Для нового переноса открой местного агента.");
      if(!String(tg.bot_token||"").trim()||!/^\d+$/.test(String(tg.owner_id||""))||Number(tg.owner_id)<=0)throw new Error("Сначала подключи своего бота и Telegram ID в соседнем шаге Telegram: через его кнопку окно найдёт сервер после перезапуска.");
      const connection=await shell<{checks?:Array<{ok:boolean;name:string;message:string}>;message?:string}>("server_transfer",{action:"telegram-check",request:"{}"});
      if(!connection.checks)throw new Error(connection.message||"Не удалось проверить Telegram. Повтори подключение.");
      const blockers=connection.checks.filter(row=>!row.ok&&row.name!=="Фото и стикеры");
      if(blockers.length)throw new Error(blockers.map(row=>row.message).join(" "));
      await stopForTransfer(out);
      receipt(out,"Собираю архив и переношу агента. Подготовка сервера и голоса может занять несколько минут; дождись результата.");
      const result=await shell<{ok:boolean;message?:string;base?:string;key?:string;server_locator?:string}>("server_transfer",{action:"run",request:JSON.stringify({...data,projects:decisions,expected_config:source.path})});
      if(!result.ok||!result.base||!result.key)throw new Error(result.message||"Перенос ещё не подтверждён. Местные данные сохранены.");
      receipt(out,"Перенос и канал проверены. Stop сохранён; открываю серверную установку…");
      host.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value="");delete data.password;delete data.key_text;delete data.key_password;
      await attachServer(result.base,result.key,source,result.server_locator);
    }catch(error){receipt(out,reason(error)+" Местная память сохранена. Если сервер уже запущен, не включай второго агента одновременно.",true);}
    finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);check.disabled=false;start.disabled=!ready;}
  });start.disabled=true;
  const recover=button("Остановить серверную копию для возврата","quiet",async()=>{
    if(!ready||busy||transferBusy)return;
    busy=true;transferBusy=true;recover.disabled=true;
    try {
      const source=await shell<Loaded>("config_load");
      const result=await shell<{ok:boolean;message?:string}>("server_transfer",{action:"recover-local",request:JSON.stringify({...data,expected_config:source.path})});
      if(!result.ok)throw new Error(result.message||"Остановка не подтверждена");
      receipt(out,result.message||"Серверная копия остановлена. Местный Stop сохранён.");
    } catch(error){receipt(out,reason(error),true);}
    finally{busy=false;transferBusy=false;recover.disabled=false;}
  });recover.hidden=true;

  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,"Проверяю сервер…");
    try{
      const result=await shell<{ok:boolean;message:string;trust_needed?:boolean;fingerprint?:string;existing?:boolean;resumable?:boolean}>("server_transfer",{action:"probe",request:JSON.stringify(data)});
      ready=result.ok&&!result.trust_needed&&(!result.existing||!!result.resumable);
      recover.hidden=!result.resumable;
      start.textContent=result.resumable?"Продолжить перенос":"Перенести и подключить";
      if(result.fingerprint)data.fingerprint=result.fingerprint;
      if(result.trust_needed) {
        trust.hidden=false;trust.replaceChildren(el("p","field-hint",result.message),el("p","mono server-fingerprint",result.fingerprint||""),button("Это мой сервер — проверить вход","quiet",()=>{data.confirmed_host=true;void probe();}));
      }else trust.hidden=true;
      receipt(out,result.resumable?"Найден твой предыдущий перенос. Продолжу с сохранённого этапа; повторный импорт памяти не нужен.":result.existing?"На сервере уже есть Hélène. Выбери «Сервер уже работает», чтобы подключить окно.":result.message,!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,reason(error),true);}finally{busy=false;check.disabled=false;}
  }
  const preparation=el("div","server-preparation");preparation.append(docker,check);
  const actions=el("div","server-actions");actions.append(start,recover);
  host.replaceChildren(el("h4","","Перенести на новый сервер"),el("p","field-hint","Укажи данные сервера из его панели. Во время переноса Hélène на этом компьютере будет остановлена, затем канал управления будет доступен на сервере. Агент останется остановленным; возобновление — отдельная команда владельца на сервере. Приложение подготовит Ubuntu/Debian x64 или ARM64 и проверит подключение."),grid,advanced,review,preparation,trust,actions,out);
  void shell<ReturnScope>("server_transfer",{action:"transfer-scope",request:"{}"}).then(result=>{
    if(!host.isConnected||!result.ok||!result.scope)return;
    decisions=result.scope.projects.map(item=>({path:item.path,bring:false}));
    review.replaceChildren(el("h5","","Поедет на сервер"),...scopeReview(result,decisions,
      "Внешние папки агента на этом компьютере. Отмеченные поедут с ним в workspace/projects; остальные останутся здесь, и это будет записано в паспорте переноса.",
      "Что останется на этом компьютере и почему"));
  }).catch(error=>receipt(out,reason(error),true));
  void shell<{pending:boolean;message:string;host?:string;login?:string;ssh_port?:number;deployment?:Record<string,unknown>}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
    if(result.pending&&host.isConnected){
      receipt(out,result.message+(result.host?" Сервер: "+result.host+".":""));
      if(!data.host){
        data.host=result.host;data.login=result.login||"root";data.port=result.ssh_port||22;
        grid.dispatchEvent(new Event('ssh-fields-updated'));
        if(result.deployment)Object.assign(deployment,result.deployment);
        const extra=inner.querySelectorAll<HTMLInputElement>("input");[deployment.directory,deployment.project,deployment.port].forEach((value,index)=>{if(extra[index])extra[index].value=String(value);});
      }
    }
  }).catch(error=>receipt(out,reason(error),true));
}

// 12.11 (Yegor): «память, код, проекты — всё». The return brings the agent's CURRENT server
// life home: review first (the server keeps running), then fence/stop, fresh archive, resumable
// download, a new home beside the retained local copy, and one live copy. Resume stays separate.
type ReturnRoot={kind:string;path:string;files:number;bytes:number;git:Array<{path:string;kind:string}>;
  links:Array<{path:string;target:string;error:string}>;errors:unknown[];special:unknown[];empty_directories:number};
type ReturnScope={ok:boolean;message?:string;version?:string;local_version?:string;compatible?:boolean;
  scope?:{roots:ReturnRoot[];projects:Array<{path:string;via:string[]}>;unavailable:Array<{path:string;via?:string;reason:string}>};
  host_exclusions?:Array<{path:string;reason:string}>};
type ReturnStatus={returned?:boolean;phase?:string|null;progress?:{done:number;total:number}|null;projects?:Array<{path:string;bring:boolean}>};
const RETURN_PHASES:Record<string,string>={started:"Готовлю возврат",fenced:"Серверная копия остановлена; снимаю свежий архив",
  snapshot:"Скачиваю архив агента",downloaded:"Архив скачан и проверен; раскладываю в новую папку",
  staged:"Копия подготовлена; проверяю версию и переношу правки кода",activating:"Переключаю агента на вернувшиеся данные"};
const ROOT_TITLES:Record<string,string>={data:"Память, навыки и рабочая папка",code:"Код агента",application:"Оболочка агента"};
const UNAVAILABLE:Record<string,string>={bound_project_requires_mapping:"проект привязан к другому устройству",
  project_unavailable:"папки нет на сервере",relative_project_path:"путь записан относительно",project_not_directory:"это не папка"};
function megabytes(bytes:number){return (bytes/1048576).toFixed(bytes<10485760?1:0)+" МБ";}
// One review for both directions: what travels, external projects to bring or leave,
// and what stays behind with its reason. Nothing is decided silently.
function scopeReview(result:ReturnScope,decisions:Array<{path:string;bring:boolean}>,projectsHint:string,staysTitle:string):HTMLElement[] {
  const scope=result.scope!;
  const roots=el("ul","field-hint");
  for(const root of scope.roots){
    const extra=[root.git.length?"Git: "+root.git.length:"",root.links.length?"ссылок: "+root.links.length:"",root.empty_directories?"пустых папок: "+root.empty_directories:""].filter(Boolean).join(", ");
    roots.append(el("li","",(ROOT_TITLES[root.kind]||root.kind)+" — файлов "+root.files+", "+megabytes(root.bytes)+(extra?" ("+extra+")":"")));
  }
  const projects=el("div","return-projects");
  if(scope.projects.length){
    projects.append(el("p","field-hint",projectsHint));
    scope.projects.forEach((item,index)=>projects.append(toggle(item.path,decisions[index]?.bring!==false,value=>{decisions[index].bring=value;})));
  }
  const stays=el("details","fold");stays.append(el("summary","",staysTitle));
  const list=el("ul","field-hint");(result.host_exclusions||[]).forEach(row=>list.append(el("li","",row.path+" — "+row.reason)));
  scope.unavailable.forEach(item=>list.append(el("li","",(item.path||item.via||"проект")+" — "+(UNAVAILABLE[item.reason]||item.reason))));
  stays.append(list);
  return [roots,projects,stays];
}
function drawReturn(host:HTMLElement) {
  const data:Record<string,unknown>={login:"root",port:22,deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  let decisions:Array<{path:string;bring:boolean}>=[];let reviewed=false;let busy=false;let continuing=false;
  const out=el("p","receipt");const review=el("div","return-review");const live=el("p","receipt");live.setAttribute("aria-live","polite");
  const changed=()=>{if(continuing)return;reviewed=false;start.disabled=true;review.replaceChildren();};
  const {grid,advanced}=sshForm(data,changed,out);const inner=advanced.querySelector<HTMLElement>('.fold-body')!;
  const deployment=data.deployment as Record<string,unknown>;
  const check=button("Проверить, что вернётся","quiet",()=>void inspect());
  const start=button("Вернуть агента","primary",()=>void bring());start.disabled=true;
  const restartApp=button("Перезапустить программу","primary",async()=>{
    restartApp.disabled=true;
    try{await shell("restart_self",{full:true});}catch(error){receipt(out,reason(error),true);restartApp.disabled=false;}
  });restartApp.hidden=true;
  const cancel=button("Отменить незавершённый возврат","quiet",async()=>{
    if(busy)return;busy=true;cancel.disabled=true;
    try{
      const source=await shell<Loaded>("config_load");
      const reply=await shell<{ok:boolean;message?:string}>("server_transfer",{action:"return-cancel",request:JSON.stringify({expected_config:source.path})});
      if(!reply.ok)throw new Error(reply.message||"Отмена не подтверждена");
      continuing=false;cancel.hidden=true;start.textContent="Вернуть агента";reviewed=false;start.disabled=true;
      receipt(out,reply.message||"Возврат отменён.");
    }catch(error){receipt(out,reason(error),true);}finally{busy=false;cancel.disabled=false;}
  });cancel.hidden=true;
  function lock(on:boolean){host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>{if(node!==restartApp)node.disabled=on;});}
  async function inspect() {
    if(busy||transferBusy)return;busy=true;check.disabled=true;
    receipt(out,"Читаю состав агента на сервере. Сервер пока продолжает работать, ничего не меняется.");
    try{
      const source=await shell<Loaded>("config_load");
      const result=await shell<ReturnScope>("server_transfer",{action:"return-scope",request:JSON.stringify({...data,expected_config:source.path})});
      if(!result.ok||!result.scope)throw new Error(result.message||"Состав агента не прочитан.");
      decisions=result.scope.projects.map(item=>({path:item.path,bring:true}));
      const version=el("p","field-hint","Версия на сервере "+(result.version||"неизвестна")+", здесь "+(result.local_version||"неизвестна")+"."
        +(result.compatible===false?" Сервер новее: возврат скачается и подождёт, пока программа на этом компьютере обновится.":""));
      review.replaceChildren(el("h5","","Вернётся"),...scopeReview(result,decisions,
        "Внешние проекты агента на сервере. Отмеченные приедут вместе с ним; остальные останутся на сервере, и это будет записано в отчёте.",
        "Что останется на сервере и почему"),version);
      reviewed=true;start.disabled=false;out.textContent="";
    }catch(error){reviewed=false;receipt(out,reason(error),true);}finally{busy=false;check.disabled=false;}
  }
  async function bring() {
    if((!reviewed&&!continuing)||busy||transferBusy)return;busy=true;transferBusy=true;lock(true);
    receipt(out,"Останавливаю серверную копию и снимаю свежий архив. Большой агент скачивается дольше; окно можно не трогать.");
    const poll=setInterval(()=>void shell<{return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
      const state=result.return;if(!state?.phase)return;
      const step=RETURN_PHASES[state.phase]||state.phase;
      live.textContent=state.progress&&state.phase==="snapshot"?step+": "+megabytes(state.progress.done)+" из "+megabytes(state.progress.total):step+".";
    }).catch(()=>{}),1500);
    try{
      const source=await shell<Loaded>("config_load");
      const result=await shell<{ok:boolean;message?:string;code?:{summary?:string;folder?:string};previous_data?:string}>("server_transfer",
        {action:"return",request:JSON.stringify({...data,projects:decisions,expected_config:source.path})});
      if(!result.ok)throw new Error(result.message||"Возврат ещё не завершён.");
      host.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value="");delete data.password;delete data.key_text;delete data.key_password;
      const code=result.code?.summary?" Код: "+result.code.summary+(result.code.folder?" (материалы — "+result.code.folder+")":"")+".":"";
      receipt(out,(result.message||"Агент вернулся.")+code+(result.previous_data?" Прежняя копия: "+result.previous_data+".":""));
      live.textContent="";cancel.hidden=true;start.hidden=true;check.hidden=true;
      // The MiniApp reloads only the phone page; the computer's programme restarts there.
      if(electron?.presentation==="miniapp")out.textContent+=" Перезапусти программу на компьютере: из Mini App она не перезапускается.";
      else restartApp.hidden=false;
    }catch(error){
      const text=reason(error);
      receipt(out,text+" Прежняя местная копия и данные на сервере сохранены."+(/повтори/i.test(text)?"":" Повтори — возврат продолжится с сохранённого этапа."),true);
      cancel.hidden=false;start.textContent="Продолжить возврат";continuing=true;
    }finally{clearInterval(poll);busy=false;transferBusy=false;lock(false);start.disabled=!reviewed&&!continuing;}
  }
  const actions=el("div","server-actions");actions.append(check,start,cancel,restartApp);
  host.replaceChildren(el("h4","","Вернуть агента на этот компьютер"),
    el("p","field-hint","Вернётся текущий агент с сервера: память, навыки, настройки, его код и проекты, Git с историей. Сначала посмотри состав — сервер в это время работает. Затем серверная копия остановится, архив скачается и разложится в новую папку рядом с прежней. Прежняя местная копия и данные на сервере не удаляются. Агент останется остановленным: возобнови его сам, когда проверишь."),
    grid,advanced,review,actions,live,out);
  void shell<{pending:boolean;host?:string;login?:string;ssh_port?:number;deployment?:Record<string,unknown>;return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
    if(!host.isConnected)return;
    if(result.host&&!data.host){
      data.host=result.host;data.login=result.login||"root";data.port=result.ssh_port||22;
      grid.dispatchEvent(new Event('ssh-fields-updated'));
      if(result.deployment)Object.assign(deployment,result.deployment);
      const extra=inner.querySelectorAll<HTMLInputElement>("input");[deployment.directory,deployment.project,deployment.port].forEach((value,index)=>{if(extra[index])extra[index].value=String(value);});
    }
    if(result.return?.returned){receipt(out,"Агент уже вернулся с этого сервера. Прежняя копия и архив сохранены.");start.hidden=true;check.hidden=true;return;}
    if(result.return?.phase){
      continuing=true;decisions=result.return.projects||[];start.textContent="Продолжить возврат";start.disabled=false;cancel.hidden=false;
      receipt(out,"Найден незавершённый возврат: "+(RETURN_PHASES[result.return.phase]||result.return.phase).toLowerCase()+". Укажи вход на сервер и продолжи — уже сделанное не повторится.");
    }
  }).catch(error=>receipt(out,reason(error),true));
}

// 11.11 (Yegor): install a new server agent and update an installed one from the window;
// no manual scripts. Same SSH discipline as the transfer: the host key is shown and confirmed
// before any password is sent; passwords and keys are cleared from the form after use.
type ServerProbe={ok:boolean;message:string;trust_needed?:boolean;fingerprint?:string;existing?:boolean;arch?:string;docker?:boolean};
function sshForm(data:Record<string,unknown>,changed:()=>void,_out:HTMLElement) {
  const deployment=data.deployment as Record<string,unknown>;
  const grid=sshAccessForm(data,changed);
  const advanced=el("details","fold");const inner=el("div","fold-body");advanced.append(el("summary","","Параметры установки и SSH-ключ"),inner);
  inner.append(field("Папка установки",String(deployment.directory),value=>{deployment.directory=value;changed();}),
    field("Проект Compose",String(deployment.project),value=>{deployment.project=value;deployment.container=value;changed();}),
    field("Порт канала на сервере",String(deployment.port),value=>{deployment.port=Number(value);changed();}));
  return {grid,advanced};
}
function forgetSecrets(host:HTMLElement,data:Record<string,unknown>) {
  host.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value="");
  delete data.password;delete data.key_text;delete data.key_password;
}
function trustBlock(trust:HTMLElement,data:Record<string,unknown>,result:ServerProbe,again:()=>void) {
  if(result.fingerprint)data.fingerprint=result.fingerprint;
  if(!result.trust_needed){trust.hidden=true;return;}
  trust.hidden=false;
  trust.replaceChildren(el("p","field-hint",result.message),el("p","mono server-fingerprint",result.fingerprint||""),
    button("Это мой сервер — проверить вход","quiet",()=>{data.confirmed_host=true;again();}));
}

function drawFreshServer(host:HTMLElement) {
  const data:Record<string,unknown>={login:"root",port:22,install_docker:false,telegram:{},agent_name:"",
    deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  const telegram=data.telegram as Record<string,unknown>;
  let ready=false,busy=false;
  const out=el("p","receipt");const trust=el("div");trust.hidden=true;
  const changed=()=>{ready=false;trust.hidden=true;delete data.fingerprint;delete data.confirmed_host;start.disabled=true;};
  const {grid,advanced}=sshForm(data,changed,out);
  const agent=el("div","form-grid two");
  agent.append(field("Имя агента","",value=>{data.agent_name=value;}),
    field("Токен бота агента","",value=>{telegram.bot_token=value.trim();},{type:"password",hint:"Новый бот от @BotFather. Через его кнопку окно находит сервер после перезапуска."}),
    field("Твой Telegram ID","",value=>{telegram.owner_id=value.trim();},{hint:"Число из @userinfobot. Бот слушается только тебя."}));
  const docker=toggle("Подготовить Docker, если его ещё нет",false,value=>data.install_docker=value);
  const check=button("Проверить сервер","quiet",()=>void probe());
  const start=button("Установить агента на сервер","primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      receipt(out,"Ставлю Hélène на сервер: сборка образа и подготовка займут несколько минут. Местный агент не останавливается.");
      const result=await shell<{ok:boolean;message?:string;agent_id?:string;trust_needed?:boolean}>("server_install",{action:"install",request:JSON.stringify(data)});
      forgetSecrets(host,data);
      if(result.trust_needed)throw new Error(result.message||"Подтверди ключ сервера и повтори.");
      if(!result.ok||!result.agent_id)throw new Error(result.message||"Установка ещё не подтверждена.");
      receipt(out,"Агент на сервере установлен и подключён. Открываю его…");
      await shell("switch_agent",{id:result.agent_id});
    }catch(error){
      receipt(out,humanError(error).text,true);
      void offerConnect();
    }finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);start.disabled=!ready;}
  });start.disabled=true;
  const connect=button("Подключить установленного агента","quiet",async()=>{
    if(busy||transferBusy)return;busy=true;transferBusy=true;connect.disabled=true;
    try{
      receipt(out,"Ищу адрес агента через его бота…");
      const result=await shell<{ok:boolean;agent_id?:string;message?:string}>("server_install",{action:"connect",request:"{}"});
      if(!result.ok||!result.agent_id)throw new Error(result.message||"Адрес ещё не найден.");
      await shell("switch_agent",{id:result.agent_id});
    }catch(error){receipt(out,humanError(error).text,true);}finally{busy=false;transferBusy=false;connect.disabled=false;}
  });connect.hidden=true;
  async function offerConnect() {
    try{const state=await shell<{pending:boolean;phase?:string}>("server_install",{action:"status",request:"{}"});connect.hidden=!(state.pending&&state.phase==="installed");}
    catch{connect.hidden=true;}
  }
  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,"Проверяю сервер…");
    try{
      const result=await shell<ServerProbe>("server_install",{action:"probe",request:JSON.stringify(data)});
      trustBlock(trust,data,result,()=>void probe());
      ready=result.ok&&!result.trust_needed&&!result.existing;
      const where=result.arch==="aarch64"?" Сервер ARM64.":result.arch==="x86_64"?" Сервер x64.":"";
      receipt(out,result.existing?"На сервере уже стоит Hélène. Подключи её кнопкой «Сервер уже работает» или обнови кнопкой «Обновить сервер».":result.trust_needed?result.message:ready?"Сервер готов к установке."+where:result.message+where,!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  const preparation=el("div","server-preparation");preparation.append(docker,check);
  const actions=el("div","server-actions");actions.append(start,connect);
  host.replaceChildren(el("h4","","Новый агент на сервере"),
    el("p","field-hint","Приложение само поставит Hélène на твой сервер Ubuntu/Debian (x64 или ARM64) и откроет нового агента отдельной строкой. Агент на этом компьютере продолжает работать и не переносится."),
    grid,agent,advanced,preparation,trust,actions,out);
  void offerConnect();
}

function drawServerUpdate(host:HTMLElement) {
  const data:Record<string,unknown>={login:"root",port:22,deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  let ready=false,busy=false;
  const out=el("p","receipt");const trust=el("div");trust.hidden=true;
  const changed=()=>{ready=false;trust.hidden=true;delete data.fingerprint;delete data.confirmed_host;start.disabled=true;};
  const {grid,advanced}=sshForm(data,changed,out);
  const check=button("Проверить сервер","quiet",()=>void probe());
  const start=button("Обновить сервер","primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      receipt(out,"Передаю серверу эту версию. Его исполнитель сделает копию, обновит и проверит агента; если что-то пойдёт не так — вернёт прежнюю версию. Это займёт несколько минут.");
      const result=await shell<{ok:boolean;message?:string;trust_needed?:boolean}>("server_install",{action:"update",request:JSON.stringify(data)});
      forgetSecrets(host,data);
      if(result.trust_needed)throw new Error(result.message||"Подтверди ключ сервера и повтори.");
      receipt(out,result.message||"Готово.",!result.ok);
    }catch(error){receipt(out,humanError(error).text,true);}
    finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);start.disabled=!ready;}
  });start.disabled=true;
  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,"Проверяю сервер…");
    try{
      const result=await shell<ServerProbe>("server_install",{action:"probe",request:JSON.stringify(data)});
      trustBlock(trust,data,result,()=>void probe());
      ready=!result.trust_needed&&!!result.existing;
      receipt(out,result.trust_needed?result.message:result.existing?"Hélène на сервере найдена. Можно обновить её до версии этого приложения.":"В этой папке Hélène не найдена. Проверь папку установки в параметрах.",!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  const actions=el("div","server-actions");actions.append(check,start);
  host.replaceChildren(el("h4","","Обновить сервер из приложения"),
    el("p","field-hint","Нужно, когда на сервере ещё нет исполнителя обновлений или он не отвечает. Приложение по SSH передаст свою версию; дальше обновления — кнопкой «Обновить» в окне."),
    grid,advanced,trust,actions,out);
}
