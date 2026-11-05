/** Fish performance controls over the actual server API. No vendor key in UI. */
type Selection = {provider:string;enabled:boolean;revision:string;reference_id?:string;model?:string};
type Status = {ok:boolean;selection:Selection;fish:{configured:boolean;authenticated?:boolean;api_credit_raw?:string;error?:string}};
type Voice = {id:string;name:string;model:string;description?:string;author?:string;tags?:string[];languages?:string[];licensed?:boolean;task_count?:number};
type Catalog = {voices:Voice[];has_more:boolean;page:number;total?:number;total_is_exact?:boolean;window_limited?:boolean};
type Job = {id:string;state:string;error?:string};
type AudioResult = {data:string;mime:string};
type Channel = {base?:string;key?:string};

const esc=(value:string)=>value.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const uid=()=>crypto.randomUUID().replace(/-/g,'');
const operations=new Map<string,{intent:string;request_id:string}>();

// Retain the exact operation identity across a lost response or page refresh.
// This storage contains the preview intent, never a provider/channel credential.
function requestFor(kind:string,intent:Record<string,unknown>):Record<string,unknown>&{request_id:string} {
  const key='praxis.fish.operation.'+kind,serialized=JSON.stringify(intent);
  try{
    const previous=operations.get(kind)||JSON.parse(sessionStorage.getItem(key)||'null');
    if(previous?.intent===serialized&&/^[a-f0-9]{32}$/.test(previous.request_id))return {...intent,request_id:previous.request_id};
  }catch{/* Memory-only operation still works where storage is unavailable. */}
  const request_id=uid();
  operations.set(kind,{intent:serialized,request_id});
  try{sessionStorage.setItem(key,JSON.stringify({intent:serialized,request_id}));}catch{/* Server still retains this request identity. */}
  return {...intent,request_id};
}
function forgetPreview(){operations.delete('preview');try{sessionStorage.removeItem('praxis.fish.operation.preview');}catch{/* Optional browser state. */}}

async function api<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T> {
  const surface=window as unknown as {PULT_CONFIG_OVERRIDE?:Channel;PULT_CONFIG?:Channel;DESK_CONFIG_OVERRIDE?:Channel;DESK_CONFIG?:Channel};
  const cfg=surface.PULT_CONFIG_OVERRIDE||surface.PULT_CONFIG||surface.DESK_CONFIG_OVERRIDE||surface.DESK_CONFIG||{};
  let key=cfg.key||'';
  try{key=key||localStorage.getItem('frame.tg.device')||'';}catch{/* Cookie auth remains available. */}
  const response=await fetch((cfg.base||'')+path,{method:body===undefined?'GET':'POST',cache:'no-store',credentials:'same-origin',signal,
    headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(key?{Authorization:'Bearer '+key}:{})},
    body:body===undefined?undefined:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok||result.ok===false)throw new Error(result.error||'unavailable');
  return result as T;
}

const messages:Record<string,string>={unauthorized:'Нет доступа к настройкам голоса.',fish_no_api_credit:'На API-балансе Fish нет средств.',
  fish_key_unavailable:'API-ключ Fish ещё не настроен на сервере.',fish_forbidden:'Fish отказал в доступе.',
  config_changed:'Настройки уже изменились. Обновите состояние перед выбором.',voice_conflict:'Этот запрос уже использован для другого действия.',
  fish_rate_limited:'Fish ограничил частоту запросов.',invalid_text:'Введите текст для пробы.',
  profile_changed:'Профиль голоса изменился. Обновите каталог.',unavailable:'Сервер озвучки недоступен.',
  fish_invalid_reference:'Вставьте ID голоса или ссылку https://fish.audio/m/…',fish_not_found:'Такой голос не найден или недоступен.',
  fish_invalid_request:'Проверьте параметры поиска.'};

