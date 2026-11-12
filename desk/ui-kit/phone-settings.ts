import { el } from './dom';
import { relayAccounts } from '../app/src/relay-accounts';
import { lookCard } from './window/look';
import { serviceTierReceipt } from './service-tier';
import { effortPlan, fastModeAvailability, type ModelDetails, type ProviderKind } from './providers';
import { setEffortOptions, setTierOptions, type ModelCatalog } from './model-controls';

type Field={path:string;label:string;group:string;section:string;type:string;value:string|number|boolean|string[];stored?:boolean;min?:number;max?:number;choices?:Array<[string,string]>;hint?:string};
type Snapshot={groups:Array<[string,string]>;fields:Field[];mtime_ns:string;scope_id:string;agent:string};
type Reply={ok:boolean;mtime_ns?:string;note?:string;restart_needed?:boolean};
export type SettingsRequest=<T>(path:string,body?:unknown)=>Promise<T>;

/** The form edits the active agent's typed settings, not a browser-only draft. */
export function phoneSettings(root:HTMLElement,request:SettingsRequest) {
  root.className='phone-settings';
  const nav=el('nav','phone-settings-tabs'),body=el('div','phone-settings-body'),footer=el('div','phone-settings-save');
  nav.setAttribute('aria-label','Разделы настроек');
  const save=el('button','btn btn-primary','Сохранить') as HTMLButtonElement;
  const reload=el('button','btn btn-quiet','Перечитать') as HTMLButtonElement;
  const restart=el('button','btn btn-quiet','Перезапустить движок') as HTMLButtonElement;
  const note=el('p','receipt');note.setAttribute('role','status');
  save.type=reload.type=restart.type='button';restart.hidden=true;save.disabled=true;
  footer.append(save,reload,restart,note);root.append(nav,body,footer);
  let snapshot:Snapshot|undefined,group='',busy=false,revision=0;
  let codexModels:ModelDetails[]=[];
  let catalogGeneration=0;
  const modelRefresh=el('button','btn btn-quiet','Обновить модели') as HTMLButtonElement;
  modelRefresh.type='button';
  const modelNote=el('p','receipt');
  const modelList=document.createElement('datalist');modelList.id='phone-model-catalog';
  async function loadModels(refresh=false) {
    const generation=++catalogGeneration,scope=snapshot?.scope_id;
    modelRefresh.disabled=true;modelNote.textContent='Читаю каталог моделей…';
    try {
      const value=await request<{by_framework?:Record<string,ModelCatalog & {note?:string;ok?:boolean}>}>('/api/brain-models'+(refresh?'?refresh=1':''));
      if(generation!==catalogGeneration||snapshot?.scope_id!==scope)return;
      const catalogs=Object.values(value.by_framework||{});
      const rows=catalogs.filter(item=>item.codex===true).flatMap(item=>item.model_details||[]);
      if(rows.length)codexModels=rows;
      const ids=[...new Set(catalogs.flatMap(item=>(item.model_details||[]).map(row=>row.id)))];
      if(ids.length){modelList.replaceChildren();for(const id of ids){const option=document.createElement('option');option.value=id;modelList.append(option)}}
      modelNote.textContent=catalogs.map(item=>item.note||'').filter(Boolean).join(' ')||'Каталог пока недоступен.';
      visibility();
    }catch{if(generation===catalogGeneration)modelNote.textContent='Не удалось обновить каталог. Сохранённый выбор оставлен.'}
    finally{if(generation===catalogGeneration)modelRefresh.disabled=false}
  }
  modelRefresh.addEventListener('click',()=>void loadModels(true));
  const changes=new Map<string,unknown>(),controls=new Map<string,HTMLElement>();
  const tierObservation=el('p','receipt');tierObservation.dataset.serviceTierActual='';
  const subscription=el('section','phone-subscription');
  const subscriptionNote=el('p','field-hint'),install=el('button','btn btn-quiet','Установить praxis-relay') as HTMLButtonElement;
  install.type='button';install.hidden=true;
  type AccountState={available?:boolean;installed?:boolean;component_ready?:boolean;note?:string;slots?:Array<{slot:string;stored:boolean;active:boolean}>;login?:{pending?:boolean;slot?:string;url?:string;error?:string}};
  let accountState:AccountState={};
  async function readAccount() {
    accountState=await request<AccountState>('/api/phone/subscription');
    subscriptionNote.textContent=accountState.note||'';
    install.hidden=accountState.installed!==false||!accountState.component_ready;
    accounts.element.hidden=accountState.installed===false;
    return accountState;
  }
  const accounts=relayAccounts(async<T>(command:string,args:Record<string,unknown>={})=>{
    if(command==='relay_account') {
      const value=await readAccount();
      return {reachable:!!value.available,active_slot:value.slots?.find(row=>row.active)?.slot,
        slots:['primary','secondary'].map(slot=>{const row=value.slots?.find(row=>row.slot===slot);return {
          slot,stored:!!row?.stored,loaded:!!row?.stored,serving:!!row?.active,
          pending:value.login?.pending&&value.login.slot===slot,login_error:value.login?.error||(!value.available?value.note:undefined)}})} as T;
    }
    if(command==='relay_login_url')return (accountState.login?.url||null) as T;
    if(command==='open_login_page') {
      const value=await readAccount();const url=new URL(value.login?.url||'');
      if(url.protocol!=='https:')throw Error('Страница входа ещё не готова');
      window.open(url.href,'_blank','noopener,noreferrer');return undefined as T;
    }
    const action=({relay_login:'login',relay_logout:'logout',relay_switch:'switch',relay_cancel_login:'cancel_login'} as Record<string,string>)[command];
    if(!action)throw Error('Недоступное действие подписки');
    return await request('/api/phone/subscription',{action,slot:args.slot||'primary'}) as T;
  });
  install.addEventListener('click',async()=>{
    install.disabled=true;subscriptionNote.textContent='Устанавливаем praxis-relay…';
    try{await request('/api/phone/subscription',{action:'install'});await accounts.refresh()}
    catch(error){subscriptionNote.textContent=error instanceof Error?error.message:String(error)}
    finally{install.disabled=false}
  });
  subscription.append(el('h3','','Подписки и реле'),subscriptionNote,install,accounts.element);
  function changed(path:string,value:unknown) {
    const original=snapshot?.fields.find(row=>row.path===path);
    if(original&&original.type!=='password'&&value===original.value)changes.delete(path);else changes.set(path,value);
    revision++;save.disabled=busy||changes.size===0;
    root.dataset.dirty=String(changes.size>0);
    if(['provider','model.model','model.base_url','model.service_tier','images.enabled','telegram.mode','relay.proxy.mode'].includes(path))visibility();
  }
  function visibility() {
    const provider=changes.get('provider')??snapshot?.fields.find(row=>row.path==='provider')?.value;
    const tier=controls.get('model.service_tier')?.closest<HTMLElement>('.phone-setting');
    if(tier)tier.hidden=provider!=='chatgpt';
    const images=changes.get('images.enabled')??snapshot?.fields.find(row=>row.path==='images.enabled')?.value;
    subscription.hidden=provider!=='chatgpt'&&!images;
    for(const path of ['model.key','model.base_url']) {
      const wrapper=controls.get(path)?.closest<HTMLElement>('.phone-setting');
      if(wrapper)wrapper.hidden=provider==='chatgpt';
    }
    const phoneMode=changes.get('phone.mode')??snapshot?.fields.find(row=>row.path==='phone.mode')?.value;
    const external=controls.get('phone.external')?.closest<HTMLElement>('.phone-setting');
    if(external)external.hidden=phoneMode!=='stable';
    const current=(path:string)=>changes.get(path)??snapshot?.fields.find(row=>row.path===path)?.value;
    const details=codexModels.find(item=>item.id===String(current('model.model') || ''));
    const fastAvailable=fastModeAvailability(details);
    const tierSelect=controls.get('model.service_tier') as HTMLSelectElement|undefined;
    if(tierSelect)setTierOptions(tierSelect,fastAvailable,String(current('model.service_tier') || ''));
    const effort=controls.get('model.reasoning_effort') as HTMLSelectElement|undefined;
    if(effort) {
      const model=String(current('model.model') || '');
      const providerKind=(provider==='openai'?'api':provider) as ProviderKind;
      const plan=effortPlan(providerKind,model,String(current('model.base_url') || ''),codexModels.find(item=>item.id===model)?.reasoning_efforts);
      setEffortOptions(effort,plan.chips.length?plan.chips:[{value:'',label:'По умолчанию'}],String(current('model.reasoning_effort') || ''));
      const wrapper=effort.closest<HTMLElement>('.phone-setting');
      if(wrapper) {
        let hint=wrapper.querySelector<HTMLElement>('[data-codex-effort-hint]');
        if(!hint){hint=el('span','field-hint');hint.dataset.codexEffortHint='';wrapper.append(hint);}
        hint.textContent=provider==='chatgpt'&&current('model.reasoning_effort')==='none'
          ? 'В старой настройке сохранено «none». Сейчас применяется рассуждение модели по умолчанию.' : '';
        hint.hidden=!hint.textContent;
      }
    }
    const fastHint=controls.get('model.service_tier')?.closest<HTMLElement>('.phone-setting')?.querySelector<HTMLElement>('[data-service-tier-hint]');
    if(fastHint) {
      const original=snapshot?.fields.find(row=>row.path==='model.service_tier')?.hint || '';
      fastHint.textContent=original+(fastAvailable===null?' Каталог ещё не подтвердил ускорение этой модели.':fastAvailable===false?' Выбранная модель не объявила поддержку Fast mode.':' Фактический режим определяется ответом сервера.');
    }
    for(const [path,control] of controls) {
      if(path.startsWith('relay.proxy.')&&path!=='relay.proxy.mode')control.closest<HTMLElement>('.phone-setting')!.hidden=current('relay.proxy.mode')!=='proxy';
      if(['telegram.api_id','telegram.api_hash','telegram.phone'].includes(path))control.closest<HTMLElement>('.phone-setting')!.hidden=current('telegram.mode')!=='account';
      if(path==='telegram.bot_token')control.closest<HTMLElement>('.phone-setting')!.hidden=current('telegram.mode')==='account';
    }
  }
  function pick(id:string) {
    group=id;
    footer.hidden=id==='look';
    for(const section of body.querySelectorAll<HTMLElement>('[data-settings-group]'))section.hidden=section.dataset.settingsGroup!==id;
    for(const tab of nav.querySelectorAll<HTMLElement>('[role=tab]'))tab.setAttribute('aria-selected',String(tab.dataset.group===id));
    body.scrollTop=0;
  }
  function draw(value:Snapshot) {
    snapshot=value;changes.clear();controls.clear();nav.replaceChildren();body.replaceChildren();
    nav.setAttribute('role','tablist');
    for(const [id,title] of value.groups) {
      const tab=el('button','',''+title) as HTMLButtonElement;tab.type='button';tab.setAttribute('role','tab');tab.dataset.group=id;
      tab.addEventListener('click',()=>pick(id));nav.append(tab);
      const section=el('section','phone-settings-group');section.dataset.settingsGroup=id;
      if(id==='look')section.append(lookCard({phone:true}));
      const sections=new Map<string,HTMLElement>();
      for(const field of value.fields.filter(row=>row.group===id)) {
        let part=sections.get(field.section);
        if(!part){part=el('section','phone-settings-card');part.append(el('h3','',field.section));sections.set(field.section,part);section.append(part)}
        const label=el(field.type==='multiselect'?'fieldset':'label','phone-setting'),caption=el(field.type==='multiselect'?'legend':'span','field-label',field.label);
        let control:HTMLElement;
        if(field.type==='multiselect') {
          control=el('span','phone-settings-permissions');
          const selected=new Set(Array.isArray(field.value)?field.value:[]);
          for(const [key,title] of field.choices||[]) {
            const row=el('label','phone-permission'),input=document.createElement('input');input.type='checkbox';input.checked=selected.has(key);
            input.addEventListener('change',()=>{if(input.checked)selected.add(key);else selected.delete(key);changed(field.path,Array.from(selected))});
            row.append(el('span','',title),input);control.append(row);
          }
        } else if(field.type==='select') {
          const select=document.createElement('select');
          for(const [key,text] of field.choices||[]) {const option=document.createElement('option');option.value=key;option.textContent=text;select.append(option)}
          if(field.value&&!field.choices?.some(([key])=>key===field.value)){const option=new Option(String(field.value),String(field.value));select.append(option)}
          select.value=String(field.value??'');control=select;
          select.addEventListener('change',()=>{changed(field.path,select.value);visibility()});
        } else {
          const input=document.createElement('input');control=input;
          input.type=field.type==='boolean'?'checkbox':field.type==='password'?'password':field.type==='number'?'number':'text';
          input.autocomplete='off';input.spellcheck=false;
          if(field.type==='boolean')input.checked=field.value===true;
          else input.value=field.type==='password'?'':String(field.value??'');
          if(field.type==='password'&&field.stored)input.placeholder='Ключ сохранён';
          if(field.min!==undefined)input.min=String(field.min);if(field.max!==undefined)input.max=String(field.max);
          input.addEventListener(field.type==='boolean'?'change':'input',()=>{
            if(field.type==='password'&&!input.value){changes.delete(field.path);revision++;save.disabled=busy||changes.size===0;root.dataset.dirty=String(changes.size>0);return}
            changed(field.path,field.type==='boolean'?input.checked:field.type==='number'?Number(input.value):input.value);
          });
          if(field.type==='boolean')label.classList.add('is-toggle');
        }
        controls.set(field.path,control);label.append(caption,control);
        if(field.hint) {
          const hint=el('span','field-hint',field.hint);
          if(field.path==='model.service_tier')hint.dataset.serviceTierHint='';
          label.append(hint);
        }
        if(field.path==='model.service_tier')label.append(tierObservation);
        if(field.type==='password'&&field.stored) {
          const clear=el('button','phone-setting-clear','Удалить сохранённый ключ') as HTMLButtonElement;clear.type='button';
          clear.addEventListener('click',event=>{event.preventDefault();(control as HTMLInputElement).value='';changed(field.path,'');clear.textContent='Будет удалён после сохранения'});
          label.append(el('span','field-hint','Оставь поле пустым, чтобы сохранить прежний ключ.'),clear);
        }
        part.append(label);
      }
      if(id==='brain') {
        sections.get('Модель')?.after(subscription);
        sections.get('Telegram')?.append(el('p','field-hint','Для первого входа в личный аккаунт Telegram открой настройки на ПК. Уже подключённый аккаунт продолжает работать.'));
        sections.get('Подключение реле')?.append(el('p','field-hint','Этот маршрут относится к реле, включая вход в ChatGPT. Telegram использует свои настройки.'));
      }
      const localNotes:Record<string,string>={agent:'Создание агентов, конституция и расширения — в настройках на компьютере.',voice:'Модели Whisper и XTTS устанавливаются на компьютере. Здесь меняются настройки того же агента.',rights:'Папки песочницы и системные разрешения изменяются на компьютере.',app:'Автозапуск, установка компонентов и перенос данных доступны на компьютере. Пустая папка копий — расположение по умолчанию.'};
      if(localNotes[id])section.append(el('p','phone-settings-local-note',localNotes[id]));
      if(id==='brain') {
        section.append(modelRefresh,modelNote,modelList);
        (controls.get('model.model') as HTMLInputElement|undefined)?.setAttribute('list',modelList.id);
      }
      body.append(section);
    }
    pick(value.groups.some(([id])=>id===group)?group:value.groups[0][0]);visibility();
    save.disabled=true;root.dataset.dirty='false';
  }
  async function load() {
    if(busy)return;busy=true;save.disabled=reload.disabled=true;note.textContent='Читаю настройки агента…';
    try{
      const value=await request<Snapshot>('/api/phone/settings');draw(value);note.textContent='';
      tierObservation.textContent=serviceTierReceipt(null);
      const scope=snapshot?.scope_id;
      void request<{brain?:{service_tier_actual?:string|null}}>('/api/state').then(value=>{
        if(snapshot?.scope_id===scope)tierObservation.textContent=serviceTierReceipt(value.brain?.service_tier_actual);
      }).catch(()=>{});
      void loadModels();
    }
    catch(error){note.textContent=String(error instanceof Error?error.message:error);note.className='receipt err';}
    finally{busy=false;reload.disabled=false;save.disabled=changes.size===0;}
  }
  save.addEventListener('click',async()=>{
    if(busy||!snapshot||!changes.size)return;
    const current=(path:string)=>changes.get(path)??snapshot?.fields.find(row=>row.path===path)?.value;
    const provider=current('provider');
    const model=String(current('model.model') || '');
    const effort=String(current('model.reasoning_effort') || '');
    const plan=effortPlan((provider==='openai'?'api':provider) as ProviderKind,model,String(current('model.base_url') || ''),codexModels.find(item=>item.id===model)?.reasoning_efforts);
    if((changes.has('provider')||changes.has('model.model')||changes.has('model.reasoning_effort'))&&effort&&!plan.chips.some(chip=>chip.value===effort)) {
      note.textContent='Выбери допустимое усилие для новой модели или «По умолчанию».';note.className='receipt err';return;
    }
    busy=true;save.disabled=reload.disabled=true;note.className='receipt';note.textContent='Сохраняю настройки агента…';
    const bytes=crypto.getRandomValues(new Uint8Array(16));const operation=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    const submitted=new Map(changes),submittedRevision=revision;
    if((changes.has('provider')||changes.has('model.model'))&&['priority','fast'].includes(String(current('model.service_tier')))&&(provider!=='chatgpt'||fastModeAvailability(codexModels.find(item=>item.id===model))!==true))submitted.set('model.service_tier','');
    try {
      let reply:Reply;
      try {reply=await request<Reply>('/api/phone/settings',{changes:Object.fromEntries(submitted),mtime_ns:snapshot.mtime_ns,scope_id:snapshot.scope_id,operation})}
      catch(error) {
        // Read back the SAME write receipt; an uncertain POST is never retried.
        const receipt=await request<{state?:string;mtime_ns?:string}>('/api/phone/settings/receipt?operation='+operation).catch(()=>null);
        if(receipt?.state!=='saved')throw error;
        reply={ok:true,mtime_ns:receipt.mtime_ns,note:'Сохранение подтверждено. Подключение обновляется.'};
      }
      if(!reply.ok)throw Error(reply.note||'Сохранение не подтвердилось');
      if(reply.mtime_ns)snapshot.mtime_ns=reply.mtime_ns;
      for(const [path,value] of submitted) {
        const row=snapshot.fields.find(row=>row.path===path);if(row){row.value=row.type==='password'?'':value as Field['value'];if(row.type==='password')row.stored=!!value}
        if(revision===submittedRevision||changes.get(path)===value)changes.delete(path);
      }
      note.textContent=(reply.note||'Сохранено.')+(changes.size?' Есть новые несохранённые правки.':'');
      note.className='receipt ok';restart.hidden=!reply.restart_needed;root.dataset.dirty=String(changes.size>0);
      visibility();
      void accounts.refresh();
    }catch(error){note.textContent=(error instanceof Error?error.message:String(error))+' Правки остались в форме.';note.className='receipt err';}
    finally{busy=false;reload.disabled=false;save.disabled=changes.size===0;}
  });
  reload.addEventListener('click',()=>void load());
  restart.addEventListener('click',async()=>{
    restart.disabled=true;note.textContent='Передаю просьбу о перезапуске…';
    try {await request('/api/supervisor/restart',{target:'all'});note.textContent='Перезапуск запрошен. Состояние — на экране «Система».';}
    catch(error){note.textContent=error instanceof Error?error.message:String(error)}
    finally{restart.disabled=false}
  });
  void load();
  return {reload:load,dirty:()=>changes.size>0};
}
