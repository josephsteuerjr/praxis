import { shell } from './api';
import { button, el, field } from '../dom';
import { humanError } from './lib';
import { pickPaperFiles } from './paper-files';

export type ServerProfile={id:string;name:string;host:string;port:number;login:string;kind?:string;error?:boolean};
type Check={ok:boolean;trust_needed?:boolean;fingerprint?:string;message:string;profile?:ServerProfile};

/** Shared SSH input and saved profiles for bridge, install, move and update. */
export function sshAccessForm(data:Record<string,unknown>,changed:()=>void) {
  const element=el('div');const out=el('p','receipt');const trust=el('div');
  const picker=el('select','field-input');picker.setAttribute('aria-label','Сохранённое подключение сервера');
  const manual=el('fieldset','');manual.style.border='0';manual.style.padding='0';manual.style.margin='12px 0';
  const grid=el('div','form-grid two');const extra=el('details','fold');const inner=el('div','fold-body');
  extra.append(el('summary','','SSH-ключ и дополнительные параметры'),inner);
  const invalidate=()=>{delete data.fingerprint;delete data.confirmed_host;trust.replaceChildren();out.textContent='';changed();};
  const host=field('Адрес сервера',String(data.host||''),value=>{data.host=value;invalidate()},{placeholder:'IP или имя из панели сервера'});
  const login=field('Логин',String(data.login||'root'),value=>{data.login=value;invalidate()});
  const password=field('Пароль или SSH-ключ','',value=>{data.password=value;delete data.key_text;invalidate()},
    {type:'password',hint:'Можно вставить приватный SSH-ключ целиком — программа распознает его. Данные остаются на компьютере с Hélène.'});
  const keyLabel=el('p','receipt');
  password.querySelector('input')!.addEventListener('paste',event=>{
    const value=event.clipboardData?.getData('text')||'';
    if(!/^\s*-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(value))return;
    event.preventDefault();data.password=value;delete data.key_text;delete data.key_path;
    password.querySelector('input')!.value='';invalidate();keyLabel.textContent='SSH-ключ вставлен.';
  });
  grid.append(host,login,password);
  const port=field('Порт SSH',String(data.port||22),value=>{data.port=Number(value);invalidate()},{type:'number'});
  element.addEventListener('ssh-fields-updated',()=>{
    for(const [node,key] of [[host,'host'],[login,'login'],[port,'port']] as const)node.querySelector('input')!.value=String(data[key]||'');
  });
  inner.append(port,field('Путь к SSH-ключу на компьютере с Hélène','',value=>{data.key_path=value;delete data.key_text;invalidate()}),
    field('Пароль SSH-ключа','',value=>{data.key_password=value;invalidate()},{type:'password'}),
    button('Выбрать SSH-ключ','quiet',async()=>{
      try{const files=await pickPaperFiles(true,1,65536);if(files[0]){
        data.key_text=await files[0].text();delete data.password;delete data.key_path;
        password.querySelector('input')!.value='';invalidate();keyLabel.textContent=files[0].name;
      }}catch(error){out.textContent=humanError(error).text;}
    }),keyLabel);
  manual.append(grid,extra);
  let rows:ServerProfile[]=[];let busy=false;
  const choose=(profile?:ServerProfile)=>{
    for(const name of ['password','key_text','key_path','key_password','fingerprint','confirmed_host'])delete data[name];
    manual.querySelectorAll<HTMLInputElement>('input[type="password"]').forEach(input=>input.value='');keyLabel.textContent='';
    if(profile){data.server_profile=profile.id;Object.assign(data,{host:profile.host,port:profile.port,login:profile.login});}
    else delete data.server_profile;
    for(const [node,key] of [[host,'host'],[login,'login'],[port,'port']] as const)node.querySelector('input')!.value=String(data[key]||'');
    manual.disabled=!!profile;save.hidden=!!profile;invalidate();
  };
  picker.addEventListener('change',()=>choose(rows.find(row=>row.id===picker.value)));
  const load=async()=>{
    try{
      rows=await shell<ServerProfile[]>('server_connection',{action:'list'});
      picker.replaceChildren(new Option('Новое подключение',''),...rows.filter(row=>!row.error).map(row=>new Option(`${row.name} · ${row.login}@${row.host}`,row.id)));
      picker.value=String(data.server_profile||'');
      const current=rows.find(row=>row.id===picker.value);if(current)choose(current);
    }catch(error){out.textContent=humanError(error).text;}
  };
  const save=button('Проверить и сохранить подключение','quiet',async()=>await inspect());
  async function inspect(){
    if(busy)return;busy=true;save.disabled=true;manual.disabled=true;picker.disabled=true;out.classList.remove('err');out.textContent='Проверяю подключение по SSH…';
    try{
      const result=await shell<Check>('server_connection',{action:'save',request:JSON.stringify(data)});
      out.textContent=result.message;trust.replaceChildren();
      if(result.trust_needed){
        data.fingerprint=result.fingerprint;
        trust.append(el('p','mono',result.fingerprint||''),button('Отпечаток совпадает — продолжить','quiet',()=>{data.confirmed_host=true;void inspect()}));
      }else if(result.ok&&result.profile){data.server_profile=result.profile.id;await load();out.textContent=result.message;}
    }catch(error){out.textContent=humanError(error).text;out.classList.add('err')}
    finally{busy=false;save.disabled=false;picker.disabled=false;manual.disabled=!!data.server_profile;}
  }
  element.append(picker,manual,save,trust,out);void load();
  return element;
}
