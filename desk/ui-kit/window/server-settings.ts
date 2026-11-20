import { t as uiText } from "../../lang/lang";
import { shell } from './api';
import { button, card, choice, el, field, toggle } from '../dom';
import { humanError } from './lib';
import { sshAccessForm } from './server-access-form';
import type { ServerBridge } from './server-bridge-config';
import { serverBridgeManual } from './server-bridge-manual';

type Operation={id:string;name:string;phase:string;message:string;active?:boolean;plan?:{sha256:string;changes:Array<{message:string}>}};
type State={bridges:ServerBridge[];states:Record<string,{state:string;message:string;error_code?:string}>;component:{installed:boolean}};
type Checked={ok:boolean;checks?:Array<{message:string}>;error_code?:string;message?:string;bridge?:ServerBridge};
/** Guide only: apply a fresh bridge to the chosen routes right away. */
type Apply=(bridge:ServerBridge,targets:{network:boolean;relay:boolean})=>void|string|Promise<void|string>;
type PanelOptions={
  /** Routes that currently go through a bridge, shown in its row. */
  usage?:(id:string)=>string[];
  apply?:Apply;
};
function bridgeError(code?:string) {
  switch(code){
    case 'local_port_unavailable':return uiText('bridge.error.local_port');
    case 'ssh_host_key_changed':return uiText('bridge.error.host_key');
    case 'ssh_authentication_failed':case 'ssh_credentials_invalid':return uiText('bridge.error.authentication');
    case 'ssh_unavailable':return uiText('bridge.error.unavailable');
    default:return '';
  }
}
/** A failed check speaks in the backend's exact words when it has them. */
function failure(result:Checked) {
  return result.message||bridgeError(result.error_code)||uiText("ui.ui.kit.window.server.settings.b7a276c178");
}
function line(node:HTMLElement) {const box=el('div');box.append(node);return box;}
/** Two clicks instead of a modal dialog, which the WebView may not show. */
function confirmButton(text:string,confirm:string,action:()=>Promise<void>) {
  let armed:ReturnType<typeof setTimeout>|undefined;
  const node=button(text,'quiet',async()=>{
    if(!armed){node.textContent=confirm;armed=setTimeout(()=>{armed=undefined;node.textContent=text;},5000);return;}
    clearTimeout(armed);armed=undefined;node.textContent=text;node.disabled=true;
    try{await action();}finally{node.disabled=false;}
  });
  return node;
}

/**
 * Hélène bridges as proxy sources. 2.8.12 (owner, 20.11): a bridge only serves
 * a proxy on this computer; Telegram and the relay pick it in their own routes,
 * so the list says what each bridge serves and who uses it, without a separate
 * «use for this agent» step or a manual refresh button.
 */
