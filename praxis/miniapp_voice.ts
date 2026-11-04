/* Standalone voice component; the legacy adapter preserves the serving app. */
type Voice = { id:string; name:string; kind:'builtin'|'clone'; profile_id:string; model_id:string; seconds?:number };
type State = { ok:boolean; available:boolean; model_ready:boolean; phase:string; busy:boolean; enabled:boolean;
  selected_voice:string; selection_revision:string; voices:Voice[] };
type Job = { ok:boolean; id:string; state:string; phase:string; error?:string; audio_ready?:boolean };
type Config = { base?:string; key?:string };
declare global { interface Window { DESK_CONFIG?:Config; DESK_CONFIG_OVERRIDE?:Config; PULT_CONFIG?:Config; PULT_CONFIG_OVERRIDE?:Config } }

const esc=(value:unknown)=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const uid=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
const errors:Record<string,string>={
  unavailable:'Нет связи с голосовой службой. Попробуйте ещё раз.', unauthorized:'Нужно снова открыть MiniApp из Telegram.',
  busy:'Сейчас готовится другая реплика. Подождите немного.', warming:'Голосовая модель прогревается.',
  reference_duration:'Нужна запись одного голоса от 3 до 60 секунд.', reference_too_large:'Выберите запись размером до 8 МБ.',
  invalid_reference:'Не удалось прочитать аудио. Выберите другую запись.', invalid_voice_name:'Введите название голоса — до 80 знаков.',
  profile_changed:'Профиль изменился. Обновите список и прослушайте голос снова.', reference_changed:'Образец голоса изменился. Добавьте его заново.',
  config_changed:'Настройки уже изменились. Повторите выбор.', voice_not_configured:'Сначала выберите голос.',
  active_voice:'Сначала приостановите озвучку этим голосом.', voice_deleted:'Этот голос уже удалён.',
  cancelled:'Подготовка отменена.', timeout:'Подготовка заняла слишком много времени. Попробуйте ещё раз.',
};
const explain=(error:unknown)=>errors[error instanceof Error?error.message:'']||'Не удалось завершить действие. Попробуйте ещё раз.';

async function api<T>(path:string,body?:unknown,signal?:AbortSignal):Promise<T> {
  const config=window.PULT_CONFIG_OVERRIDE||window.PULT_CONFIG||window.DESK_CONFIG_OVERRIDE||window.DESK_CONFIG||{};
  let key=config.key||'';
  try { key=key||localStorage.getItem('frame.tg.device')||''; } catch { /* cookie remains usable */ }
  const response=await fetch((config.base||'')+path,{method:body===undefined?'GET':'POST',credentials:'include',cache:'no-store',signal,
    headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(key?{'Authorization':'Bearer '+key}:{})},
    body:body===undefined?undefined:JSON.stringify(body)});
  if(response.status===403)throw new Error('unauthorized');
  const result=await response.json().catch(()=>{throw new Error('unavailable');});
  if(!response.ok||result.ok===false)throw new Error(result.error||'unavailable');
  return result as T;
}

