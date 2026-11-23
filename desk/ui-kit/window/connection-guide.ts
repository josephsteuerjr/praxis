import { t as uiText, locale, UiError } from "../../lang/lang";
import { api, cfg, electron, inTauri, post, shell } from "./api";
import { button, el, field, toggle } from "../dom";
import { humanError } from "./lib";
import { phoneCard, type Config, type Loaded } from "./views/settings-frame";
import { collectConnection } from "./server-connection";
import { SERVER_TRANSFER_AVAILABLE, SERVER_TRANSFER_NOTICE } from './feature-availability';
import { sshAccessForm } from './server-access-form';
import { serverBridgeCard } from './server-settings';
import { useBridge } from './server-bridge-config';

let qrRenderer: (value:string)=>Promise<string> = async()=>{throw new UiError(uiText("ui.ui.kit.window.connection.guide.758847f282"));};
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
  if(fresh.remote!==session.remote||fresh.stamp!==session.stamp)throw new UiError(uiText("ui.ui.kit.window.connection.guide.e7da5293d6"));
  const next:Config={...fresh.config};
  for (const name of Object.keys(patch)) {
    const before=next[name]; const value=patch[name];
    next[name]=before && typeof before==="object" && value && typeof value==="object" ? {...before,...value} : value;
  }
  const reply= fresh.remote
    ? await post<{ok:boolean;error?:string}>("/api/agent-config",{config:Object.fromEntries(Object.keys(patch).map(key=>[key,next[key]])),mtime_ns:fresh.stamp})
    : await shell<{ok?:boolean;error?:string;code?:string}>("config_save",{config:JSON.stringify(next),mtimeNs:fresh.stamp});
  if (reply?.ok===false) throw new Error(reply.error||uiText("ui.ui.kit.window.connection.guide.581ae58c1b"));
  Object.assign(session,await load());
}
function receipt(box:HTMLElement,text:string,error=false) {
  box.className="receipt"+(error?" err":" ok"); box.textContent=text;
}
function steps(items:string[]) {
  const list=el("ol","field-hint"); items.forEach(text=>list.append(el("li","",text))); return list;
}
async function restart(box:HTMLElement) {
  const reply=await post<{ok:boolean;note?:string}>("/api/supervisor/restart",{target:"runner"});
  if (!reply.ok) throw new Error(reply.note||uiText("ui.ui.kit.window.connection.guide.e48750641a"));
  receipt(box,uiText("ui.ui.kit.window.connection.guide.db96efb862"));
}