export function serverBridgePanel(options:PanelOptions={}):HTMLElement {
  const body=el('div'),list=el('div'),setup=el('div'),operations=el('div'),note=el('p','receipt');
  let installed=false,busy=false;let timer:ReturnType<typeof setTimeout>|undefined;
  let last:State|undefined;let signature='';
  const command=<T>(action:string,request:unknown={})=>shell<T>('server_bridge',{action,request:JSON.stringify(request)});
  const info=el('details','fold');info.append(el('summary','',uiText("ui.ui.kit.window.server.settings.8f6923e3b8")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.7e4de143ff")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.d6105bb1f1")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.f52c607e61")),
    el('p','field-hint',uiText("ui.ui.kit.window.server.settings.7079f609f4")));
  info.append(button(uiText("ui.ui.kit.window.server.settings.e0bb6718d5"),'quiet',()=>{
    const url=URL.createObjectURL(new Blob([serverBridgeManual()],{type:'text/markdown;charset=utf-8'}));
    const link=el('a');link.href=url;link.download='Helene-server-bridge.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }));
  const install=button(uiText("ui.ui.kit.window.server.settings.0383a1ea48"),'quiet',async()=>{
    install.disabled=true;
    try{await shell('component_install',{name:'server-bridge'});note.textContent=uiText("ui.ui.kit.window.server.settings.0d9f4d68be");await refresh();}
    catch(error){note.textContent=humanError(error).text;}
    finally{install.disabled=false;}
  });
  const add=button(uiText('bridge.add'),'quiet',async()=>{
    add.disabled=true;
    try{
      if(!installed){await shell('component_install',{name:'server-bridge'});installed=true;install.hidden=true;}
      drawSetup();
    }catch(error){note.textContent=humanError(error).text;}
    finally{add.disabled=false;}
  });
  const actions=el('div','actions');actions.append(install,add);
  /** Settings: the routes below choose the bridge. Guide: choose targets here. */
  function ready(bridge:ServerBridge,out:HTMLElement) {
    out.parentElement?.querySelectorAll('.bridge-use').forEach(node=>node.remove());
    const port=bridge.ports.local_proxy_port;
    if(!options.apply){
      if(port)out.textContent+=' '+uiText('bridge.ready',{p0:'127.0.0.1:'+port});
      return;
    }
    let network=!!port,relay=false;
    const choices=el('div','bridge-use');
    if(port)choices.append(line(toggle(uiText('network.usage.telegram'),true,value=>network=value)),
      line(toggle(uiText('network.usage.relay'),false,value=>relay=value)));
    const use=button(uiText('bridge.apply'),'primary',async()=>{
      use.disabled=true;
      try{const message=await options.apply!(bridge,{network,relay});out.textContent=message||uiText("ui.ui.kit.window.server.settings.2a012899e4");}
      catch(error){out.textContent=humanError(error).text;}
      finally{use.disabled=false;}
    });choices.append(use);out.after(choices);
  }
  function repairOffer(bridge:ServerBridge,out:HTMLElement) {
    const repair=button(uiText('bridge.port.repair'),'quiet',async()=>{
      repair.disabled=true;
      try{const result=await command<{message:string}>('repair-port',{id:bridge.id});await refresh();note.textContent=result.message;}
      catch(error){out.textContent=humanError(error).text;repair.disabled=false;}
    });
    const box=el('div','bridge-use');box.append(repair);out.after(box);
  }
  async function inspectBridge(bridge:ServerBridge,out:HTMLElement) {
    if(busy)return;busy=true;out.parentElement?.querySelectorAll('.bridge-use').forEach(node=>node.remove());
    out.textContent=uiText("ui.ui.kit.window.server.settings.41c62997d0");
    try{
      const result=await command<Checked>('check',{id:bridge.id});
      if(!result.ok){
        out.textContent=failure(result);
        if(result.error_code==='local_port_unavailable'&&bridge.ports.local_proxy_port)repairOffer(bridge,out);
        return;
      }
      out.textContent=[uiText('bridge.ssh.verified'),...(result.checks||[]).map(check=>check.message)].join(' ');
      if(options.apply)ready(bridge,out);
    }catch(error){out.textContent=humanError(error).text;}
    finally{busy=false;}
  }
  function drawRows(){
    if(!last)return;
    list.replaceChildren();
    for(const bridge of last.bridges){
      const row=el('section','bridge-row');row.append(el('h4','',bridge.name));
      const port=bridge.ports?.local_proxy_port;
      if(port)row.append(el('p','field-hint',uiText('bridge.local_proxy',{p0:`127.0.0.1:${port}`})));
      if(bridge.external)row.append(el('p','field-hint',uiText('bridge.https',{p0:bridge.external})));
      const current=last.states[bridge.id];
      row.append(el('p','receipt',bridgeError(current?.error_code)||current?.message||uiText("ui.ui.kit.window.server.settings.21328a3a2d")));
      const used=options.usage?.(bridge.id)||[];
      if(options.usage)row.append(el('p','field-hint',used.length?uiText('bridge.used_by',{p0:used.join(', ')}):uiText('bridge.unused')));
      const out=el('p','receipt');const buttons=el('div','actions');
      buttons.append(button(uiText("ui.ui.kit.window.server.settings.93275ac8c5"),'quiet',()=>void inspectBridge(bridge,out)),
        button(uiText('bridge.reconnect'),'quiet',async()=>{try{await command('retry',{id:bridge.id});await refresh()}catch(error){out.textContent=humanError(error).text}}),
        confirmButton(uiText('bridge.remove'),uiText('bridge.remove_confirm'),async()=>{
          try{const result=await command<{message:string}>('remove',{id:bridge.id});await refresh();note.textContent=result.message;}
          catch(error){out.textContent=humanError(error).text;}
        }));
      row.append(buttons,out);list.append(row);
      if(current?.error_code==='local_port_unavailable'&&port)repairOffer(bridge,out);
    }
  }
  async function refresh(){
    clearTimeout(timer);
    if(busy){timer=setTimeout(()=>{if(body.isConnected)void refresh()},2500);return;}
    try{
      const state=await command<State>('list');installed=state.component.installed;
      install.hidden=installed;last=state;drawRows();
      // Routes list bridges as choices: tell them when the set of sources changes.
      const next=state.bridges.map(row=>row.id+':'+(row.ports?.local_proxy_port||'')).join(',');
      if(signature&&next!==signature)document.dispatchEvent(new CustomEvent('helene-bridges-changed'));
      signature=next;
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
      // Follow a connecting bridge or a running setup on their own; no refresh button.
      const moving=Object.values(state.states).some(value=>value.state==='connecting'||value.state==='retry');
      if(pending.some(value=>value.active)||moving)timer=setTimeout(()=>{if(body.isConnected)void refresh()},2500);
    }catch(error){note.textContent=humanError(error).text;}
  }
  const routeChanged=()=>{if(!body.isConnected&&last){document.removeEventListener('helene-route-changed',routeChanged);return;}drawRows();};
  document.addEventListener('helene-route-changed',routeChanged);
  function drawSetup(){
    const data:Record<string,unknown>={login:'root',port:22};let existing=true,proxy=true,https=false;
    const out=el('p','receipt'),extras=el('div','form-grid two');
    // One primary action: saving the SSH connection is part of it. A confirmed
    // fingerprint continues the same action instead of asking for a second click.
    const form=sshAccessForm(data,()=>{},{saveButton:false,onSaved:()=>void run()});
    let name='',domain='',external='',publicAddress='',localPort='',remoteProxy=8888,remoteApp=18766;
    const httpFields=el('div');httpFields.hidden=true;
    const domainField=field(uiText("ui.ui.kit.window.server.settings.728a52663d"),'',value=>domain=value,{placeholder:'helene.example.org'});domainField.hidden=true;
    const externalField=field(uiText("ui.ui.kit.window.server.settings.bfd93aa6d9"),'',value=>external=value,{placeholder:'https://helene.example.org'});
    httpFields.append(domainField,externalField);
    const mode=choice<'existing'|'auto'>([
      {value:'existing',title:uiText("ui.ui.kit.window.server.settings.11153a731d"),text:uiText('bridge.mode.existing')},
      {value:'auto',title:uiText("ui.ui.kit.window.server.settings.451f03b1c9"),text:uiText('bridge.mode.auto')},
    ],'existing',value=>{
      existing=value==='existing';
      domainField.hidden=existing;
      externalField.hidden=!existing;
      submit.textContent=existing?uiText('connections.bridge_check'):uiText("ui.ui.kit.window.server.settings.e30f061a56");
      out.textContent='';
    });
    const proxyFields=el('div','form-grid two');proxyFields.append(
      field(uiText("ui.ui.kit.window.server.settings.8d45fc2b21"),'',value=>localPort=value.trim(),{type:'number',placeholder:uiText('bridge.port.auto'),hint:uiText('bridge.port.auto_hint')}),
      field(uiText("ui.ui.kit.window.server.settings.7bddbad76a"),'8888',value=>remoteProxy=Number(value),{type:'number'}));
    const reverseFields=el('div');reverseFields.hidden=true;reverseFields.append(
      field(uiText("ui.ui.kit.window.server.settings.107e8585a7"),'18766',value=>remoteApp=Number(value),{type:'number'}),
      field(uiText("ui.ui.kit.window.server.settings.2163464958"),'',value=>publicAddress=value,{hint:uiText("ui.ui.kit.window.server.settings.88bce69b8e")}));
    extras.append(proxyFields,reverseFields);
    const advanced=el('details','fold');advanced.append(el('summary','',uiText("ui.ui.kit.window.server.settings.e0e021a041")),extras);
    let running=false;
    async function run(){
      if(running)return;running=true;submit.disabled=true;
      setup.querySelectorAll('.bridge-use').forEach(node=>node.remove());
      out.textContent=uiText("ui.ui.kit.window.server.access.form.40f9ee7915");
      try{
        if(!await form.save()){out.textContent=uiText('bridge.ssh.pending');return;}
        out.textContent=existing?uiText("ui.ui.kit.window.server.settings.de19649eda"):uiText("ui.ui.kit.window.server.settings.4291a149c0");
        if(existing){
          const ports:Record<string,number|string>={};
          if(proxy)Object.assign(ports,{local_proxy_port:localPort,remote_proxy_port:remoteProxy});
          if(https)ports.remote_app_port=remoteApp;
          const result=await command<Checked>('add-existing',{server_profile:data.server_profile,name,external:https?external:'',ports,verify:true});
          if(!result.ok||!result.bridge){out.textContent=failure(result);return;}
          await refresh();
          out.textContent=[uiText('bridge.ssh.verified'),...(result.checks||[]).map(check=>check.message)].join(' ');
          ready(result.bridge,out);
        }else{
          const result=await command<Checked>('prepare',{server_profile:data.server_profile,name,proxy,https,domain,public_address:publicAddress,
            ...(localPort?{local_proxy_port:localPort}:{}),remote_proxy_port:remoteProxy,remote_app_port:remoteApp});
          if(result&&result.ok===false){out.textContent=failure(result);return;}
          out.textContent=uiText("ui.ui.kit.window.server.settings.83aff31a65");await refresh();
        }
      }catch(error){out.textContent=humanError(error).text;}
      finally{running=false;submit.disabled=false;}
    }
    const submit=button(uiText('connections.bridge_check'),'primary',()=>void run());
    const toggles=el('div','bridge-toggles');
    toggles.append(line(toggle(uiText("ui.ui.kit.window.server.settings.a644a513f1"),true,value=>{proxy=value;proxyFields.hidden=!value})),
      line(toggle(uiText("ui.ui.kit.window.server.settings.5dc62ddfff"),false,value=>{https=value;httpFields.hidden=!value;reverseFields.hidden=!value})));
    setup.replaceChildren(el('h4','',uiText("ui.ui.kit.window.server.settings.2283137e59")),el('p','field-hint',uiText('bridge.existing.help')),mode.el,
      field(uiText("ui.ui.kit.window.server.settings.8c96c91a7c"),'',value=>name=value),
      form,toggles,httpFields,advanced,submit,out);
  }
  body.append(el('p','field-hint',uiText('bridge.bundled.help')),
    actions,info,setup,operations,list,note);void refresh();return body;
}

export function serverBridgeCard(options:PanelOptions):HTMLElement {
  return card(uiText("ui.ui.kit.window.server.settings.719d7aa17f"),serverBridgePanel(options));
}