export function mountVoicePanel(root:HTMLElement):()=>void {
  let disposed=false, state:State|undefined, timer:number|undefined, signature='', choosing=false, choiceCancelled=false;
  let preview='', previewVersion=0, sample:File|Blob|undefined, sampleURL='', playingURL='';
  let recorder:MediaRecorder|undefined, stream:MediaStream|undefined, recordTimer:number|undefined;
  let recording=false;
  const aborter=new AbortController();
  root.innerHTML=`<h2 class="screen-title">Голос <span class="muted">XTTS</span></h2>
    <div class="xtts-body"><p class="muted">Какие реплики озвучить, Праксис решает сама. Здесь — её голос.</p>
      <div class="xtts-status" aria-live="polite">Проверяю голосовую службу…</div>
      <div class="actions"><button type="button" class="chip" data-power disabled>Включить озвучку</button><button type="button" class="chip" data-refresh>Обновить</button></div>
      <p class="xtts-note muted" role="status" aria-live="polite"></p>
      <label>Текст для пробы<textarea data-text rows="2" maxlength="400">Привет. Проверяем, как звучит этот голос.</textarea></label>
      <div class="xtts-player" hidden><div data-playing class="muted"></div><audio controls preload="none"></audio></div>
      <button type="button" class="chip" data-cancel hidden>Отменить пробу</button>
      <label class="xtts-search">Найти голос<input type="search" data-search placeholder="Название голоса" autocomplete="off"></label>
    </div><div data-voices class="xtts-voices"></div>
    <div class="xtts-body xtts-clone"><h3>Свой голос</h3><p class="muted">Загрузите или запишите чистую речь одного человека: 3–60 секунд, до 8 МБ.</p>
      <label>Название<input type="text" data-name maxlength="80" placeholder="Как назвать голос" autocomplete="off"></label>
      <label class="xtts-file">Образец<input type="file" data-file accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm,.flac"></label>
      <div class="actions"><button type="button" class="chip" data-record>Записать с микрофона</button><button type="button" class="chip" data-stop hidden>Остановить запись</button></div>
      <p data-sample class="muted">Образец не выбран.</p><audio data-source controls hidden></audio>
      <button type="button" class="chip" data-add>Добавить голос</button>
      <p class="muted">Добавление и прослушивание не меняют действующий голос. Для этого нажмите «Выбрать и включить».</p>
    </div>`;
  const find=<T extends HTMLElement>(selector:string)=>root.querySelector<T>(selector)!;
  const notice=(text:string,bad=false)=>{if(disposed)return;const node=find<HTMLElement>('.xtts-note');node.textContent=text;node.className='xtts-note '+(bad?'err':'muted');};
  const player=find<HTMLAudioElement>('.xtts-player audio');
  const sourcePlayer=find<HTMLAudioElement>('audio[data-source]');
  const updatePower=()=>{
    if(!state)return;
    const button=find<HTMLButtonElement>('[data-power]');
    button.textContent=choosing?'Отменить выбор':state.enabled?'Приостановить озвучку':'Включить озвучку';
    button.disabled=!state.available||(!state.enabled&&!choosing&&!state.selected_voice);
  };
  function renderVoices(force=false) {
    if(disposed||!state)return;
    const filter=find<HTMLInputElement>('[data-search]').value.trim().toLocaleLowerCase('ru');
    const next=JSON.stringify([state.voices,state.selected_voice,choosing,filter]);
    if(!force&&signature===next)return;
    signature=next;
    const voices=state.voices.filter(v=>v.name.toLocaleLowerCase('ru').includes(filter));
    find<HTMLElement>('[data-voices]').innerHTML=voices.length?voices.map(v=>`<div class="task-row xtts-voice${v.id===state?.selected_voice?' xtts-active':''}">
      <div class="task-head"><b>${esc(v.name)}</b> <span class="muted">${v.kind==='clone'?'По образцу':'Встроенный'}${v.id===state?.selected_voice?' · выбран':''}</span></div>
      <div class="actions"><button type="button" class="chip" data-preview="${esc(v.id)}">Послушать</button>
        <button type="button" class="chip" data-select="${esc(v.id)}"${choosing?' disabled':''}>Выбрать и включить</button>
        ${v.kind==='clone'?`<button type="button" class="chip" data-delete="${esc(v.id)}">Удалить</button>`:''}</div></div>`).join(''):
      `<p class="xtts-body muted">${!state.available?'Голосовая служба пока недоступна.':!state.model_ready?'Голоса появятся после прогрева модели.':'Голоса не найдены.'}</p>`;
  }
  async function refresh() {
    try {
      const result=await api<State>('/api/xtts/status',undefined,aborter.signal);
      if(disposed)return;state=result;
      const selected=state.voices.find(v=>v.id===state?.selected_voice);
      find<HTMLElement>('.xtts-status').textContent=!state.available?'Голосовая служба недоступна.':!state.model_ready?'Голосовая модель прогревается…':
        `${state.enabled?'Озвучка включена':'Озвучка приостановлена'} · ${selected?selected.name:'голос пока не выбран'}. ${state.busy?'Готовится звук.':'Модель готова.'}`;
      renderVoices();updatePower();
    } catch(error) {if(!disposed)notice(explain(error),true);}
  }
  async function waitJob(id:string,guard:()=>boolean=()=>!disposed):Promise<Job> {
    while(guard()) {
      const job=await api<Job>(`/api/xtts/jobs/${id}`,undefined,aborter.signal);
      if(!guard())throw new Error('cancelled');
      if(job.state==='ready')return job;
      if(job.state==='failed'||job.state==='cancelled')throw new Error(job.error||'cancelled');
      notice(job.phase==='busy'?'Голос занят другой репликой. Жду…':job.phase==='warming'?'Модель прогревается…':'Готовлю звук…');
      await new Promise(resolve=>setTimeout(resolve,650));
    }
    throw new Error('cancelled');
  }
  function cancelPreview() {
    previewVersion++;const old=preview;preview='';
    if(old)void api(`/api/xtts/jobs/${old}/cancel`,{}).catch(()=>undefined);
    player.pause();if(playingURL)URL.revokeObjectURL(playingURL);playingURL='';player.removeAttribute('src');
    find<HTMLElement>('.xtts-player').hidden=true;find<HTMLButtonElement>('[data-cancel]').hidden=true;
  }
  async function play(voice:Voice) {
    cancelPreview();const version=previewVersion;const id=uid();preview=id;
    find<HTMLButtonElement>('[data-cancel]').hidden=false;notice(`Готовлю пробу: ${voice.name}…`);
    try {
      await api('/api/xtts/jobs',{kind:'preview',request_id:id,voice_id:voice.id,profile_id:voice.profile_id,model_id:voice.model_id,
                                text:find<HTMLTextAreaElement>('[data-text]').value},aborter.signal);
      await waitJob(id,()=>!disposed&&version===previewVersion);
      const audio=await api<{data:string;mime:string}>(`/api/xtts/jobs/${id}/audio`,undefined,aborter.signal);
      if(disposed||version!==previewVersion)return;
      const binary=atob(audio.data), bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
      playingURL=URL.createObjectURL(new Blob([bytes],{type:audio.mime}));player.src=playingURL;
      find<HTMLElement>('[data-playing]').textContent=voice.name;find<HTMLElement>('.xtts-player').hidden=false;
      find<HTMLButtonElement>('[data-cancel]').hidden=true;
      notice('Проба готова.');
      void player.play().catch(()=>notice('Готово — нажмите ▶ для прослушивания.'));
    } catch(error) {if(!disposed&&version===previewVersion){notice(explain(error),true);find<HTMLButtonElement>('[data-cancel]').hidden=true;}}
  }
  async function select(voice?:Voice) {
    if(!state||choosing)return;
    cancelPreview();choosing=true;choiceCancelled=false;updatePower();renderVoices();
    try {
      const id=uid();const body=voice?{kind:'select',voice_id:voice.id,profile_id:voice.profile_id,model_id:voice.model_id}:
        {kind:'enable'};
      await api('/api/xtts/jobs',{...body,request_id:id,expected_revision:state.selection_revision},aborter.signal);
      await waitJob(id);notice('Голос выбран. Праксис сможет озвучивать им свои реплики.');
    } catch(error) {if(!disposed)notice(choiceCancelled?'Озвучка приостановлена.':explain(error),!choiceCancelled);}
    finally {choosing=false;await refresh();updatePower();renderVoices();}
  }
  async function power() {
    if(!state)return;
    if(state.enabled||choosing) {
      await api('/api/xtts/enabled',{enabled:false,request_id:uid()},aborter.signal);
      choiceCancelled=choosing;notice('Озвучка приостановлена. Предпрослушивание доступно.');await refresh();
    } else await select();
  }
  async function remove(voice:Voice) {
    const active=state?.selected_voice===voice.id;
    if(!confirm(`${active?'Приостановить озвучку и удалить':'Удалить'} голос «${voice.name}» вместе с образцом?`))return;
    cancelPreview();
    if(active&&state?.enabled)await api('/api/xtts/enabled',{enabled:false,request_id:uid()},aborter.signal);
    const result=await api<Job>(`/api/xtts/voices/${voice.id}/delete`,{request_id:uid()},aborter.signal);
    await waitJob(result.id);notice('Голос и образец удалены.');await refresh();
  }
  function setSample(value:File|Blob,label:string) {
    if(value.size>8*1024*1024){notice(errors.reference_too_large,true);return;}
    sample=value;if(sampleURL)URL.revokeObjectURL(sampleURL);
    sampleURL=URL.createObjectURL(value);sourcePlayer.src=sampleURL;sourcePlayer.hidden=false;
    find<HTMLElement>('[data-sample]').textContent=label;
  }
  function stopRecording() {
    recording=false;find<HTMLButtonElement>('[data-record]').disabled=false;
    if(recordTimer!==undefined)clearTimeout(recordTimer);recordTimer=undefined;
    if(recorder&&recorder.state!=='inactive')recorder.stop();
    stream?.getTracks().forEach(track=>track.stop());stream=undefined;
    find<HTMLButtonElement>('[data-record]').hidden=false;find<HTMLButtonElement>('[data-stop]').hidden=true;
  }
  async function record() {
    if(recording)return;
    if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')throw new Error('microphone');
    recording=true;find<HTMLButtonElement>('[data-record]').disabled=true;
    const acquired=await navigator.mediaDevices.getUserMedia({audio:true});
    if(disposed){acquired.getTracks().forEach(track=>track.stop());return;}
    stream=acquired;
    const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(type=>MediaRecorder.isTypeSupported(type));
    recorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
    const chunks:Blob[]=[];
    recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
    const activeRecorder=recorder;
    recorder.onstop=()=>{if(!disposed)setSample(new Blob(chunks,{type:activeRecorder.mimeType||'audio/webm'}),'Запись с микрофона.');};
    recorder.onerror=()=>{stopRecording();notice('Запись не удалась. Можно загрузить аудиофайл.',true);};
    recorder.start();find<HTMLButtonElement>('[data-record]').hidden=true;find<HTMLButtonElement>('[data-stop]').hidden=false;
    find<HTMLElement>('[data-sample]').textContent='Идёт запись. Остановите её через 3–60 секунд.';
    recordTimer=window.setTimeout(stopRecording,59000);
  }
  async function add() {
    if(!sample){notice('Сначала выберите или запишите образец.',true);return;}
    const name=find<HTMLInputElement>('[data-name]').value.trim();if(!name){notice(errors.invalid_voice_name,true);return;}
    const button=find<HTMLButtonElement>('[data-add]');button.disabled=true;
    try {
      const bytes=new Uint8Array(await sample.arrayBuffer());let binary='';
      for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
      const id=uid();await api('/api/xtts/jobs',{kind:'add',request_id:id,name,data:btoa(binary)},aborter.signal);
      await waitJob(id);notice('Свой голос добавлен. Теперь его можно послушать и выбрать.');await refresh();
    } catch(error) {if(!disposed)notice(explain(error),true);}
    finally {if(!disposed)button.disabled=false;}
  }
  const click=(event:Event)=>{
    const target=(event.target as HTMLElement).closest<HTMLButtonElement>('button');if(!target||!root.contains(target))return;
    const run=async()=>{
      if(target.hasAttribute('data-refresh'))return refresh();
      if(target.hasAttribute('data-power'))return power();
      if(target.hasAttribute('data-cancel')){cancelPreview();notice('Подготовка отменена.');return;}
      if(target.hasAttribute('data-record'))return record();
      if(target.hasAttribute('data-stop')){stopRecording();return;}
      if(target.hasAttribute('data-add'))return add();
      const key=target.dataset.preview||target.dataset.select||target.dataset.delete;
      const voice=state?.voices.find(v=>v.id===key);if(!voice)return;
      if(target.dataset.preview)return play(voice);
      if(target.dataset.select)return select(voice);
      if(target.dataset.delete)return remove(voice);
    };
    void run().catch(error=>{if(target.hasAttribute('data-record'))stopRecording();notice(error instanceof Error&&error.message==='microphone'?
      'Микрофон недоступен. Можно загрузить аудиофайл.':explain(error),true);});
  };
  root.addEventListener('click',click);
  find<HTMLInputElement>('[data-search]').addEventListener('input',()=>renderVoices());
  find<HTMLInputElement>('[data-file]').addEventListener('change',event=>{
    const file=(event.target as HTMLInputElement).files?.[0];if(file)setSample(file,file.name);
  });
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')find<HTMLButtonElement>('[data-record]').hidden=true;
  const poll=async()=>{if(disposed)return;await refresh();if(!disposed)timer=window.setTimeout(poll,5000);};
  void poll();
  return ()=>{disposed=true;aborter.abort();if(timer!==undefined)clearTimeout(timer);cancelPreview();stopRecording();
    sourcePlayer.pause();if(sampleURL)URL.revokeObjectURL(sampleURL);root.removeEventListener('click',click);};
}

/** Small compatibility hook for the already-serving phone UI. No base bundle edits. */
export function installLegacyVoiceGroup():()=>void {
  let node:HTMLElement|undefined,dispose:(()=>void)|undefined,pending=false;
  const reconcile=()=>{
    pending=false;
    const screen=document.querySelector<HTMLElement>('#screen');
    const active=!!document.querySelector('[data-tab="system"][aria-current="true"]');
    if(node&&(!node.isConnected||node.parentElement!==screen||!active)){dispose?.();node.remove();node=undefined;dispose=undefined;}
    if(active&&screen?.querySelector('.screen-title')&&!node){
      node=document.createElement('section');node.id='praxis-voice-group';node.setAttribute('aria-label','Голос');
      screen.append(node);dispose=mountVoicePanel(node);
    }
  };
  const observer=new MutationObserver(()=>{if(!pending){pending=true;queueMicrotask(reconcile);}});
  observer.observe(document.querySelector('#app')||document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['aria-current']});
  reconcile();
  return()=>{observer.disconnect();dispose?.();node?.remove();};
}

if(document.querySelector('script[data-praxis-voice="legacy"]'))installLegacyVoiceGroup();
