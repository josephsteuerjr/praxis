import { t as uiText, UiError } from "../../lang/lang";
import { shell } from './api';
import { button, card, el, field, toggle } from '../dom';
import { humanError } from './lib';
import { sshAccessForm } from './server-access-form';
import type { ServerBridge } from './server-bridge-config';
import { serverBridgeManual } from './server-bridge-manual';

type Operation={id:string;name:string;phase:string;message:string;active?:boolean;plan?:{sha256:string;changes:Array<{message:string}>}};
type State={bridges:ServerBridge[];states:Record<string,{state:string;message:string}>;component:{installed:boolean}};

/** Same setup and daily controls live in the guide and persistent settings. */
export function serverBridgeCard(onUse:(bridge:ServerBridge)=>void|string|Promise<void|string>):HTMLElement {
  const body=el('div'),list=el('div'),setup=el('div'),operations=el('div'),note=el('p','receipt');
  let installed=false,busy=false;let timer:ReturnType<typeof setTimeout>|undefined;
  const command=<T>(action:string,request:unknown={})=>shell<T>('server_bridge',{action,request:JSON.stringify(request)});
  const info=el('details','fold');info.append(el('summary','',uiText("ui.ui.kit.window.server.settings.8f6923e3b8")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.7e4de143ff")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.d6105bb1f1")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.f52c607e61")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.7079f609f4")));
  info.append(button(uiText("ui.ui.kit.window.server.settings.e0bb6718d5"),'quiet',()=>{
    const url=URL.createObjectURL(new Blob([serverBridgeManual],{type:'text/markdown;charset=utf-8'}));
    const link=el('a');link.href=url;link.download='Helene-server-bridge.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }));
  const install=button(uiText("ui.ui.kit.window.server.settings.0383a1ea48"),'quiet',async()=>{
    install.disabled=true;
    try{await shell('component_install',{name:'server-bridge'});note.textContent=uiText("ui.ui.kit.window.server.settings.0d9f4d68be");await refresh();}
    catch(error){note.textContent=humanError(error).text;}
    finally{install.disabled=false;}
  });
  const add=button(uiText("ui.ui.kit.window.server.settings.3c1395e0a4"),'quiet',()=>drawSetup());
  const reload=button(uiText("ui.ui.kit.window.server.settings.35c394ca54"),'quiet',()=>void refresh());
  const actions=el('div','actions');actions.append(install,add,reload);
  async function inspectBridge(bridge:ServerBridge,out:HTMLElement) {
    if(busy)return;busy=true;out.parentElement?.querySelector('.bridge-use')?.remove();
    out.textContent=uiText("ui.ui.kit.window.server.settings.41c62997d0");
    try{
      const result=await command<{ok:boolean;checks:Array<{message:string}>}>('check',{id:bridge.id});
      out.textContent=result.checks.map(check=>check.message).join(' ');
      if(!result.ok)throw new UiError(uiText("ui.ui.kit.window.server.settings.b7a276c178"));
      const use=button(uiText("ui.ui.kit.window.server.settings.2243b059b6"),'primary',async()=>{
        use.disabled=true;
        try{const message=await onUse(bridge);out.textContent=message||uiText("ui.ui.kit.window.server.settings.2a012899e4");}
        catch(error){out.textContent=humanError(error).text;}
        finally{use.disabled=false;}
      });use.classList.add('bridge-use');out.after(use);
    }catch(error){out.textContent=humanError(error).text;}
    finally{busy=false;}
  }
  async function refresh(){
    clearTimeout(timer);
    if(busy){timer=setTimeout(()=>{if(body.isConnected)void refresh()},2500);return;}
    try{
      const state=await command<State>('list');installed=state.component.installed;
      install.hidden=installed;add.disabled=!installed;list.replaceChildren();
      for(const bridge of state.bridges){
        const row=el('section','');row.append(el('h4','',bridge.name));
        if(bridge.external)row.append(el('p','field-hint',bridge.external));
        const current=state.states[bridge.id];row.append(el('p','receipt',current?.message||uiText("ui.ui.kit.window.server.settings.21328a3a2d")));
        const out=el('p','receipt');const buttons=el('div','actions');
        buttons.append(button(uiText("ui.ui.kit.window.server.settings.93275ac8c5"),'quiet',()=>void inspectBridge(bridge,out)),
          button(uiText("ui.ui.kit.window.server.settings.557ca639e9"),'quiet',async()=>{try{await command('retry',{id:bridge.id});await refresh()}catch(error){out.textContent=humanError(error).text}}));
        row.append(buttons,out);list.append(row);
      }
      const pending=await command<Operation[]>('operations');operations.replaceChildren();
      for(const operation of pending){
        const row=el('section','');row.append(el('h4','',operation.name||uiText("ui.ui.kit.window.server.settings.393a928d66")),el('p','receipt',operation.message));
        if(operation.plan){
          const changes=el('ul','field-hint');for(const change of operation.plan.changes)changes.append(el('li','',change.message));row.append(changes);
          row.append(button(operation.phase==='planned'?uiText("ui.ui.kit.window.server.settings.c1e64b0c76"):uiText("ui.ui.kit.window.server.settings.948f86fbb0"),'primary',async()=>{
            try{await command('install',{id:operation.id,accepted_sha256:operation.plan!.sha256});await refresh();}
            catch(error){note.textContent=humanError(error).text;}
          }));
        }
        operations.append(row);
      }
      if(pending.some(value=>value.active))timer=setTimeout(()=>{if(body.isConnected)void refresh()},2500);
    }catch(error){note.textContent=humanError(error).text;}
  }
  function drawSetup(){
    const data:Record<string,unknown>={login:'root',port:22};let existing=false,proxy=true,https=false;
    const form=sshAccessForm(data,()=>{}),out=el('p','receipt'),extras=el('div','form-grid two');
    let name='',domain='',external='',publicAddress='',localPort=8888,remoteProxy=8888,remoteApp=18766;
    const httpFields=el('div');httpFields.hidden=true;
    const domainField=field(uiText("ui.ui.kit.window.server.settings.728a52663d"),'',value=>domain=value,{placeholder:'helene.example.org'});
    const externalField=field(uiText("ui.ui.kit.window.server.settings.bfd93aa6d9"),'',value=>external=value,{placeholder:'https://helene.example.org'});externalField.hidden=true;
    httpFields.append(domainField,externalField);
    const choice=el('div','actions');
    const auto=button(uiText("ui.ui.kit.window.server.settings.451f03b1c9"),'quiet',()=>{existing=false;auto.setAttribute('aria-pressed','true');manual.setAttribute('aria-pressed','false');domainField.hidden=false;externalField.hidden=true;});
    const manual=button(uiText("ui.ui.kit.window.server.settings.11153a731d"),'quiet',()=>{existing=true;auto.setAttribute('aria-pressed','false');manual.setAttribute('aria-pressed','true');domainField.hidden=true;externalField.hidden=false;});
    auto.setAttribute('aria-pressed','true');choice.append(auto,manual);
    const proxyFields=el('div','form-grid two');proxyFields.append(
      field(uiText("ui.ui.kit.window.server.settings.8d45fc2b21"),'8888',value=>localPort=Number(value),{type:'number'}),
      field(uiText("ui.ui.kit.window.server.settings.7bddbad76a"),'8888',value=>remoteProxy=Number(value),{type:'number'}));
    const reverseFields=el('div');reverseFields.hidden=true;reverseFields.append(
      field(uiText("ui.ui.kit.window.server.settings.107e8585a7"),'18766',value=>remoteApp=Number(value),{type:'number'}),
      field(uiText("ui.ui.kit.window.server.settings.2163464958"),'',value=>publicAddress=value,{hint:uiText("ui.ui.kit.window.server.settings.88bce69b8e")}));
    extras.append(proxyFields,reverseFields);
    const advanced=el('details','fold');advanced.append(el('summary','',uiText("ui.ui.kit.window.server.settings.e0e021a041")),extras);
    const submit=button(uiText("ui.ui.kit.window.server.settings.e30f061a56"),'primary',async()=>{
      if(!data.server_profile){out.textContent=uiText("ui.ui.kit.window.server.settings.2867e704ca");return;}
      submit.disabled=true;out.textContent=existing?uiText("ui.ui.kit.window.server.settings.de19649eda"):uiText("ui.ui.kit.window.server.settings.4291a149c0");
      try{
        if(existing){
          const ports:Record<string,number>={};if(proxy)Object.assign(ports,{local_proxy_port:localPort,remote_proxy_port:remoteProxy});if(https)ports.remote_app_port=remoteApp;
          const result=await command<{ok:boolean;bridge:ServerBridge;message:string}>('add-existing',{server_profile:data.server_profile,name,external:https?external:'',ports});
          await refresh();await inspectBridge(result.bridge,out);
        }else{
          await command('prepare',{server_profile:data.server_profile,name,proxy,https,domain,public_address:publicAddress,local_proxy_port:localPort,remote_proxy_port:remoteProxy,remote_app_port:remoteApp});
          out.textContent=uiText("ui.ui.kit.window.server.settings.83aff31a65");await refresh();
        }
      }catch(error){out.textContent=humanError(error).text;}
      finally{submit.disabled=false;}
    });
    setup.replaceChildren(el('h4','',uiText("ui.ui.kit.window.server.settings.2283137e59")),choice,field(uiText("ui.ui.kit.window.server.settings.8c96c91a7c"),'',value=>name=value),
      form,toggle(uiText("ui.ui.kit.window.server.settings.a644a513f1"),true,value=>{proxy=value;proxyFields.hidden=!value}),
      toggle(uiText("ui.ui.kit.window.server.settings.5dc62ddfff"),false,value=>{https=value;httpFields.hidden=!value;reverseFields.hidden=!value}),httpFields,advanced,submit,out);
  }
  body.append(el('p','field-hint',uiText("ui.ui.kit.window.server.settings.71927b1763")),
    actions,info,setup,operations,list,note);void refresh();return card(uiText("ui.ui.kit.window.server.settings.719d7aa17f"),body);
}
