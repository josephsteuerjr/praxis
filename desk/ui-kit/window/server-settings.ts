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
  const info=el('details','fold');info.append(el('summary','','ⓘ Как устроить серверный мост'),
    el('p','field-hint','Мост помогает Hélène выходить в Telegram через твой сервер и открывает программу с телефона по постоянному HTTPS. Агент остаётся на этом компьютере; для переселения есть отдельные действия.'),
    el('p','field-hint','Автоматическая настройка: Ubuntu/Debian с systemd, доступ администратора по SSH. Для HTTPS нужен домен с записью A/AAAA на адрес сервера и открытые порты 80/443. Программа покажет отпечаток и конкретные изменения, добавит отдельного пользователя и службы, сохранит прежние настройки.'),
    el('p','field-hint','Самостоятельная настройка: tinyproxy должен слушать только 127.0.0.1; для автоматического Cloudflare разреши CONNECT 443 и 7844. SSH-ключу разреши нужные пробросы. HTTPS-сайт направь на обратный loopback-порт, настрой выпуск и продление сертификата. Затем выбери «Сервер уже настроен» и проверь соединение здесь.'),
    el('p','field-hint','Если связь пропала, проверь мост и повтори подключение. Занятые чужие порты программа не освобождает. Для отключения выбери другой маршрут в «Мозг и связь»; службы и данные сервера останутся на месте. Автозапуск самой Hélène находится в разделе программы.'));
  info.append(button('Скачать полную инструкцию','quiet',()=>{
    const url=URL.createObjectURL(new Blob([serverBridgeManual],{type:'text/markdown;charset=utf-8'}));
    const link=el('a');link.href=url;link.download='Helene-server-bridge.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }));
  const install=button('Установить серверный мост','quiet',async()=>{
    install.disabled=true;
    try{await shell('component_install',{name:'server-bridge'});note.textContent='Компонент готов; перезапуск не нужен.';await refresh();}
    catch(error){note.textContent=humanError(error).text;}
    finally{install.disabled=false;}
  });
  const add=button('Настроить подключение','quiet',()=>drawSetup());
  const reload=button('Обновить состояние серверов','quiet',()=>void refresh());
  const actions=el('div','actions');actions.append(install,add,reload);
  async function inspectBridge(bridge:ServerBridge,out:HTMLElement) {
    if(busy)return;busy=true;out.parentElement?.querySelector('.bridge-use')?.remove();
    out.textContent='Проверяю соединение через мост…';
    try{
      const result=await command<{ok:boolean;checks:Array<{message:string}>}>('check',{id:bridge.id});
      out.textContent=result.checks.map(check=>check.message).join(' ');
      if(!result.ok)throw new Error('Подключение не подтверждено.');
      const use=button('Использовать для этого агента','primary',async()=>{
        use.disabled=true;
        try{const message=await onUse(bridge);out.textContent=message||'Подключение выбрано. Сохрани настройки, чтобы применить его.';}
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
        const current=state.states[bridge.id];row.append(el('p','receipt',current?.message||'Сохранено. Проверь подключение перед использованием.'));
        const out=el('p','receipt');const buttons=el('div','actions');
        buttons.append(button('Проверить соединение','quiet',()=>void inspectBridge(bridge,out)),
          button('Повторить подключение','quiet',async()=>{try{await command('retry',{id:bridge.id});await refresh()}catch(error){out.textContent=humanError(error).text}}));
        row.append(buttons,out);list.append(row);
      }
      const pending=await command<Operation[]>('operations');operations.replaceChildren();
      for(const operation of pending){
        const row=el('section','');row.append(el('h4','',operation.name||'Настройка сервера'),el('p','receipt',operation.message));
        if(operation.plan){
          const changes=el('ul','field-hint');for(const change of operation.plan.changes)changes.append(el('li','',change.message));row.append(changes);
          row.append(button(operation.phase==='planned'?'Применить эти изменения':'Продолжить этот план','primary',async()=>{
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
    const domainField=field('Домен для HTTPS','',value=>domain=value,{placeholder:'helene.example.org'});
    const externalField=field('Готовый HTTPS-адрес','',value=>external=value,{placeholder:'https://helene.example.org'});externalField.hidden=true;
    httpFields.append(domainField,externalField);
    const choice=el('div','actions');
    const auto=button('Настроить сервер автоматически','quiet',()=>{existing=false;auto.setAttribute('aria-pressed','true');manual.setAttribute('aria-pressed','false');domainField.hidden=false;externalField.hidden=true;});
    const manual=button('Сервер уже настроен','quiet',()=>{existing=true;auto.setAttribute('aria-pressed','false');manual.setAttribute('aria-pressed','true');domainField.hidden=true;externalField.hidden=false;});
    auto.setAttribute('aria-pressed','true');choice.append(auto,manual);
    const proxyFields=el('div','form-grid two');proxyFields.append(
      field('Локальный порт прокси','8888',value=>localPort=Number(value),{type:'number'}),
      field('Порт tinyproxy на сервере','8888',value=>remoteProxy=Number(value),{type:'number'}));
    const reverseFields=el('div');reverseFields.hidden=true;reverseFields.append(
      field('Порт обратного туннеля на сервере','18766',value=>remoteApp=Number(value),{type:'number'}),
      field('Публичный IP сервера · необязательно','',value=>publicAddress=value,{hint:'Если SSH идёт через частную сеть или другой адрес, укажи публичный IPv4/IPv6 из панели сервера. На него должна указывать DNS-запись домена.'}));
    extras.append(proxyFields,reverseFields);
    const advanced=el('details','fold');advanced.append(el('summary','','Дополнительные параметры'),extras);
    const submit=button('Проверить и подготовить','primary',async()=>{
      if(!data.server_profile){out.textContent='Сначала проверь и сохрани SSH-подключение выше.';return;}
      submit.disabled=true;out.textContent=existing?'Проверяю сохранённый сервер…':'Проверяю сервер и готовлю список изменений…';
      try{
        if(existing){
          const ports:Record<string,number>={};if(proxy)Object.assign(ports,{local_proxy_port:localPort,remote_proxy_port:remoteProxy});if(https)ports.remote_app_port=remoteApp;
          const result=await command<{ok:boolean;bridge:ServerBridge;message:string}>('add-existing',{server_profile:data.server_profile,name,external:https?external:'',ports});
          await refresh();await inspectBridge(result.bridge,out);
        }else{
          await command('prepare',{server_profile:data.server_profile,name,proxy,https,domain,public_address:publicAddress,local_proxy_port:localPort,remote_proxy_port:remoteProxy,remote_app_port:remoteApp});
          out.textContent='План готов. Просмотри изменения ниже и подтверди установку.';await refresh();
        }
      }catch(error){out.textContent=humanError(error).text;}
      finally{submit.disabled=false;}
    });
    setup.replaceChildren(el('h4','','Подключение сервера'),choice,field('Название подключения','',value=>name=value),
      form,toggle('Выход в Telegram через сервер',true,value=>{proxy=value;proxyFields.hidden=!value}),
      toggle('Постоянный HTTPS для Mini App',false,value=>{https=value;httpFields.hidden=!value;reverseFields.hidden=!value}),httpFields,advanced,submit,out);
  }
  body.append(el('p','field-hint','Подключения сохраняются на этом компьютере и доступны после обновления Hélène. Настрой новый сервер или подключи уже работающий.'),
    actions,info,setup,operations,list,note);void refresh();return card('Серверный мост',body);
}
