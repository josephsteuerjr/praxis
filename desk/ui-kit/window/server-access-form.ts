import { t as uiText } from "../../lang/lang";
import { shell } from './api';
import { button, el, field } from '../dom';
import { humanError } from './lib';
import { pickPaperFiles } from './paper-files';

export type ServerProfile={id:string;name:string;host:string;port:number;login:string;kind?:string;error?:boolean};
type Check={ok:boolean;trust_needed?:boolean;fingerprint?:string;message:string;profile?:ServerProfile};
type Options={
  /** Own «check and save» button; a flow with its single primary action hides it. */
  saveButton?:boolean;
  /** Called after a confirmed fingerprint lets the connection save on its own. */
  onSaved?:()=>void;
};
export type SshAccess=HTMLElement&{
  /** Save the typed connection (or keep the chosen one). True once a profile is ready. */
  save():Promise<boolean>;
};

/** «Copy as path» in Explorer wraps the path in quotes. */
export function plainKeyPath(value:string):string {
  const text=value.trim();
  return text.length>1&&text[0]===text[text.length-1]&&(text[0]==='"'||text[0]==="'")?text.slice(1,-1).trim():text;
}

/** Shared SSH input and saved profiles for bridge, install, move and update. */
export function sshAccessForm(data:Record<string,unknown>,changed:()=>void,options:Options={}):SshAccess {
  const element:SshAccess=Object.assign(el('div'),{save:async()=>false});const out=el('p','receipt');const trust=el('div');
  const picker=el('select','field-input');picker.setAttribute('aria-label',uiText("ui.ui.kit.window.server.access.form.371c182df3"));
  // The choice is in the form only when there is something saved to choose.
  const pickerBox=el('div');
  const manual=el('fieldset','');manual.style.border='0';manual.style.padding='0';manual.style.margin='12px 0';
  const grid=el('div','form-grid two');const extra=el('details','fold');const inner=el('div','fold-body');
  extra.append(el('summary','',uiText("ui.ui.kit.window.server.access.form.b64c720e30")),inner);
  const invalidate=()=>{delete data.fingerprint;delete data.confirmed_host;trust.replaceChildren();out.textContent='';changed();};
  const host=field(uiText("ui.ui.kit.window.server.access.form.4289b17137"),String(data.host||''),value=>{data.host=value.trim();invalidate()},{placeholder:uiText("ui.ui.kit.window.server.access.form.948b3597be")});
  const login=field(uiText("ui.ui.kit.window.server.access.form.5be12bfe49"),String(data.login||'root'),value=>{data.login=value.trim();invalidate()});
  const password=field(uiText("ui.ui.kit.window.server.access.form.8c652c0a26"),'',value=>{data.password=value;delete data.key_text;invalidate()},
    {type:'password',hint:uiText("ui.ui.kit.window.server.access.form.af89f7ae94")});
  const keyLabel=el('p','receipt');
  password.querySelector('input')!.addEventListener('paste',event=>{
    const value=event.clipboardData?.getData('text')||'';
    if(!/^\s*-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(value))return;
    event.preventDefault();data.password=value;delete data.key_text;delete data.key_path;
    password.querySelector('input')!.value='';invalidate();keyLabel.textContent=uiText("ui.ui.kit.window.server.access.form.cd4b2d5f42");
  });
  grid.append(host,login,password);
  const port=field(uiText("ui.ui.kit.window.server.access.form.ece958000e"),String(data.port||22),value=>{data.port=Number(value);invalidate()},{type:'number'});
  element.addEventListener('ssh-fields-updated',()=>{
    for(const [node,key] of [[host,'host'],[login,'login'],[port,'port']] as const)node.querySelector('input')!.value=String(data[key]||'');
  });
  const keyPath=field(uiText("ui.ui.kit.window.server.access.form.4f85ccf66f"),'',value=>{
      const path=plainKeyPath(value);
      if(path)data.key_path=path;else delete data.key_path;
      delete data.key_text;invalidate();keyLabel.textContent='';
    },{placeholder:'~/.ssh/id_ed25519',hint:uiText('bridge.ssh.key.help')});
  const chooseKey=button(uiText("ui.ui.kit.window.server.access.form.16e7e432b4"),'quiet',async()=>{
      // The system file chooser takes a moment to appear; say that it is coming.
      const before=keyLabel.textContent;keyLabel.textContent=uiText('ssh.key.opening');chooseKey.disabled=true;
      try{const files=await pickPaperFiles(true,1,65536);if(files[0]){
        data.key_text=await files[0].text();delete data.password;delete data.key_path;
        password.querySelector('input')!.value='';keyPath.querySelector('input')!.value='';invalidate();keyLabel.textContent=files[0].name;
      }else keyLabel.textContent=before;}catch(error){keyLabel.textContent=before;out.textContent=humanError(error).text;}
      finally{chooseKey.disabled=false;}
    });
  inner.append(port,field(uiText("ui.ui.kit.window.server.access.form.94d2a5458b"),'',value=>{data.key_password=value;invalidate()},{type:'password'}));
  manual.append(grid,keyPath,chooseKey,keyLabel,extra);
  let rows:ServerProfile[]=[];let busy=false;let armed:ReturnType<typeof setTimeout>|undefined;
  const forget=button(uiText('ssh.profile.remove'),'quiet',async()=>{
    // Two clicks: the first arms, the second removes. No modal in the WebView.
    if(!armed){forget.textContent=uiText('ssh.profile.remove_confirm');armed=setTimeout(()=>{armed=undefined;forget.textContent=uiText('ssh.profile.remove');},5000);return;}
    clearTimeout(armed);armed=undefined;forget.textContent=uiText('ssh.profile.remove');forget.disabled=true;
    try{
      const result=await shell<{ok:boolean;message:string}>('server_connection',{action:'remove',request:JSON.stringify({id:data.server_profile})});
      choose(undefined);await load();out.textContent=result.message;
    }catch(error){out.textContent=humanError(error).text;}
    finally{forget.disabled=false;}
  });
  const choose=(profile?:ServerProfile)=>{
    for(const name of ['password','key_text','key_path','key_password','fingerprint','confirmed_host'])delete data[name];
    manual.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value='');keyLabel.textContent='';keyPath.querySelector('input')!.value='';
    if(profile){data.server_profile=profile.id;Object.assign(data,{host:profile.host,port:profile.port,login:profile.login});}
    else delete data.server_profile;
    for(const [node,key] of [[host,'host'],[login,'login'],[port,'port']] as const)node.querySelector('input')!.value=String(data[key]||'');
    manual.disabled=!!profile;save.hidden=!!profile||options.saveButton===false;forget.hidden=!profile;invalidate();
  };
  picker.addEventListener('change',()=>choose(rows.find(row=>row.id===picker.value)));
  const load=async()=>{
    try{
      rows=await shell<ServerProfile[]>('server_connection',{action:'list'});
      const saved=rows.filter(row=>!row.error);
      picker.replaceChildren(new Option(uiText("ui.ui.kit.window.server.access.form.1770ea4ae6"),''),...saved.map(row=>new Option(`${row.name} · ${row.login}@${row.host}`,row.id)));
      if(saved.length)pickerBox.replaceChildren(picker,forget);else pickerBox.replaceChildren();
      picker.value=String(data.server_profile||'');
      const current=rows.find(row=>row.id===picker.value);if(current)choose(current);
    }catch(error){out.textContent=humanError(error).text;}
  };
  const save=button(uiText("ui.ui.kit.window.server.access.form.cb6566caea"),'quiet',async()=>{await inspect();});
  save.hidden=options.saveButton===false;forget.hidden=true;
  async function inspect():Promise<boolean>{
    if(data.server_profile)return true;
    if(busy)return false;busy=true;save.disabled=true;manual.disabled=true;picker.disabled=true;out.classList.remove('err');out.textContent=uiText("ui.ui.kit.window.server.access.form.40f9ee7915");
    let ready=false;
    try{
      const result=await shell<Check>('server_connection',{action:'save',request:JSON.stringify(data)});
      out.textContent=result.message;trust.replaceChildren();
      if(result.trust_needed){
        data.fingerprint=result.fingerprint;
        trust.append(el('p','mono',result.fingerprint||''),button(uiText("ui.ui.kit.window.server.access.form.964f23de52"),'quiet',async()=>{
          data.confirmed_host=true;if(await inspect())options.onSaved?.();
        }));
      }else if(result.ok&&result.profile){data.server_profile=result.profile.id;await load();out.textContent=result.message;ready=true;}
    }catch(error){out.textContent=humanError(error).text;out.classList.add('err')}
    finally{busy=false;save.disabled=false;picker.disabled=false;manual.disabled=!!data.server_profile;}
    return ready;
  }
  element.save=inspect;
  element.append(pickerBox,manual,save,trust,out);void load();
  return element;
}