export function mountFishControls(root:HTMLElement,legacy:HTMLElement[]=[]):()=>void {
  const aborter=new AbortController();let disposed=false,status:Status|undefined,page=1,catalog:Catalog|undefined;
  let view='fish',job='',version=0,url='',signature='',catalogVersion=0,timer:ReturnType<typeof setTimeout>|undefined;
  let linked:Voice|undefined,currentVoice:Voice|undefined,currentReference='',resolveVersion=0;
  let search={query:'',language:'ru',tag:'',sort_by:'score',licensed:false};
  root.classList.add('fish-panel');
  root.innerHTML=`<h2 class="screen-title">Озвучка и интонация</h2><div class="xtts-body">
    <p data-fish-status class="muted" role="status">Проверяю сервер…</p>
    <div class="actions"><label>Показать<select data-fish-view><option value="fish">Fish Audio</option><option value="xtts">XTTS</option></select></label>
    <button class="chip" type="button" data-fish-refresh>Обновить</button><button class="chip" type="button" data-fish-power>Приостановить озвучку</button></div>
    <p data-fish-note class="muted" role="status" aria-live="polite"></p>
    <div data-fish-xtts hidden><button class="chip" type="button" data-fish-use-xtts>Использовать XTTS</button><p class="muted">Настройки локального голоса находятся ниже.</p></div>
    <div data-fish-content>
      <label>Модель<select data-fish-model><option value="drama-3">Drama 3 — управление подачей</option><option value="s2.1-pro">S2.1 Pro</option></select></label>
      <label>Текст для пробы<textarea data-fish-text rows="2" maxlength="400">Я здесь. Теперь я могу говорить с нужной интонацией.</textarea></label>
      <label>Как произнести<textarea data-fish-delivery rows="3" placeholder="Тепло, с лёгкой улыбкой. Последнюю фразу — тише."></textarea></label>
      <p class="muted">Подачу можно описать своими словами. Указания [в квадратных скобках] меняют подачу следующих фраз. Drama 3 также поддерживает &lt;whisper&gt;слово&lt;/whisper&gt; и другие акценты.</p>
      <p class="muted">Прослушивание расходует API-баланс. Проба и выбор голоса ничего не отправляют в Telegram.</p>
      <div data-fish-player hidden><span data-fish-playing></span><audio controls preload="none"></audio></div>
      <button class="chip" type="button" data-fish-cancel hidden>Остановить пробу</button>
      <div data-fish-current></div>
      <h3>Найти голос</h3><label>Название<input type="search" data-fish-query maxlength="512" placeholder="Juniper, рассказчик…"></label>
      <div class="actions"><label>Язык каталога<select data-fish-language><option value="ru">Русский</option><option value="">Все языки</option><option value="en">Английский</option><option value="de">Немецкий</option><option value="fr">Французский</option><option value="es">Испанский</option><option value="zh">Китайский</option><option value="ja">Японский</option></select></label>
      <label>Порядок<select data-fish-sort><option value="score">Рекомендации Fish</option><option value="task_count">Часто используют</option><option value="created_at">Новые</option></select></label></div>
      <div class="actions"><button class="chip" type="button" data-fish-tag-pick="female">Женский</button><button class="chip" type="button" data-fish-tag-pick="male">Мужской</button><button class="chip" type="button" data-fish-tag-pick="calm">Спокойный</button><button class="chip" type="button" data-fish-tag-pick="warm">Тёплый</button><button class="chip" type="button" data-fish-tag-pick="conversational">Разговорный</button></div>
      <label>Теги через запятую<input data-fish-tag maxlength="256" placeholder="female, calm"></label>
      <label class="fish-check"><input type="checkbox" data-fish-licensed> Только лицензированные Fish</label>
      <div class="actions"><button class="chip" type="button" data-fish-search>Найти</button><a href="https://fish.audio/app/discovery/" target="_blank" rel="noopener noreferrer">Открыть каталог Fish ↗</a></div>
      <div data-fish-voices class="xtts-voices"></div><div class="actions"><button class="chip" type="button" data-fish-prev>Назад</button><span data-fish-page></span><button class="chip" type="button" data-fish-next>Дальше</button></div>
      <h3>Голос с сайта Fish</h3><label>Ссылка или ID<input data-fish-reference maxlength="2048" placeholder="https://fish.audio/m/…"></label>
      <button class="chip" type="button" data-fish-resolve>Открыть голос</button><div data-fish-linked></div>
    </div></div>`;
  const find=<T extends HTMLElement>(selector:string)=>root.querySelector<T>(selector)!;
  const audio=find<HTMLAudioElement>('audio');
  const note=(text:string,bad=false)=>{if(!disposed){const node=find<HTMLElement>('[data-fish-note]');node.textContent=text;node.className=bad?'err':'muted';}};
  const failure=(error:unknown)=>messages[error instanceof Error?error.message:'']||'Действие не завершилось. Проверьте состояние перед повтором.';
  const renderView=()=>{
    find<HTMLElement>('[data-fish-content]').hidden=view!=='fish';find<HTMLElement>('[data-fish-xtts]').hidden=view!=='xtts';
    for(const node of legacy){node.hidden=view!=='xtts';node.inert=status?.selection.provider!=='xtts';}
    find<HTMLButtonElement>('[data-fish-use-xtts]').hidden=status?.selection.provider==='xtts';
  };
  const showVoices=()=>{
    const voices=catalog?.voices||[];
    const next=JSON.stringify([catalog,currentVoice,linked,status?.selection.reference_id,page]);if(next===signature)return;signature=next;
    const card=(v:Voice)=>`<div class="task-row"><b>${esc(v.name)}</b>${status?.selection.reference_id===v.id?' <span class="muted">· выбран</span>':''}
      <p class="muted">${esc([v.author,...(v.languages||[]),v.licensed?'Лицензирован Fish':''].filter(Boolean).join(' · '))}</p>
      ${v.description?`<p class="fish-description">${esc(v.description)}</p>`:''}
      ${(v.tags||[]).length?`<p class="muted">${esc(v.tags!.join(' · '))}</p>`:''}
      <div class="actions"><button class="chip" type="button" data-fish-preview="${esc(v.id)}">Послушать</button><button class="chip" type="button" data-fish-select="${esc(v.id)}">Выбрать и включить</button><a href="https://fish.audio/m/${encodeURIComponent(v.id)}/" target="_blank" rel="noopener noreferrer">На Fish ↗</a></div></div>`;
    find<HTMLElement>('[data-fish-current]').innerHTML=currentVoice?'<h3>Текущий голос</h3>'+card(currentVoice):'';
    find<HTMLElement>('[data-fish-linked]').innerHTML=linked?card(linked):'';
    find<HTMLElement>('[data-fish-voices]').innerHTML=voices.map(card).join('')||'<p class="muted">Голосов не найдено. Попробуйте другой запрос или выберите все языки.</p>';
    find<HTMLButtonElement>('[data-fish-prev]').disabled=page===1;find<HTMLButtonElement>('[data-fish-next]').disabled=!catalog?.has_more;
    find<HTMLElement>('[data-fish-page]').textContent=`Страница ${page}${catalog?.total!==undefined&&catalog.total_is_exact?' · найдено '+catalog.total:''}`;
  };
  async function refresh(){
    status=await api<Status>('/api/speech/status',undefined,aborter.signal);if(disposed)return;
    const selected=status.selection;
    if(selected.provider==='fish'&&selected.reference_id!==currentReference){
      currentReference=selected.reference_id||'';currentVoice=undefined;
      const reference=currentReference;
      if(reference)void api<{voice:Voice}>('/api/speech/fish/resolve',{reference,model:selected.model||'drama-3'},aborter.signal)
        .then(result=>{if(!disposed&&reference===currentReference){currentVoice=result.voice;showVoices();}}).catch(()=>{/* Status is still shown if the model card is temporarily unavailable. */});
    }else if(selected.provider!=='fish'){currentReference='';currentVoice=undefined;}
    find<HTMLElement>('[data-fish-status]').textContent=`Активный движок: ${selected.provider==='fish'?'Fish Audio':'XTTS'} · ${selected.enabled?'озвучка включена':'озвучка приостановлена'}. Fish API: ${status.fish.authenticated?'подключён':'не подтверждён'}${status.fish.api_credit_raw!==undefined?' · баланс '+status.fish.api_credit_raw:''}.`;
    find<HTMLButtonElement>('[data-fish-power]').textContent=selected.enabled?'Приостановить озвучку':'Включить озвучку';
    renderView();showVoices();
  }
  async function loadCatalog(){const current=++catalogVersion;const result=await api<Catalog>('/api/speech/fish/search',{...search,page,model:find<HTMLSelectElement>('[data-fish-model]').value},aborter.signal);if(!disposed&&current===catalogVersion){catalog=result;showVoices();}}
  async function applySearch(){
    search={query:find<HTMLInputElement>('[data-fish-query]').value,language:find<HTMLSelectElement>('[data-fish-language]').value,
      tag:find<HTMLInputElement>('[data-fish-tag]').value,sort_by:find<HTMLSelectElement>('[data-fish-sort]').value,
      licensed:find<HTMLInputElement>('[data-fish-licensed]').checked};
    page=1;await loadCatalog();
  }
  function stop(cancel=true){
    version++;const old=job;job='';audio.pause();if(url)URL.revokeObjectURL(url);url='';audio.removeAttribute('src');
    find<HTMLElement>('[data-fish-player]').hidden=true;find<HTMLElement>('[data-fish-cancel]').hidden=true;
    if(old&&cancel)void api(`/api/speech/fish/jobs/${old}/cancel`,{}).catch(()=>undefined);
  }
  async function preview(voice:Voice){
    const request=requestFor('preview',{voice_id:voice.id,model:find<HTMLSelectElement>('[data-fish-model]').value,
      text:find<HTMLTextAreaElement>('[data-fish-text]').value,delivery:find<HTMLTextAreaElement>('[data-fish-delivery]').value});
    stop(job!==request.request_id);const current=version;job=request.request_id;const id=job;
    find<HTMLElement>('[data-fish-cancel]').hidden=false;note(`Готовлю пробу: ${voice.name}…`);
    let result=await api<Job>('/api/speech/fish/jobs',request,aborter.signal);
    while(!disposed&&version===current&&!['ready','failed','cancelled','unknown','cancel_requested'].includes(result.state)){
      await new Promise(resolve=>setTimeout(resolve,650));
      result=await api<Job>(`/api/speech/fish/jobs/${id}`,undefined,aborter.signal);
    }
    if(disposed||version!==current)return;
    find<HTMLElement>('[data-fish-cancel]').hidden=true;
    if(result.state==='unknown'||result.state==='cancel_requested'){note('Результат не подтверждён. Fish мог принять запрос; автоматически повторять пробу не буду.',true);return;}
    if(result.state!=='ready'){forgetPreview();throw new Error(result.error||result.state);}
    const data=await api<AudioResult>(`/api/speech/fish/jobs/${id}/audio`,undefined,aborter.signal);
    if(disposed||version!==current)return;
    const raw=atob(data.data),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));url=URL.createObjectURL(new Blob([bytes],{type:data.mime}));
    audio.src=url;find<HTMLElement>('[data-fish-playing]').textContent=voice.name;find<HTMLElement>('[data-fish-player]').hidden=false;job='';
    note('Проба готова. Действующий голос не изменён.');void audio.play().catch(()=>note('Нажмите ▶ для прослушивания.'));
  }
  async function select(provider:string,voice?:Voice,enabled=true){
    if(!status)return;
    const request=requestFor('selection',{provider,voice_id:voice?.id,model:find<HTMLSelectElement>('[data-fish-model]').value,
      enabled,expected_revision:status.selection.revision});
    await api('/api/speech/selection',request,aborter.signal);
    note('Настройки голоса сохранены на сервере.');await refresh();
  }
  const click=(event:Event)=>{
    const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button');if(!button||!root.contains(button))return;
    const run=async()=>{
      if(button.hasAttribute('data-fish-refresh')){currentReference='';await refresh();return loadCatalog();}
      if(button.hasAttribute('data-fish-cancel')){stop();forgetPreview();note('Локальная проба остановлена. Остановка генерации у Fish не подтверждается.');return;}
      if(button.hasAttribute('data-fish-power'))return select(status!.selection.provider,undefined,!status!.selection.enabled);
      if(button.hasAttribute('data-fish-use-xtts'))return select('xtts');
      if(button.hasAttribute('data-fish-prev')){page=Math.max(1,page-1);return loadCatalog();}
      if(button.hasAttribute('data-fish-next')){page++;return loadCatalog();}
      if(button.hasAttribute('data-fish-search'))return applySearch();
      if(button.dataset.fishTagPick){
        const input=find<HTMLInputElement>('[data-fish-tag]'),tag=button.dataset.fishTagPick;
        const tags=input.value.split(',').map(v=>v.trim()).filter(Boolean);
        input.value=(tags.includes(tag)?tags.filter(v=>v!==tag):[...tags,tag]).join(', ');return applySearch();
      }
      if(button.hasAttribute('data-fish-resolve')){
        const version=++resolveVersion;
        const result=await api<{voice:Voice}>('/api/speech/fish/resolve',{reference:find<HTMLInputElement>('[data-fish-reference]').value,
          model:find<HTMLSelectElement>('[data-fish-model]').value},aborter.signal);
        if(!disposed&&version===resolveVersion){linked=result.voice;showVoices();note('Голос открыт. Действующий голос не изменён.');}return;
      }
      const id=button.dataset.fishPreview||button.dataset.fishSelect;
      const voice=[...(catalog?.voices||[]),...(linked?[linked]:[]),...(currentVoice?[currentVoice]:[])].find(v=>v.id===id);if(!voice)return;
      if(button.dataset.fishPreview)return preview(voice);
      if(button.dataset.fishSelect)return select('fish',voice);
    };
    button.disabled=true;void run().catch(error=>note(failure(error),true)).finally(()=>{if(!disposed){button.disabled=false;showVoices();}});
  };
  root.addEventListener('click',click);
  for(const name of ['query','tag','reference'])find<HTMLInputElement>(`[data-fish-${name}]`).addEventListener('keydown',event=>{
    if(event.key==='Enter'){event.preventDefault();find<HTMLButtonElement>(name==='reference'?'[data-fish-resolve]':'[data-fish-search]').click();}
  });
  find<HTMLSelectElement>('[data-fish-view]').addEventListener('change',event=>{view=(event.target as HTMLSelectElement).value;renderView();});
  const poll=async()=>{if(disposed)return;try{await refresh();}catch(error){note(failure(error),true);}if(!disposed)timer=setTimeout(poll,5000);};
  void poll();void loadCatalog().catch(error=>note(failure(error),true));
  return()=>{disposed=true;aborter.abort();if(timer)clearTimeout(timer);stop(false);root.removeEventListener('click',click);for(const node of legacy){node.hidden=false;node.inert=false;}};
}

export function installFishPanel():()=>void {
  const mounted=new Map<HTMLElement,()=>void>();let pending=false;
  const reconcile=()=>{
    pending=false;
    for(const [group,dispose] of mounted)if(!group.isConnected){dispose();mounted.delete(group);}
    const group=document.querySelector<HTMLElement>('#praxis-voice-group');
    if(group&&!mounted.has(group)){
      const legacy=Array.from(group.children).filter((v):v is HTMLElement=>v instanceof HTMLElement);
      const root=document.createElement('section');root.dataset.fishControls='true';group.prepend(root);
      mounted.set(group,mountFishControls(root,legacy));
    }
  };
  const observer=new MutationObserver(()=>{if(!pending){pending=true;queueMicrotask(reconcile);}});
  observer.observe(document.querySelector('#app')||document.body,{childList:true,subtree:true});reconcile();
  return()=>{observer.disconnect();for(const dispose of mounted.values())dispose();mounted.clear();};
}

if(document.querySelector('script[data-praxis-fish]'))installFishPanel();
