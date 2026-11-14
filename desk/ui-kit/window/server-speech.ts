import { t as uiText } from "../../lang/lang";
/** Remote speech belongs to the selected channel; no vendor key or local worker. */
import { cfg } from './api';
import { S } from './state';
import { card } from '../dom';
import { sha256 } from '../sha256';
import './styles/server-speech.css';

type Selection = {provider:'fish'|'xtts';enabled:boolean;revision:string;reference_id?:string;model?:string};
type Status = {ok:true;selection:Selection;fish:{configured:boolean;authenticated?:boolean;api_credit_raw?:string}};
type Voice = {id:string;name:string};
type Job = {id:string;state:string;error?:string};
type PreviewIntent = Readonly<{request_id:string;voice_id:string;model:string;text:string;delivery:string}>;
class SpeechError extends Error {
  code:string;status:number;
  constructor(code:string,status=0){super(code);this.code=code;this.status=status;}
}

export function serverSpeechCard(): HTMLElement {
  const channel={base:cfg.base,key:cfg.key,agent:cfg.agent};
  const scope=()=>JSON.stringify([cfg.base,cfg.key,cfg.agent]);
  const identity=scope();
  const storageKey='helene.speech-preview:'+sha256(new TextEncoder().encode(identity));
  const body=document.createElement('div');body.className='server-speech';
  const root=card(uiText("ui.ui.kit.window.server.speech.5c9ebd7b1c"),body,uiText("ui.ui.kit.window.server.speech.9dc46063b3"));
  root.dataset.serverSpeechCard='true';
  let disposed=false,mounted=false,epoch=0,controller=new AbortController(),status:Status|undefined;
  let voices:Voice[]=[],page=1,hasMore=false,readingCatalog=0,catalogBusy=false,modelInitialized=false,watching:{id:string;token:number}|undefined,selectionBusy=false,selectionFlight:object|undefined;
  let job:Job|undefined,intent:PreviewIntent|undefined,audioURL='';
  try{
    const saved=sessionStorage.getItem(storageKey);
    if(saved){
      const value=/^[a-f0-9]{32}$/.test(saved)?{id:saved}:JSON.parse(saved);
      if(typeof value.id==='string'&&/^[a-f0-9]{32}$/.test(value.id)){
        job={id:value.id,state:'unknown'};
        const body=value.intent;
        if(body&&body.request_id===value.id&&['voice_id','model','text','delivery'].every(key=>typeof body[key]==='string'))
          intent=Object.freeze({request_id:value.id,voice_id:body.voice_id,model:body.model,text:body.text,delivery:body.delivery});
      }
    }
  }catch{/* Storage is optional. */}
  const text=(tag:string,className='',value='')=>{const e=document.createElement(tag);e.className=className;e.textContent=value;return e;};
  const hint=text('p','field-hint',uiText("ui.ui.kit.window.server.speech.3c8039d918")),note=text('p','receipt');note.setAttribute('role','status');
  const controls=text('div','actions'),content=text('div','server-speech-content');content.hidden=true;
  const makeButton=(label:string,action:()=>Promise<unknown>|void)=>{
    const b=document.createElement('button');b.type='button';b.className='btn btn-quiet';b.textContent=label;
    b.addEventListener('click',()=>{if(!usable())return;const token=epoch;
      void Promise.resolve().then(()=>current(token)?action():undefined).catch(error=>{if(current(token))fail(error);});});return b;
  };
  const refresh=makeButton(uiText("ui.ui.kit.window.server.speech.603e460bf5"),async()=>{await readStatus();await catalog();});
  const power=makeButton(uiText("ui.ui.kit.window.server.speech.184ec7da48"),()=>select(status!.selection.provider,undefined,!status!.selection.enabled));
  const xtts=makeButton(uiText("ui.ui.kit.window.server.speech.339d6d36cb"),()=>select('xtts'));
  controls.append(refresh,power,xtts);power.disabled=xtts.disabled=true;
  const model=document.createElement('select');model.className='field-input';model.dataset.speechModel='true';
  model.append(new Option(uiText("ui.ui.kit.window.server.speech.0bdad2d72e"),'drama-3'),new Option('S2.1 Pro','s2.1-pro'));
  const words=document.createElement('textarea');words.className='field-input';words.rows=2;words.maxLength=400;words.dataset.speechText='true';
  words.value=uiText("ui.ui.kit.window.server.speech.fdc5609336");
  const direction=document.createElement('textarea');direction.className='field-input';direction.rows=3;direction.dataset.speechDelivery='true';
  direction.placeholder=uiText("ui.ui.kit.window.server.speech.8c279b6c82");
  for(const [title,input] of [[uiText("ui.ui.kit.window.server.speech.f3bdf7cce3"),model],[uiText("ui.ui.kit.window.server.speech.a6bc6a0e36"),words],[uiText("ui.ui.kit.window.server.speech.13db63af3c"),direction]] as const){
    const label=text('label','field-label',title);label.append(input);content.append(label);
  }
  content.append(text('p','field-hint',uiText("ui.ui.kit.window.server.speech.bf95babce1")));
  content.append(text('p','field-hint',uiText("ui.ui.kit.window.server.speech.91a536d073")));
  const player=document.createElement('audio');player.controls=true;player.preload='none';player.hidden=true;
  player.setAttribute('aria-label',uiText("ui.ui.kit.window.server.speech.2811c6d052"));
  const check=makeButton(uiText("ui.ui.kit.window.server.speech.4b4366416d"),()=>watch(job!.id));check.dataset.speechCheck='true';
  const replay=makeButton(uiText("ui.ui.kit.window.server.speech.4ca61164e3"),()=>{
    if(job?.state==='unknown'&&intent?.request_id===job.id)return sendIntent(intent);
  });replay.dataset.speechReplay='true';replay.title=uiText("ui.ui.kit.window.server.speech.83d60bf4cb");
  const cancel=makeButton(uiText("ui.ui.kit.window.server.speech.5c39340dec"),async()=>{
    if(!job)return;
    const id=job.id,token=epoch;
    await request(`/api/speech/fish/jobs/${id}/cancel`,{});
    if(!current(token)||job?.id!==id)return;
    job=undefined;intent=undefined;remember();watching=undefined;stopAudio();drawJob();
    note.textContent=uiText("ui.ui.kit.window.server.speech.014a36ad1b");
  });cancel.dataset.speechCancel='true';
  const jobActions=text('div','actions');jobActions.append(check,replay,cancel);
  const list=text('div','server-speech-voices'),pages=text('div','actions'),pageLabel=text('span','field-hint');
  const previous=makeButton(uiText("ui.ui.kit.window.server.speech.1a9fb1f3cf"),()=>catalog(Math.max(1,page-1)));
  const next=makeButton(uiText("ui.ui.kit.window.server.speech.ea58bb0bc6"),()=>catalog(page+1));pages.append(previous,pageLabel,next);
  content.append(player,jobActions,list,pages);body.append(hint,controls,note,content);

  function usable(){return !disposed&&identity===scope()&&root.isConnected&&S.view==='settings';}
  function requestId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');}
  function current(token:number){return usable()&&token===epoch;}
  function remember(){try{if(job)sessionStorage.setItem(storageKey,JSON.stringify({id:job.id,intent}));else sessionStorage.removeItem(storageKey);}catch{/* Optional session storage. */}}
  function stopAudio(){player.pause();player.removeAttribute('src');player.hidden=true;if(audioURL)URL.revokeObjectURL(audioURL);audioURL='';}
  function fail(error:unknown){
    if(!usable()||controller.signal.aborted)return;
    const code=error instanceof SpeechError?error.code:'';
    const messages:Record<string,string>={unauthorized:uiText("ui.ui.kit.window.server.speech.adc9b62672"),
      config_changed:uiText("ui.ui.kit.window.server.speech.de6558d1a4"),
      fish_key_unavailable:uiText("ui.ui.kit.window.server.speech.4929518709"),fish_no_api_credit:uiText("ui.ui.kit.window.server.speech.ed767becea"),
      fish_rate_limited:uiText("ui.ui.kit.window.server.speech.cb9ad5d3f4"),invalid_text:uiText("ui.ui.kit.window.server.speech.4ad9c6b041"),
      audio_invalid:uiText("ui.ui.kit.window.server.speech.1fbedeaf51")};
    note.textContent=messages[code]||uiText("ui.ui.kit.window.server.speech.afd6ef6428");
    note.className='receipt err';drawJob();
  }
  async function request<T>(path:string,payload?:unknown):Promise<T>{
    if(disposed||identity!==scope()||S.view!=='settings')throw new SpeechError('scope_changed');
    const token=epoch;
    const response=await fetch((channel.base||'')+path,{method:payload===undefined?'GET':'POST',
      credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:{...(channel.key?{Authorization:'Bearer '+channel.key}:{}),...(payload===undefined?{}:{'Content-Type':'application/json'})},
      body:payload===undefined?undefined:JSON.stringify(payload)});
    if(disposed||identity!==scope()||token!==epoch)throw new SpeechError('scope_changed');
    let result:Record<string,unknown>;
    try{result=await response.json();}catch{throw new SpeechError('invalid_response',response.status);}
    if(!current(token))throw new SpeechError('scope_changed');
    if(!response.ok||result.ok===false)throw new SpeechError(response.status===401||response.status===403?'unauthorized':String(result.error||'unavailable'),response.status);
    return result as T;
  }
  async function readStatus(){
    const token=epoch;
    try{
      const value=await request<Status>('/api/speech/status');
      if(!current(token))return;
      if(value.ok!==true||!value.selection||!['fish','xtts'].includes(value.selection.provider)
          ||typeof value.selection.enabled!=='boolean'||typeof value.selection.revision!=='string'||!value.fish
          ||typeof value.fish.configured!=='boolean')throw new SpeechError('invalid_response');
      status=value;root.hidden=false;content.hidden=false;note.className='receipt';
      const selected=value.selection;
      if(!modelInitialized){
        if(selected.provider==='fish'&&['drama-3','s2.1-pro'].includes(selected.model||''))model.value=selected.model!;
        modelInitialized=true;
      }
      const connection=value.fish.authenticated===true?uiText("ui.ui.kit.window.server.speech.e814c936e5"):value.fish.configured?uiText("ui.ui.kit.window.server.speech.aab1a40694"):uiText("ui.ui.kit.window.server.speech.2fbc3a5cdb");
      hint.textContent=uiText("ui.ui.kit.window.server.speech.5492f8afe6", {p0: String(selected.provider==='fish'?'Fish Audio'+(selected.model?' · '+selected.model:''):'XTTS'), p1: String(selected.enabled?uiText("ui.ui.kit.window.server.speech.05d8671ba9"):uiText("ui.ui.kit.window.server.speech.871a3ca607")), p2: String(connection), p3: String(value.fish.api_credit_raw!==undefined?uiText("ui.ui.kit.window.server.speech.02181f61a0")+value.fish.api_credit_raw:'')});
      power.textContent=selected.enabled?uiText("ui.ui.kit.window.server.speech.184ec7da48"):uiText("ui.ui.kit.window.server.speech.fdec013be3");power.disabled=selectionBusy;
      xtts.hidden=selected.provider==='xtts';xtts.disabled=selectionBusy;
      if(!value.fish.configured)list.replaceChildren(text('p','field-hint',uiText("ui.ui.kit.window.server.speech.b1c4f68caa")));
      drawVoices();drawJob();
    }catch(error){
      if(!current(token))return;
      if(error instanceof SpeechError&&(error.status===404||error.status===405)){root.hidden=true;return;}
      hint.textContent=uiText("ui.ui.kit.window.server.speech.24d6b2be73");fail(error);
    }
  }
  async function catalog(requestedPage=page){
    if(!status?.fish.configured)return;
    const sequence=++readingCatalog,token=epoch;
    catalogBusy=true;previous.disabled=next.disabled=true;
    try{
      const value=await request<{voices:Voice[];page:number;has_more:boolean}>(`/api/speech/fish/catalog/${requestedPage}`);
      if(!current(token)||sequence!==readingCatalog)return;
      if(!Array.isArray(value.voices)||value.page!==requestedPage||typeof value.has_more!=='boolean'
        ||value.voices.some(v=>typeof v.id!=='string'||typeof v.name!=='string'))throw new SpeechError('invalid_response');
      voices=value.voices;page=requestedPage;hasMore=value.has_more;
    }finally{if(current(token)&&sequence===readingCatalog){catalogBusy=false;drawVoices();}}
  }
  function pending(){return !!job&&!['ready','failed','cancelled'].includes(job.state);}
  function drawJob(){check.hidden=!job;replay.hidden=job?.state!=='unknown'||intent?.request_id!==job?.id;cancel.hidden=!pending();for(const b of list.querySelectorAll<HTMLButtonElement>('[data-speech-preview]'))b.disabled=pending();}
  function drawVoices(){
    if(!status?.fish.configured)return;
    list.replaceChildren();
    for(const voice of voices){
      const row=text('div','server-speech-voice'),name=text('strong','',voice.name),actions=text('div','actions');
      if(status.selection.provider==='fish'&&status.selection.reference_id===voice.id)name.append(text('span','field-hint',uiText("ui.ui.kit.window.server.speech.99b3db37a6")));
      const sample=makeButton(uiText("ui.ui.kit.window.server.speech.c9f921d211"),()=>preview(voice));sample.dataset.speechPreview=voice.id;sample.disabled=pending();
      const choose=makeButton(uiText("ui.ui.kit.window.server.speech.446c810115"),()=>select('fish',voice));choose.dataset.speechSelect=voice.id;choose.disabled=selectionBusy;
      actions.append(sample,choose);row.append(name,actions);list.append(row);
    }
    if(!voices.length)list.append(text('p','field-hint',uiText("ui.ui.kit.window.server.speech.8f7e5d67dd")));
    pageLabel.textContent=uiText("ui.ui.kit.window.server.speech.e2eb3825a4", {p0: String(page)});previous.disabled=catalogBusy||page===1;next.disabled=catalogBusy||!hasMore;
  }
  async function select(provider:'fish'|'xtts',voice?:Voice,enabled=true){
    if(!status||selectionBusy)return;const flight={},token=epoch;selectionFlight=flight;
    selectionBusy=true;drawVoices();power.disabled=xtts.disabled=true;
    try{
      await request('/api/speech/selection',{request_id:requestId(),
        expected_revision:status.selection.revision,provider,enabled,...(voice?{voice_id:voice.id,model:model.value}:{})});
      if(current(token))note.textContent=uiText("ui.ui.kit.window.server.speech.b740c50d66");
    }catch(error){if(current(token))fail(error);}finally{
      if(selectionFlight===flight){selectionFlight=undefined;selectionBusy=false;if(current(token))await readStatus();}
    }
  }
  async function preview(voice:Voice){
    if(pending())return;
    stopAudio();const id=requestId();
    intent=Object.freeze({request_id:id,voice_id:voice.id,model:model.value,text:words.value,delivery:direction.value});
    job={id,state:'queued'};remember();drawJob();
    note.className='receipt';note.textContent=uiText("ui.ui.kit.window.server.speech.522979f036", {p0: String(voice.name)});
    await sendIntent(intent);
  }
  async function sendIntent(body:PreviewIntent){
    const token=epoch,id=body.request_id;
    if(!usable()||job?.id!==id)return;
    job.state='queued';drawJob();
    try{
      const value=await request<Job>('/api/speech/fish/jobs',body);
      if(!current(token)||job?.id!==id)return;
      if(value.id!==id)throw new SpeechError('invalid_response');job=value;
      await watch(id);
    }catch(error){if(current(token)){if(job?.id===id)job.state='unknown';fail(error);}}
  }
  async function watch(id:string){
    const token=epoch;if(watching?.id===id&&watching.token===token)return;
    const flight={id,token};watching=flight;
    try{
      while(current(token)&&job?.id===id){
        const value=await request<Job>(`/api/speech/fish/jobs/${id}`);
        if(!current(token)||job?.id!==id)return;
        if(value.id!==id||typeof value.state!=='string')throw new SpeechError('invalid_response');
        job=value;drawJob();
        if(value.state==='ready'){await loadAudio(id,token);return;}
        if(['unknown','cancel_requested'].includes(value.state)){note.textContent=uiText("ui.ui.kit.window.server.speech.29e5cde9fc");return;}
        if(['failed','cancelled'].includes(value.state)){note.textContent=value.state==='cancelled'?uiText("ui.ui.kit.window.server.speech.537fc7870e"):uiText("ui.ui.kit.window.server.speech.c43d34f54e");return;}
        await new Promise(resolve=>setTimeout(resolve,650));
      }
    }catch(error){if(current(token)&&job?.id===id){job.state='unknown';fail(error);}}
    finally{if(watching===flight)watching=undefined;if(current(token))drawJob();}
  }
  async function loadAudio(id:string,token:number){
    const value=await request<{mime:string;data:string;bytes:number;sha256:string}>(`/api/speech/fish/jobs/${id}/audio`);
    if(!current(token)||job?.id!==id)return;
    if(value.mime!=='audio/ogg'||typeof value.data!=='string'||!Number.isSafeInteger(value.bytes)||value.bytes<1
      ||value.bytes>20*1024*1024||!/^[a-f0-9]{64}$/.test(value.sha256))throw new SpeechError('audio_invalid');
    let bytes:Uint8Array<ArrayBuffer>;
    try{bytes=Uint8Array.from(atob(value.data),c=>c.charCodeAt(0));}catch{throw new SpeechError('audio_invalid');}
    const hash=sha256(bytes);
    if(bytes.length!==value.bytes||hash!==value.sha256)throw new SpeechError('audio_invalid');
    if(!current(token)||job?.id!==id)return;
    stopAudio();audioURL=URL.createObjectURL(new Blob([bytes],{type:value.mime}));player.src=audioURL;player.hidden=false;
    note.textContent=uiText("ui.ui.kit.window.server.speech.f830ffc5d6");
    void player.play().catch(()=>{if(current(token)&&job?.id===id)note.textContent=uiText("ui.ui.kit.window.server.speech.4f97e65b65");});
  }
  function suspend(){epoch++;controller.abort();controller=new AbortController();watching=undefined;activation=undefined;selectionBusy=false;selectionFlight=undefined;stopAudio();}
  function dispose(){if(disposed)return;disposed=true;suspend();observer.disconnect();removeEventListener('frame-section',section);}
  let activation:object|undefined;
  async function activate(){
    if(!usable()||activation)return;const flight={},token=epoch;activation=flight;
    try{
      await readStatus();if(!current(token))return;
      if(status&&!root.hidden){await catalog();if(current(token)&&job)await watch(job.id);}
    }catch(error){if(current(token))fail(error);}finally{if(activation===flight)activation=undefined;}
  }
  const section=(event:Event)=>{
    if(identity!==scope()){dispose();return;}
    if((event as CustomEvent).detail!=='settings'){suspend();return;}
    if(root.isConnected)void activate();
    else if(mounted)dispose();
  };
  const observer=new MutationObserver(()=>{if(identity!==scope()){dispose();return;}if(root.isConnected){if(!mounted){mounted=true;void activate();}}else if(mounted)dispose();});
  observer.observe(document.body,{childList:true,subtree:true});addEventListener('frame-section',section);
  queueMicrotask(()=>{if(root.isConnected){mounted=true;void activate();}});
  return root;
}