export function mountConnectionGuide(host:HTMLElement) {
  const goals=[
    ["phone","Mini App Telegram",uiText("ui.ui.kit.window.connection.guide.36f59f35ea")],
    ["telegram",uiText("ui.ui.kit.window.connection.guide.10757dda55"),uiText("ui.ui.kit.window.connection.guide.f3c5a47259")],
    ["bridge",uiText("ui.ui.kit.window.connection.guide.719d7aa17f"),uiText("ui.ui.kit.window.connection.guide.6d73cadacc")],
    ["server",uiText("ui.ui.kit.window.connection.guide.9176ce0a09"),uiText("ui.ui.kit.window.connection.guide.8f8a16a25e")],
  ];
  const tabs=el("div","choice images-options connection-goals"); tabs.setAttribute("role","group"); tabs.setAttribute("aria-label",uiText("ui.ui.kit.window.connection.guide.bfd8671730"));
  const content=el("div","guide-content"); host.append(tabs,content);
  let serial=0;
  const pick=async(kind:string)=>{
    if(transferBusy)return;
    const current=++serial;
    tabs.querySelectorAll("button").forEach(node=>node.setAttribute("aria-pressed",String(node.dataset.goal===kind)));
    content.replaceChildren(el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.35951f816b")));
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
      content.replaceChildren(out,button(uiText("ui.ui.kit.window.connection.guide.5189135a61"),"quiet",()=>void pick(kind)));
    }
  };
  for(const [kind,title,text] of goals) {
    const node=button("","quiet",()=>void pick(kind)); node.className="choice-item";node.dataset.goal=kind;
    node.append(el("span","choice-title",title),el("span","choice-text",text)); tabs.append(node);
  }
  void pick("phone");
}
function drawBridge(host:HTMLElement) {
  if(!inTauri){host.append(el('p','field-hint',uiText("ui.ui.kit.window.connection.guide.99fec0f312")));return;}
  const note=el('p','receipt');const restartButton=button(uiText("ui.ui.kit.window.connection.guide.bfa262aa90"),'quiet',async()=>{
    try{await restart(note);restartButton.hidden=true;}catch(error){receipt(note,humanError(error).text,true);}
  });restartButton.hidden=true;
  host.append(serverBridgeCard({apply:async(bridge,targets)=>{
    const session=await load();
    if(session.remote)throw new UiError(uiText("ui.ui.kit.window.connection.guide.e809df1a6d"));
    const next=structuredClone(session.config);useBridge(next,bridge,{phone:!!bridge.external,...targets});
    const patch:Partial<Config>={};if(bridge.external)patch.phone=next.phone;
    if(targets.network)patch.network=next.network;
    if(targets.relay)patch.relay=next.relay;
    // Telegram's route needs an engine restart; the relay restarts by itself
    // when its route changes; the phone address is prepared in the background.
    await save(patch,session);restartButton.hidden=!targets.network;
    if(targets.network)return uiText("ui.ui.kit.window.connection.guide.01b83734e0");
    return [targets.relay?uiText('bridge.applied.relay'):'',bridge.external?uiText("ui.ui.kit.window.connection.guide.f9f2cbda7b"):'']
      .filter(Boolean).join(' ');
  }}),note,restartButton);
}
/** Daily server operations stay available after onboarding has finished. */
export async function mountServerManagement(host:HTMLElement) {
  const source=await shell<Loaded>('config_load');
  if(!host.isConnected)return;
  host.replaceChildren();drawServer(host,{config:source.config,stamp:String(source.mtime_ns||''),remote:source.config.mode==='remote'});
}
function drawPhone(host:HTMLElement,session:Session,reload:()=>void) {
  host.append(steps([uiText("ui.ui.kit.window.connection.guide.a76e2d2542"),session.remote?uiText("ui.ui.kit.window.connection.guide.aadd69aecf"):uiText("ui.ui.kit.window.connection.guide.29cb92db6d"),uiText("ui.ui.kit.window.connection.guide.ddb6d8a462"),session.remote?uiText("ui.ui.kit.window.connection.guide.87410c9336"):uiText("ui.ui.kit.window.connection.guide.02bde81bd7")]));
  const config=structuredClone(session.config);
  const card=phoneCard(config,!!config.phone?.enabled,config.phone?.mode||"",session.remote?cfg.base:"",qrRenderer,{showToggle:false});
  const out=el("p","receipt");
  if(!session.remote) {
    const apply=button(config.phone?.enabled?uiText("ui.ui.kit.window.connection.guide.b7625e8bb7"):uiText("ui.ui.kit.window.connection.guide.11383c8513"),"primary",async()=>{
      apply.disabled=true;receipt(out,uiText("ui.ui.kit.window.connection.guide.77d0a4d5fa"));
      try{
        const patch:Partial<Config>={phone:{...config.phone,enabled:true,mode:config.phone?.mode||'automatic'}};
        if(config.network?.bridge_profile)patch.network=config.network;
        await save(patch,session);reload();
      }
      catch(error){receipt(out,humanError(error).text,true);apply.disabled=false;}
    });
    host.append(apply,out);
    if(config.phone?.enabled)host.append(button(uiText("ui.ui.kit.window.connection.guide.b4c4159f13"),"quiet",async()=>{
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
  const token=field(uiText("ui.ui.kit.window.connection.guide.a3ae25a9f8"),String(tg.bot_token||""),value=>tg.bot_token=value,{type:"password",hint:uiText("ui.ui.kit.window.connection.guide.b9a267d98c")});
  const id=field(uiText("ui.ui.kit.window.connection.guide.8991005049"),String(tg.owner_id||""),value=>tg.owner_id=value,{hint:uiText("ui.ui.kit.window.connection.guide.fb8ed39fbe")});
  owner.append(token,id);
  const proxyFields=el("div","form-grid two");proxyFields.hidden=!proxy.enabled;
  proxyFields.append(field(uiText("ui.ui.kit.window.connection.guide.771987b200"),String(proxy.url||""),value=>proxy.url=value,{placeholder:"https://…/helene/telegram",hint:uiText("ui.ui.kit.window.connection.guide.73b53f6779")}),field(uiText("ui.ui.kit.window.connection.guide.b1f8b13c9c"),String(proxy.key||""),value=>proxy.key=value,{type:"password",hint:uiText("ui.ui.kit.window.connection.guide.34b68d4e84")}));
  const useProxy=toggle(uiText("ui.ui.kit.window.connection.guide.2d4d806483"),proxy.enabled===true,value=>{proxy.enabled=value;proxyFields.hidden=!value;});
  host.append(steps([uiText("ui.ui.kit.window.connection.guide.0a6e7f9ee2"),uiText("ui.ui.kit.window.connection.guide.09d85bb4da"),uiText("ui.ui.kit.window.connection.guide.a8c2c62c31")]),owner,useProxy,proxyFields);
  const controls=el("div","actions");
  const check=button(uiText("ui.ui.kit.window.connection.guide.87cb1172f2"),"quiet",async()=>{
    check.disabled=true;receipt(note,uiText("ui.ui.kit.window.connection.guide.0e1f613d36"));
    try{
      const result=await post<{ok:boolean;bot_url?:string;checks:Array<{name:string;ok:boolean;message:string}>}>("/api/telegram/check",{telegram:{...tg,proxy}});
      out.replaceChildren(...result.checks.map(item=>el("p",item.ok?"receipt ok":"receipt err",item.name+": "+item.message)));
      if(result.bot_url){const link=el("a","btn btn-quiet",uiText("ui.ui.kit.window.connection.guide.74d51d1a52"));link.href=result.bot_url;link.target="_blank";link.rel="noopener noreferrer";out.append(link);}
      receipt(note,result.ok?uiText("ui.ui.kit.window.connection.guide.f60ca02211"):uiText("ui.ui.kit.window.connection.guide.b89279061f"),!result.ok);
    }catch(error){receipt(note,humanError(error).text,true);}finally{check.disabled=false;}
  });
  const apply=button(uiText("ui.ui.kit.window.connection.guide.d9e0128dfa"),"primary",async()=>{
    apply.disabled=true;
    try{
      receipt(note,uiText("ui.ui.kit.window.connection.guide.e73d2da877"));
      const result=await post<{checks:Array<{name:string;ok:boolean;message:string}>}>("/api/telegram/check",{telegram:{...tg,proxy}});
      const blockers=result.checks.filter(row=>!row.ok&&row.name!=="Фото и стикеры");
      if(blockers.length)throw new Error(blockers.map(row=>row.message).join(" "));
      await save({telegram:{...tg,bot_token:String(tg.bot_token||"").trim(),proxy:{...proxy,url:String(proxy.url||"").trim(),key:String(proxy.key||"").trim()}}},session);await restart(note);
    }catch(error){receipt(note,humanError(error).text,true);}finally{apply.disabled=false;}
  });
  controls.append(check,apply);host.append(controls,note,out);
  if(tg.mode==="account")host.prepend(el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.27a84a3fba")));
  if(tg.bot_token||proxy.enabled)void check.click();
}
function drawServer(host:HTMLElement,session:Session) {
  host.append(steps([uiText("ui.ui.kit.window.connection.guide.ff32f2b5ec"),uiText("ui.ui.kit.window.connection.guide.1eb70f6b13"),uiText("ui.ui.kit.window.connection.guide.41c60a1fbe")]));
  if(!inTauri){host.append(el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.b9d401de99")));return;}
  const buttons=el("div","actions");const body=el("div","server-move-guide");
  if(!session.remote)buttons.append(button(uiText("ui.ui.kit.window.connection.guide.080639ea8a"),"primary",()=>{if(!transferBusy)drawNewServer(body);}));
  buttons.append(button(uiText("ui.ui.kit.window.connection.guide.b99010e383"),"quiet",()=>{if(!transferBusy)drawExistingServer(body);}));
  buttons.append(button(uiText("ui.ui.kit.window.connection.guide.8a4f7d9520"),"quiet",()=>{if(!transferBusy)drawFreshServer(body);}));
  buttons.append(button(uiText("ui.ui.kit.window.connection.guide.d985d9be6c"),"quiet",()=>{if(!transferBusy)drawServerUpdate(body);}));
  const back=button(uiText("ui.ui.kit.window.connection.guide.91672be89e"),"quiet",()=>{if(!transferBusy)drawReturn(body);});
  back.hidden=!session.remote;buttons.append(back);
  void shell<{pending?:boolean;return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"})
    .then(result=>{
      if(result.return?.pending_resume){
        back.textContent=uiText("server.return.resume");back.hidden=false;
      }else if(result.pending&&!result.return?.returned)back.hidden=false;
    }).catch(()=>{});
  if(session.remote)buttons.append(button(uiText("ui.ui.kit.window.connection.guide.b5f7920af5"),"quiet",async()=>{
    const out=el("p","receipt");body.replaceChildren(out);
    try{
      receipt(out,uiText("ui.ui.kit.window.connection.guide.b80d2a7fa9"));
      const fresh=await shell<Loaded>("config_load");
      const reply=await shell<{ok?:boolean;error?:string}>("config_save",{config:JSON.stringify({...fresh.config,mode:"local",server_locator:undefined}),mtimeNs:String(fresh.mtime_ns||"")});
      if(reply?.ok===false)throw new Error(reply.error||uiText("ui.ui.kit.window.connection.guide.cdba39d13e"));
      await shell("restart_self");
    }catch(error){receipt(out,humanError(error).text,true);}
  }));
  host.append(buttons,body);if(session.remote)drawExistingServer(body);else drawNewServer(body);
}

async function attachServer(base:string,key:string,source:Loaded,locator?:string) {
  const fresh=await shell<Loaded>("config_load");
  if(fresh.path!==source.path)throw new UiError(uiText("ui.ui.kit.window.connection.guide.bcb140a346"));
  const config={...fresh.config,mode:"remote",base,key,server_locator:locator};
  const issue=collectConnection(config,config);if(issue)throw new UiError(issue);
  const reply=await shell<{ok?:boolean;error?:string}>("config_save",{config:JSON.stringify(config),mtimeNs:String(fresh.mtime_ns||"")});
  if(reply?.ok===false)throw new Error(reply.error||uiText("ui.ui.kit.window.connection.guide.d41100c8a8"));
  await shell("restart_self");
}
function drawExistingServer(host:HTMLElement) {
  let base="",key="";const out=el("p","receipt");
  host.replaceChildren(el("h4","",uiText("ui.ui.kit.window.connection.guide.e8091ec1f1")),field(uiText("ui.ui.kit.window.connection.guide.d55b3da929"),base,value=>base=value,{placeholder:"https://…"}),field(uiText("ui.ui.kit.window.connection.guide.4a5a0cbeaa"),key,value=>key=value,{type:"password",hint:uiText("ui.ui.kit.window.connection.guide.22fec37b6e")}));
  const connect=button(uiText("ui.ui.kit.window.connection.guide.403dbcb170"),"primary",async()=>{
    connect.disabled=true;
    try{
      const candidate:Record<string,unknown>={mode:"remote",base,key};const issue=collectConnection(candidate,candidate);if(issue)throw new UiError(issue);
      const endpoint=new URL(String(candidate.base));if(endpoint.protocol!=="https:")throw new UiError(uiText("ui.ui.kit.window.connection.guide.c2160ecddd"));
      receipt(out,uiText("ui.ui.kit.window.connection.guide.00c6564844"));
      const response=await fetch(String(candidate.base)+"/api/state",{headers:{Authorization:"Bearer "+String(candidate.key)},redirect:"error",signal:AbortSignal.timeout(20000)});
      if(!response.ok)throw new UiError(response.status===403?uiText("ui.ui.kit.window.connection.guide.11868678cb"):uiText("ui.ui.kit.window.connection.guide.10f7f8535a"));
      const state=await response.json();if(!state.agent)throw new UiError(uiText("ui.ui.kit.window.connection.guide.61d09672ad"));
      const source=await shell<Loaded>("config_load");
      if(source.config.mode!=="remote")await stopForTransfer(out);
      receipt(out,uiText("ui.ui.kit.window.connection.guide.1f4d57383b"));
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
  receipt(out,uiText("ui.ui.kit.window.connection.guide.9c74a7d2d2"));
  await shell("owner_control",{action:"panic"});
  for(let attempt=0;attempt<30;attempt++) {
    const state=await shell<{stopped:boolean;runner_alive:boolean|null;pid:number}>("owner_state");
    if(state.stopped&&(state.runner_alive===false||!state.pid))return;
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  throw new UiError(uiText("ui.ui.kit.window.connection.guide.b92e388c2d"));
}
function drawNewServer(host:HTMLElement) {
  if (!SERVER_TRANSFER_AVAILABLE) {
    const unavailable=el('p','receipt',SERVER_TRANSFER_NOTICE());unavailable.setAttribute('role','status');
    host.replaceChildren(el('h4','',uiText("ui.ui.kit.window.connection.guide.2dda0e2c34")),unavailable);
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
  const docker=toggle(uiText("ui.ui.kit.window.connection.guide.dc8e4fd0e8"),false,value=>data.install_docker=value);
  const check=button(uiText("ui.ui.kit.window.connection.guide.c8cafbdc9a"),"quiet",()=>void probe());
  const start=button(uiText("ui.ui.kit.window.connection.guide.93684c61f8"),"primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;start.disabled=true;check.disabled=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      const source=await shell<Loaded>("config_load");
      const tg=source.config.telegram||{};
      if(source.config.mode==="remote")throw new UiError(uiText("ui.ui.kit.window.connection.guide.00e15b0a1e"));
      if(!String(tg.bot_token||"").trim()||!/^\d+$/.test(String(tg.owner_id||""))||Number(tg.owner_id)<=0)throw new UiError(uiText("ui.ui.kit.window.connection.guide.3f0190e0c1"));
      const connection=await shell<{checks?:Array<{ok:boolean;name:string;message:string}>;message?:string}>("server_transfer",{action:"telegram-check",request:"{}"});
      if(!connection.checks)throw new Error(connection.message||uiText("ui.ui.kit.window.connection.guide.e984d94257"));
      const blockers=connection.checks.filter(row=>!row.ok&&row.name!=="Фото и стикеры");
      if(blockers.length)throw new Error(blockers.map(row=>row.message).join(" "));
      await stopForTransfer(out);
      receipt(out,uiText("ui.ui.kit.window.connection.guide.c5f31e64e1"));
      const result=await shell<{ok:boolean;message?:string;base?:string;key?:string;server_locator?:string}>("server_transfer",{action:"run",request:JSON.stringify({...data,projects:decisions,expected_config:source.path})});
      if(!result.ok||!result.base||!result.key)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.af8d5f634f"));
      receipt(out,uiText("ui.ui.kit.window.connection.guide.555c6c2b6b"));
      host.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value="");delete data.password;delete data.key_text;delete data.key_password;
      await attachServer(result.base,result.key,source,result.server_locator);
    }catch(error){receipt(out,humanError(error).text+uiText("ui.ui.kit.window.connection.guide.b7af821ced"),true);}
    finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);check.disabled=false;start.disabled=!ready;}
  });start.disabled=true;
  const recover=button(uiText("ui.ui.kit.window.connection.guide.699d287d44"),"quiet",async()=>{
    if(!ready||busy||transferBusy)return;
    busy=true;transferBusy=true;recover.disabled=true;
    try {
      const source=await shell<Loaded>("config_load");
      const result=await shell<{ok:boolean;message?:string}>("server_transfer",{action:"recover-local",request:JSON.stringify({...data,expected_config:source.path})});
      if(!result.ok)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.6a53f9ebf1"));
      receipt(out,result.message||uiText("ui.ui.kit.window.connection.guide.20269420a2"));
    } catch(error){receipt(out,humanError(error).text,true);}
    finally{busy=false;transferBusy=false;recover.disabled=false;}
  });recover.hidden=true;

  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,uiText("ui.ui.kit.window.connection.guide.7f6b19b694"));
    try{
      const result=await shell<{ok:boolean;message:string;trust_needed?:boolean;fingerprint?:string;existing?:boolean;resumable?:boolean}>("server_transfer",{action:"probe",request:JSON.stringify(data)});
      ready=result.ok&&!result.trust_needed&&(!result.existing||!!result.resumable);
      recover.hidden=!result.resumable;
      start.textContent=result.resumable?uiText("ui.ui.kit.window.connection.guide.38959f9e7c"):uiText("ui.ui.kit.window.connection.guide.93684c61f8");
      if(result.fingerprint)data.fingerprint=result.fingerprint;
      if(result.trust_needed) {
        trust.hidden=false;trust.replaceChildren(el("p","field-hint",result.message),el("p","mono server-fingerprint",result.fingerprint||""),button(uiText("ui.ui.kit.window.connection.guide.0db5785a92"),"quiet",()=>{data.confirmed_host=true;void probe();}));
      }else trust.hidden=true;
      receipt(out,result.resumable?uiText("ui.ui.kit.window.connection.guide.9e22ef50e7"):result.existing?uiText("ui.ui.kit.window.connection.guide.04a8ce9456"):result.message,!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  const preparation=el("div","server-preparation");preparation.append(docker,check);
  const actions=el("div","server-actions");actions.append(start,recover);
  host.replaceChildren(el("h4","",uiText("ui.ui.kit.window.connection.guide.b4bf5ba114")),el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.2ac1b05bad")),grid,advanced,review,preparation,trust,actions,out);
  void shell<ReturnScope>("server_transfer",{action:"transfer-scope",request:"{}"}).then(result=>{
    if(!host.isConnected||!result.ok||!result.scope)return;
    decisions=result.scope.projects.map(item=>({path:item.path,bring:false}));
    review.replaceChildren(el("h5","",uiText("ui.ui.kit.window.connection.guide.e42deb294c")),...scopeReview(result,decisions,
      uiText("ui.ui.kit.window.connection.guide.a9de124bad"),
      uiText("ui.ui.kit.window.connection.guide.9652d2c82f")));
  }).catch(error=>receipt(out,humanError(error).text,true));
  void shell<{pending:boolean;message:string;host?:string;login?:string;ssh_port?:number;deployment?:Record<string,unknown>}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
    if(result.pending&&host.isConnected){
      receipt(out,result.message+(result.host?uiText("ui.ui.kit.window.connection.guide.2f2ccf14c0")+result.host+".":""));
      if(!data.host){
        data.host=result.host;data.login=result.login||"root";data.port=result.ssh_port||22;
        grid.dispatchEvent(new Event('ssh-fields-updated'));
        if(result.deployment)Object.assign(deployment,result.deployment);
        const extra=inner.querySelectorAll<HTMLInputElement>("input");[deployment.directory,deployment.project,deployment.port].forEach((value,index)=>{if(extra[index])extra[index].value=String(value);});
      }
    }
  }).catch(error=>receipt(out,humanError(error).text,true));
}

// 12.11 (the owner): «память, код, проекты — всё». The return brings the agent's CURRENT server
// life home: review first (the server keeps running), then fence/stop, fresh archive, resumable
// download, a new home beside the retained local copy, and one live copy. Resume stays separate.
type ReturnRoot={kind:string;path:string;files:number;bytes:number;git:Array<{path:string;kind:string}>;
  links:Array<{path:string;target:string;error:string}>;errors:unknown[];special:unknown[];empty_directories:number};
type ReturnScope={code_scope?:{agents:string[];binding:string;stopped:boolean};ok:boolean;message?:string;version?:string;local_version?:string;compatible?:boolean;
  scope?:{roots:ReturnRoot[];projects:Array<{path:string;via:string[]}>;unavailable:Array<{path:string;via?:string;reason:string}>};
  host_exclusions?:Array<{path:string;reason:string}>};
type ReturnStatus={pending_resume?:boolean;returned?:boolean;phase?:string|null;progress?:{done:number;total:number}|null;projects?:Array<{path:string;bring:boolean}>};
const RETURN_PHASES:Record<string,string>={get started() { return uiText("ui.ui.kit.window.connection.guide.713c87da71"); },get fenced() { return uiText("ui.ui.kit.window.connection.guide.298ea4cf21"); },
  get snapshot() { return uiText("ui.ui.kit.window.connection.guide.39eb6fff9a"); },get downloaded() { return uiText("ui.ui.kit.window.connection.guide.e18d08d5a9"); },
  get staged() { return uiText("ui.ui.kit.window.connection.guide.0846aecb00"); },get activating() { return uiText("ui.ui.kit.window.connection.guide.429b1bf7ee"); }};
const ROOT_TITLES:Record<string,string>={get data() { return uiText("ui.ui.kit.window.connection.guide.260a0572f8"); },get code() { return uiText("ui.ui.kit.window.connection.guide.c96eb04dc3"); },get application() { return uiText("ui.ui.kit.window.connection.guide.32f59781d2"); }};
const UNAVAILABLE:Record<string,string>={get bound_project_requires_mapping() { return uiText("ui.ui.kit.window.connection.guide.d81cde9147"); },
  get project_unavailable() { return uiText("ui.ui.kit.window.connection.guide.d532e549fb"); },get relative_project_path() { return uiText("ui.ui.kit.window.connection.guide.abece36db4"); },get project_not_directory() { return uiText("ui.ui.kit.window.connection.guide.01faea97a0"); }};
function megabytes(bytes:number){const digits=bytes<10485760?1:0;return (bytes/1048576).toLocaleString(locale(),{minimumFractionDigits:digits,maximumFractionDigits:digits})+uiText("ui.ui.kit.window.connection.guide.883817f199");}
// One review for both directions: what travels, external projects to bring or leave,
// and what stays behind with its reason. Nothing is decided silently.
function scopeReview(result:ReturnScope,decisions:Array<{path:string;bring:boolean}>,projectsHint:string,staysTitle:string):HTMLElement[] {
  const scope=result.scope!;
  const roots=el("ul","field-hint");
  for(const root of scope.roots){
    const extra=[root.git.length?"Git: "+root.git.length:"",root.links.length?uiText("ui.ui.kit.window.connection.guide.e4d2313846")+root.links.length:"",root.empty_directories?uiText("ui.ui.kit.window.connection.guide.d08dbcfdba")+root.empty_directories:""].filter(Boolean).join(", ");
    roots.append(el("li","",(ROOT_TITLES[root.kind]||root.kind)+uiText("ui.ui.kit.window.connection.guide.c6aae3b348")+root.files+", "+megabytes(root.bytes)+(extra?" ("+extra+")":"")));
  }
  const projects=el("div","return-projects");
  if(scope.projects.length){
    projects.append(el("p","field-hint",projectsHint));
    scope.projects.forEach((item,index)=>projects.append(toggle(item.path,decisions[index]?.bring!==false,value=>{decisions[index].bring=value;})));
  }
  const stays=el("details","fold");stays.append(el("summary","",staysTitle));
  const list=el("ul","field-hint");(result.host_exclusions||[]).forEach(row=>list.append(el("li","",row.path+" — "+row.reason)));
  scope.unavailable.forEach(item=>list.append(el("li","",(item.path||item.via||uiText("ui.ui.kit.window.connection.guide.dfb992e6d0"))+" — "+(UNAVAILABLE[item.reason]||item.reason))));
  stays.append(list);
  return [roots,projects,stays];
}
function drawReturn(host:HTMLElement) {
  const data:Record<string,unknown>={login:"root",port:22,deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  let decisions:Array<{path:string;bring:boolean}>=[];let reviewed=false;let busy=false;let continuing=false;
  const out=el("p","receipt");const review=el("div","return-review");const live=el("p","receipt");live.setAttribute("aria-live","polite");
  let reviewedConfig="", sharedCode:string|null=null, resumeProfile=false;
  const changed=()=>{if(continuing)return;reviewed=false;start.disabled=true;review.replaceChildren();};
  const {grid,advanced}=sshForm(data,changed,out);const inner=advanced.querySelector<HTMLElement>('.fold-body')!;
  const deployment=data.deployment as Record<string,unknown>;
  const check=button(uiText("ui.ui.kit.window.connection.guide.36900b0f94"),"quiet",()=>void inspect());
  const start=button(uiText("ui.ui.kit.window.connection.guide.dfffca24a0"),"primary",()=>void bring());start.disabled=true;
  const restartApp=button(uiText("ui.ui.kit.window.connection.guide.a489a6740a"),"primary",async()=>{
    restartApp.disabled=true;
    try{
      if(resumeProfile){
        const result=await shell<{ok:boolean;message?:string}>("server_transfer",{action:"resume-returned",request:JSON.stringify({expected_config:reviewedConfig})});
        if(!result.ok)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.d6584be0b8"));
        restartApp.hidden=true;
        receipt(out,uiText("server.return.resumed"));
      }else await shell("restart_self",{full:true});
    }catch(error){receipt(out,humanError(error).text,true);restartApp.disabled=false;}
  });restartApp.hidden=true;
  const cancel=button(uiText("ui.ui.kit.window.connection.guide.e910269f8a"),"quiet",async()=>{
    if(busy)return;busy=true;cancel.disabled=true;
    try{
      const source=await shell<Loaded>("config_load");
      const reply=await shell<{ok:boolean;message?:string}>("server_transfer",{action:"return-cancel",request:JSON.stringify({expected_config:reviewedConfig||source.path})});
      if(!reply.ok)throw new Error(reply.message||uiText("ui.ui.kit.window.connection.guide.371c99ad6e"));
      continuing=false;cancel.hidden=true;start.textContent=uiText("ui.ui.kit.window.connection.guide.dfffca24a0");reviewed=false;start.disabled=true;
      receipt(out,reply.message||uiText("ui.ui.kit.window.connection.guide.6d03b45dc4"));
    }catch(error){receipt(out,humanError(error).text,true);}finally{busy=false;cancel.disabled=false;}
  });cancel.hidden=true;
  function lock(on:boolean){host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>{if(node!==restartApp)node.disabled=on||node.dataset.requiresStop==="true";});}
  async function inspect() {
    if(busy||transferBusy)return;busy=true;check.disabled=true;
    receipt(out,uiText("ui.ui.kit.window.connection.guide.c3604f658c"));
    try{
      const source=await shell<Loaded>("config_load");
      const result=await shell<ReturnScope>("server_transfer",{action:"return-scope",request:JSON.stringify({...data,expected_config:source.path})});
      if(!result.ok||!result.scope)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.83972d185e"));
      reviewedConfig=source.path;sharedCode=null;
      decisions=result.scope.projects.map(item=>({path:item.path,bring:true}));
      const version=el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.5542171c47")+(result.version||uiText("ui.ui.kit.window.connection.guide.8d699d8c65"))+uiText("ui.ui.kit.window.connection.guide.4b000be118")+(result.local_version||uiText("ui.ui.kit.window.connection.guide.8d699d8c65"))+"."
        +(result.compatible===false?uiText("ui.ui.kit.window.connection.guide.ed8962d012"):""));
      review.replaceChildren(el("h5","",uiText("ui.ui.kit.window.connection.guide.be7ddb0d9a")),...scopeReview(result,decisions,
        uiText("ui.ui.kit.window.connection.guide.4b1ab907b9"),
        uiText("ui.ui.kit.window.connection.guide.7ddaa4ce64")),version);
      const scope=result.code_scope;
      if(scope?.agents.length){
        review.append(el("p","field-hint",uiText("server.return.private_choice",{agents:scope.agents.join(", ")})));
        const label=el("label","field-hint");const apply=document.createElement("input");
        apply.type="checkbox";apply.disabled=!scope.stopped;apply.dataset.requiresStop=String(!scope.stopped);
        apply.addEventListener("change",()=>{sharedCode=apply.checked?scope.binding:null;});
        label.append(apply,document.createTextNode(uiText("server.return.shared_choice")));review.append(label);
      }
      reviewed=true;start.disabled=false;out.textContent="";
    }catch(error){reviewed=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  async function bring() {
    if((!reviewed&&!continuing)||busy||transferBusy)return;busy=true;transferBusy=true;lock(true);
    receipt(out,uiText("ui.ui.kit.window.connection.guide.9574dbde3d"));
    const poll=setInterval(()=>void shell<{return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
      const state=result.return;if(!state?.phase)return;
      const step=RETURN_PHASES[state.phase]||state.phase;
      live.textContent=state.progress&&state.phase==="snapshot"?step+": "+megabytes(state.progress.done)+uiText("ui.ui.kit.window.connection.guide.e029cc4b39")+megabytes(state.progress.total):step+".";
    }).catch(()=>{}),1500);
    try{
      const source=await shell<Loaded>("config_load");
      const result=await shell<{ok:boolean;message?:string;restart?:string;code?:{summary?:string;folder?:string};previous_data?:string}>("server_transfer",
        {action:"return",request:JSON.stringify({...data,projects:decisions,...(reviewed?{shared_code:sharedCode}:{}),expected_config:reviewedConfig||source.path})});
      if(!result.ok)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.d6584be0b8"));
      host.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value="");delete data.password;delete data.key_text;delete data.key_password;
      const code=result.code?.summary?uiText("ui.ui.kit.window.connection.guide.dec37b12c9")+result.code.summary+(result.code.folder?uiText("ui.ui.kit.window.connection.guide.1ec1ee99f1")+result.code.folder+")":"")+".":"";
      receipt(out,(result.message||uiText("ui.ui.kit.window.connection.guide.0d400bffdb"))+code+(result.previous_data?uiText("ui.ui.kit.window.connection.guide.5753bd6c6b")+result.previous_data+".":""));
      live.textContent="";cancel.hidden=true;start.hidden=true;check.hidden=true;
      // From the MiniApp the computer's own programme window performs the restart.
      if(electron?.presentation==="miniapp")restartApp.textContent=uiText("ui.ui.kit.window.connection.guide.6c73988e0b");
      resumeProfile=result.restart==="profile";
      if(resumeProfile){reviewedConfig=reviewedConfig||source.path;restartApp.textContent=uiText("server.return.resume");}
      restartApp.hidden=false;
    }catch(error){
      const text=humanError(error).text;
      receipt(out,text+uiText("ui.ui.kit.window.connection.guide.de7fd9bf1c")+(/повтори/i.test(text)?"":uiText("ui.ui.kit.window.connection.guide.22004ef181")),true);
      cancel.hidden=false;start.textContent=uiText("ui.ui.kit.window.connection.guide.9f7c63856d");continuing=true;
    }finally{clearInterval(poll);busy=false;transferBusy=false;lock(false);start.disabled=!reviewed&&!continuing;}
  }
  const actions=el("div","server-actions");actions.append(check,start,cancel,restartApp);
  host.replaceChildren(el("h4","",uiText("ui.ui.kit.window.connection.guide.543a04f3bc")),
    el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.2af1c56579")),
    grid,advanced,review,actions,live,out);
  void shell<{pending:boolean;config_path:string;host?:string;login?:string;ssh_port?:number;deployment?:Record<string,unknown>;return?:ReturnStatus}>("server_transfer",{action:"status",request:"{}"}).then(result=>{
    if(!host.isConnected)return;
    if(result.host&&!data.host){
      data.host=result.host;data.login=result.login||"root";data.port=result.ssh_port||22;
      grid.dispatchEvent(new Event('ssh-fields-updated'));
      if(result.deployment)Object.assign(deployment,result.deployment);
      const extra=inner.querySelectorAll<HTMLInputElement>("input");[deployment.directory,deployment.project,deployment.port].forEach((value,index)=>{if(extra[index])extra[index].value=String(value);});
    }
    if(result.return?.phase||result.return?.returned)reviewedConfig=result.config_path;
    if(result.return?.returned){
      if(result.return.pending_resume){
        resumeProfile=true;
        restartApp.textContent=uiText("server.return.resume");restartApp.hidden=false;
      }
      receipt(out,uiText("ui.ui.kit.window.connection.guide.c128e7fa7d"));start.hidden=true;check.hidden=true;return;}
    if(result.return?.phase){
      continuing=true;decisions=result.return.projects||[];start.textContent=uiText("ui.ui.kit.window.connection.guide.9f7c63856d");start.disabled=false;cancel.hidden=false;
      receipt(out,uiText("ui.ui.kit.window.connection.guide.fb943adbd0")+(RETURN_PHASES[result.return.phase]||result.return.phase).toLowerCase()+uiText("ui.ui.kit.window.connection.guide.71c7079dd3"));
    }
  }).catch(error=>receipt(out,humanError(error).text,true));
}

// 11.11 (the owner): install a new server agent and update an installed one from the window;
// no manual scripts. Same SSH discipline as the transfer: the host key is shown and confirmed
// before any password is sent; passwords and keys are cleared from the form after use.
type ServerProbe={ok:boolean;message:string;trust_needed?:boolean;fingerprint?:string;existing?:boolean;arch?:string;docker?:boolean};
function sshForm(data:Record<string,unknown>,changed:()=>void,_out:HTMLElement) {
  const deployment=data.deployment as Record<string,unknown>;
  const grid=sshAccessForm(data,changed);
  const advanced=el("details","fold");const inner=el("div","fold-body");advanced.append(el("summary","",uiText("ui.ui.kit.window.connection.guide.de7d41e838")),inner);
  inner.append(field(uiText("ui.ui.kit.window.connection.guide.709f7210c7"),String(deployment.directory),value=>{deployment.directory=value;changed();}),
    field(uiText("ui.ui.kit.window.connection.guide.40e794dcaf"),String(deployment.project),value=>{deployment.project=value;deployment.container=value;changed();}),
    field(uiText("ui.ui.kit.window.connection.guide.f7ad2fc014"),String(deployment.port),value=>{deployment.port=Number(value);changed();}));
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
    button(uiText("ui.ui.kit.window.connection.guide.0db5785a92"),"quiet",()=>{data.confirmed_host=true;again();}));
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
  agent.append(field(uiText("ui.ui.kit.window.connection.guide.ba527ef131"),"",value=>{data.agent_name=value;}),
    field(uiText("ui.ui.kit.window.connection.guide.fcb6883f75"),"",value=>{telegram.bot_token=value.trim();},{type:"password",hint:uiText("ui.ui.kit.window.connection.guide.89b0733d9f")}),
    field(uiText("ui.ui.kit.window.connection.guide.8991005049"),"",value=>{telegram.owner_id=value.trim();},{hint:uiText("ui.ui.kit.window.connection.guide.c46d720845")}));
  const docker=toggle(uiText("ui.ui.kit.window.connection.guide.dc8e4fd0e8"),false,value=>data.install_docker=value);
  const check=button(uiText("ui.ui.kit.window.connection.guide.c8cafbdc9a"),"quiet",()=>void probe());
  const start=button(uiText("ui.ui.kit.window.connection.guide.1047f94551"),"primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      receipt(out,uiText("ui.ui.kit.window.connection.guide.8d148c8d41"));
      const result=await shell<{ok:boolean;message?:string;agent_id?:string;trust_needed?:boolean}>("server_install",{action:"install",request:JSON.stringify(data)});
      forgetSecrets(host,data);
      if(result.trust_needed)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.997dd3e5bb"));
      if(!result.ok||!result.agent_id)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.9b78bf1045"));
      receipt(out,uiText("ui.ui.kit.window.connection.guide.de74275889"));
      await shell("switch_agent",{id:result.agent_id});
    }catch(error){
      receipt(out,humanError(error).text,true);
      void offerConnect();
    }finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);start.disabled=!ready;}
  });start.disabled=true;
  const connect=button(uiText("ui.ui.kit.window.connection.guide.40f8edc347"),"quiet",async()=>{
    if(busy||transferBusy)return;busy=true;transferBusy=true;connect.disabled=true;
    try{
      receipt(out,uiText("ui.ui.kit.window.connection.guide.d4c1ffdf1c"));
      const result=await shell<{ok:boolean;agent_id?:string;message?:string}>("server_install",{action:"connect",request:"{}"});
      if(!result.ok||!result.agent_id)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.e07eb96d82"));
      await shell("switch_agent",{id:result.agent_id});
    }catch(error){receipt(out,humanError(error).text,true);}finally{busy=false;transferBusy=false;connect.disabled=false;}
  });connect.hidden=true;
  async function offerConnect() {
    try{const state=await shell<{pending:boolean;phase?:string}>("server_install",{action:"status",request:"{}"});connect.hidden=!(state.pending&&state.phase==="installed");}
    catch{connect.hidden=true;}
  }
  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,uiText("ui.ui.kit.window.connection.guide.7f6b19b694"));
    try{
      const result=await shell<ServerProbe>("server_install",{action:"probe",request:JSON.stringify(data)});
      trustBlock(trust,data,result,()=>void probe());
      ready=result.ok&&!result.trust_needed&&!result.existing;
      const where=result.arch==="aarch64"?uiText("ui.ui.kit.window.connection.guide.5d0f7a7dd7"):result.arch==="x86_64"?uiText("ui.ui.kit.window.connection.guide.e7531c00c0"):"";
      receipt(out,result.existing?uiText("ui.ui.kit.window.connection.guide.7f567a1f96"):result.trust_needed?result.message:ready?uiText("ui.ui.kit.window.connection.guide.4faeea96b2")+where:result.message+where,!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  const preparation=el("div","server-preparation");preparation.append(docker,check);
  const actions=el("div","server-actions");actions.append(start,connect);
  host.replaceChildren(el("h4","",uiText("ui.ui.kit.window.connection.guide.8a4f7d9520")),
    el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.c4db34d701")),
    grid,agent,advanced,preparation,trust,actions,out);
  void offerConnect();
}

function drawServerUpdate(host:HTMLElement) {
  const data:Record<string,unknown>={login:"root",port:22,deployment:{directory:"/opt/helene",project:"helene",container:"helene",port:8094}};
  let ready=false,busy=false;
  const out=el("p","receipt");const trust=el("div");trust.hidden=true;
  const changed=()=>{ready=false;trust.hidden=true;delete data.fingerprint;delete data.confirmed_host;start.disabled=true;};
  const {grid,advanced}=sshForm(data,changed,out);
  const check=button(uiText("ui.ui.kit.window.connection.guide.c8cafbdc9a"),"quiet",()=>void probe());
  const start=button(uiText("ui.ui.kit.window.connection.guide.d985d9be6c"),"primary",async()=>{
    if(!ready||busy||transferBusy)return;busy=true;transferBusy=true;
    host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=true);
    try{
      receipt(out,uiText("ui.ui.kit.window.connection.guide.48516eb849"));
      const result=await shell<{ok:boolean;message?:string;trust_needed?:boolean}>("server_install",{action:"update",request:JSON.stringify(data)});
      forgetSecrets(host,data);
      if(result.trust_needed)throw new Error(result.message||uiText("ui.ui.kit.window.connection.guide.997dd3e5bb"));
      receipt(out,result.message||uiText("ui.ui.kit.window.connection.guide.12c7111228"),!result.ok);
    }catch(error){receipt(out,humanError(error).text,true);}
    finally{busy=false;transferBusy=false;host.querySelectorAll<HTMLInputElement|HTMLButtonElement>("input,button").forEach(node=>node.disabled=false);start.disabled=!ready;}
  });start.disabled=true;
  async function probe() {
    if(busy)return;busy=true;check.disabled=true;receipt(out,uiText("ui.ui.kit.window.connection.guide.7f6b19b694"));
    try{
      const result=await shell<ServerProbe>("server_install",{action:"probe",request:JSON.stringify(data)});
      trustBlock(trust,data,result,()=>void probe());
      ready=!result.trust_needed&&!!result.existing;
      receipt(out,result.trust_needed?result.message:result.existing?uiText("ui.ui.kit.window.connection.guide.734d98110f"):uiText("ui.ui.kit.window.connection.guide.112ff747b0"),!ready&&!result.trust_needed);
      start.disabled=!ready;
    }catch(error){ready=false;receipt(out,humanError(error).text,true);}finally{busy=false;check.disabled=false;}
  }
  const actions=el("div","server-actions");actions.append(check,start);
  host.replaceChildren(el("h4","",uiText("ui.ui.kit.window.connection.guide.7f150827aa")),
    el("p","field-hint",uiText("ui.ui.kit.window.connection.guide.59c6382c5b")),
    grid,advanced,trust,actions,out);
}
