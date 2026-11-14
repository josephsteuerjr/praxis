import { t as uiText, locale, UiError } from "../../lang/lang";
// Карточка «Голос»: слышит ли агент голосовые на этом ПК.
//
// Что здесь особенного и почему это отдельный файл. Голос — единственная часть
// продукта, которой НЕ ХВАТАЕТ ГИГАБАЙТА, чтобы работать: библиотека едет в
// рантайме, а модель — нет (самая маленькая 480 МБ, рабочая 1,6 ГБ). Значит
// карточка обязана уметь то, чего не умеет ни одна другая: показать, чего не
// хватает, скачать это и рассказывать о ходе дела, не притворяясь готовой.
//
// Состояние спрашивается у КАНАЛА (`/api/voice`, тот же `localharness/voice.py`,
// которым голос поднимает раннер), а не считается здесь: вторая правда о том,
// скачана ли модель, разъехалась бы с первой молча.
import { voiceOwnedText } from "../../ui-kit/window/voice-owned";
import { voicePreferencesFields } from "../../ui-kit/window/voice-preferences";
import { api, shell, channelURL } from "../../ui-kit/window/api";
import { el, humanError } from "../../ui-kit/window/lib";
import { button, card, choice, toggle } from "../../ui-kit/dom";
import type { Config } from "../../ui-kit/window/views/settings-frame";

interface VoiceModel {
  id: string;
  title: string;
  note: string;
  size_mb: number;
  installed: boolean;
}

interface VoiceState {
  enabled: boolean;
  model: string;
  ready: boolean;
  why: string;
  dir: string;
  library: EngineState;
  installed: { model?: string; path?: string; bytes?: number };
  catalog: VoiceModel[];
  speech?: SpeechState;
  download: {
    state: string;
    model: string;
    note: string;
    got_bytes: number;
    total_bytes: number;
    updated_utc: string;
    active?: boolean | null;
  } | null;
}

/** Чем слушать и говорить. С 1.2.1 на Windows движок голоса не едет в установщике: его
 *  можно докачать (`downloadable`) — одной кнопкой вместе с моделью или голосом. */
interface EngineState {
  present: boolean;
  why: string;
  downloadable?: boolean;
  size_mb?: number;
  cuda_supported?: boolean | null;
}

/** Голос агента наружу — вторая половина того же ответа канала. */
interface SpeechState {
  fish?: {configured: boolean; models: string[]; last_request?: {request_id: string; status: string; cancelled?: boolean}|null};
  speaker?: string;
  speakers?: string[];
  languages?: string[];
  worker_ready?: boolean | null;
  worker_phase?: string;
  worker_error?: string;
  device_actual?: string;
  enabled: boolean;
  voice: string;
  ready: boolean;
  why: string;
  dir: string;
  model: string;
  library: EngineState;
  catalog: Array<{ id: string; title: string; note: string; size_mb: number; installed: boolean }>;
  download: { state: string; voice: string; note?: string; got_bytes?: number; total_bytes?: number; active?: boolean | null; done_mb?: number; size_mb?: number; at?: string; error?: string } | null;
}

export interface VoiceCard {
  el: HTMLElement;
  enabled(): boolean;
  model(): string;
  keepLoaded(): boolean;
  speak(): boolean;
  speakVoice(): string;
}

const MB = 1024 * 1024;

function voiceField(title: string): HTMLLabelElement {
  const label = el('label', 'field');
  label.append(el('span', 'field-label', title));
  return label;
}

export function speechStatusText(state: SpeechState): string {
  if (!state.ready) return uiText("ui.app.src.voicecard.5de99125d3", {p0: voiceOwnedText(state.why)});
  const device = state.device_actual ? ` · ${state.device_actual.toUpperCase()}` : '';
  switch (state.worker_phase) {
    case 'loading': return uiText("ui.app.src.voicecard.0eddc4ea8f");
    case 'warming_voice': return uiText("ui.app.src.voicecard.66fcf1bfee");
    case 'busy': return uiText("ui.app.src.voicecard.23576bb730", {p0: String(device)});
    case 'paused': return uiText("ui.app.src.voicecard.4e17d5b586");
    case 'error': return uiText("ui.app.src.voicecard.b82ce2bd3f", {p0: String(state.worker_error || uiText("ui.app.src.voicecard.6249494479"))});
    case 'unknown': return uiText("ui.app.src.voicecard.5870d1370e");
  }
  return state.worker_ready === true
    ? uiText("ui.app.src.voicecard.250f0ee950", {p0: String(device)})
    : uiText("ui.app.src.voicecard.b1f5cbd9d7");
}

function mb(bytes: number | undefined): string {
  return bytes ? uiText("ui.app.src.voicecard.fb360df45f", {p0: new Intl.NumberFormat(locale()).format(Math.round(bytes / MB))}) : uiText("ui.app.src.voicecard.6eeb81327f");
}

/** Карточка голоса. Черновик правится на месте, как у соседей. */
export function voiceCard(draft: Config): VoiceCard {
  const voice = (draft.voice = draft.voice || {});
  // Умолчание — ВЫКЛЮЧЕНО: без скачанной модели включённый голос был бы
  // обещанием, которое нечем исполнить.
  let enabled = !!voice.enabled;
  let model = String(voice.model || "turbo");
  let keep = !!voice.keep_loaded;
  // Речь — отдельный выключатель, а не часть слуха: слышать голосовые и
  // отвечать голосом — разные желания, и одно без другого законно.
  let speak = !!voice.speak;
  let speakVoice = String(voice.voice || 'xtts');
  voice.voice = speakVoice;

  const box = el("div", "voice-settings");
  const status = el("p", "field-hint", uiText("ui.app.src.voicecard.61bd6769dc"));
  const pickBox = el("div");
  const actions = el("div", "actions");
  const note = el("span", "receipt");
  const fetchBtn = button(uiText("ui.app.src.voicecard.3fcb297329"), "quiet", () => void start());
  actions.append(fetchBtn, note);

  const onOff = toggle(uiText("ui.app.src.voicecard.f837894d3e"), enabled, (v) => {
    enabled = v;
    voice.enabled = v;
    void refresh();
  });
  const keepOff = toggle(uiText("ui.app.src.voicecard.f036a094dd"), keep, (v) => {
    keep = v;
    voice.keep_loaded = v;
  });

  let picker = choice<string>([], model, () => {});
  let timer: ReturnType<typeof setTimeout> | null = null;
  let refreshing = false;
  let refreshAgain = false;

  const drawPicker = (state: VoiceState) => {
    const items = state.catalog.map((m) => ({
      value: m.id,
      title: voiceOwnedText(m.title) + (m.installed ? uiText("ui.app.src.voicecard.280e5de27a") : uiText("ui.app.src.voicecard.2e27412068", {p0: new Intl.NumberFormat(locale()).format(m.size_mb)})),
      text: voiceOwnedText(m.note),
    }));
    picker = choice<string>(items, model, (v) => {
      model = v;
      voice.model = v;
      void refresh();
    }, { stack: true });
    pickBox.replaceChildren(picker.el);
  };

  const say = (state: VoiceState) => {
    const down = state.download;
    const busy = !!down && down.state === "running" && (down.active ?? fresh(down.updated_utc));
    fetchBtn.disabled = busy;
    if (busy) {
      const total = down!.total_bytes || 1;
      note.className = "receipt";
      note.textContent = uiText("ui.app.src.voicecard.440f9359db", {p0: String(down!.note || uiText("ui.app.src.voicecard.f2a84fd63b")), p1: String(down!.model), p2: String(mb(down!.got_bytes)), p3: String(mb(total))});
    } else if (down && down.state === "failed") {
      note.className = "receipt err";
      note.textContent = voiceOwnedText(down.note);
    } else if (down && down.state === "running") {
      // Помощник качал и пропал: окно закрыли, машину усыпили, процесс убит.
      // Вечное «качаю…» здесь было бы враньём, поэтому запись читается со
      // сроком годности.
      note.className = "receipt err";
      note.textContent = uiText("ui.app.src.voicecard.df695371d6");
    } else if (down && down.state === "done" && state.installed.path) {
      note.className = "receipt ok";
      note.textContent = uiText("ui.app.src.voicecard.ceffc39921", {p0: String(mb(state.installed.bytes))});
    }
    if (!state.library.present) {
      if (state.library.downloadable) {
        // 1.2.1: движок не в установщике — он приедет той же кнопкой, первым.
        status.textContent =
          uiText("voice.recognition.runtime.missing", {size: state.library.size_mb === undefined ? "?" : new Intl.NumberFormat(locale()).format(state.library.size_mb)});
        return;
      }
      status.textContent = voiceOwnedText(state.library.why) + uiText("ui.app.src.voicecard.716d9344e9");
      fetchBtn.disabled = true;
      return;
    }
    const where = uiText("ui.app.src.voicecard.6242d7fc2f", {p0: String(state.dir)});
    if (state.ready) {
      status.textContent =
        uiText("ui.app.src.voicecard.94c1173bac", {p0: String(state.installed.model || "?"), p1: String(mb(state.installed.bytes))}) +
        (state.why ? voiceOwnedText(state.why) + ". " : "") +
        uiText("voice.recognition.cpu", {where});
    } else {
      status.textContent = uiText("ui.app.src.voicecard.c5fb148996", {p0: voiceOwnedText(state.why), p1: String(where)});
    }
  };

  const fresh = (stamp: string): boolean => {
    const at = Date.parse(stamp || "");
    const age = Date.now() - at;
    return Number.isFinite(at) && age >= 0 && age < 60_000;
  };

  const refresh = async () => {
    if (timer) { clearTimeout(timer); timer = null; }
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      const state = await api<VoiceState>("/api/voice");
      if (!box.isConnected) return;
      // Выбор владельца в черновике главнее того, что лежит в файле: он мог
      // переключить модель и ещё не нажать «Сохранить».
      const shown = { ...state, model, enabled };
      if (!picker.el.childElementCount) drawPicker(shown);
      say(shown);
      // Речь приезжает тем же ответом канала — второй запрос разъезжался бы с
      // первым ровно тогда, когда владелец нажимает обе кнопки подряд.
      const speech = state.speech;
      if (speech) {
        if (speech.worker_phase === 'paused') cancelPreview();
        const mine = { ...speech, voice: speakVoice, enabled: speak };
        if (!speechPicker.el.childElementCount) drawVoices(mine);
        sayVoice(mine);
        const names = speech.speakers || [];
        cudaOption.disabled=speech.library.cuda_supported===false;
        deviceNote.textContent=speech.library.cuda_supported===false?uiText("ui.app.src.voicecard.f925355e29"):uiText("ui.app.src.voicecard.5b89d5d3be");
        if (names.join('\n') !== speakerSelect.dataset.catalog) {
          speakerSelect.replaceChildren(...names.map(name => new Option(name, name)));
          const selected = String(voice.tts_speaker || speech.speaker || 'Ana Florence');
          if (!names.includes(selected)) speakerSelect.append(new Option(selected+uiText("ui.app.src.voicecard.cb0edfc0ab"), selected));
          speakerSelect.value = selected;
          speakerSelect.dataset.catalog = names.join('\n');
          speakerSelect.disabled = !names.length;
          speakerCount.textContent = names.length ? uiText("ui.app.src.voicecard.d1e88822b5", {p0: String(names.length)}) : uiText("ui.app.src.voicecard.a06d1334b5");
        }
        fishKeyNote.textContent = speech.fish?.configured ? uiText("ui.app.src.voicecard.857e85fc9f") : uiText("ui.app.src.voicecard.fa2e989285");
        if(!previewing&&speakVoice==='fish'&&speech.fish?.last_request?.status==='unknown'){
          const last=speech.fish.last_request;
          previewNote.textContent='Fish · '+last.request_id+uiText("ui.app.src.voicecard.0f53700da8")+(last.cancelled?uiText("ui.app.src.voicecard.531c2e12dd"):'')+uiText("ui.app.src.voicecard.ba8538676e");
        }
        previewBtn.disabled = previewing || !speak || speech.worker_phase === 'paused' || (speakVoice === 'xtts' && speech.worker_ready !== true)
          || (speakVoice === 'fish' && (!speech.fish?.configured || !voice.fish_model || !voice.fish_voice));
        if (!previewing && speakVoice === 'xtts') previewNote.textContent = speech.worker_ready === true ? '' : speechStatusText(mine);
      } else {
        speechStatus.textContent = uiText("ui.app.src.voicecard.cf8c7ba38b");
        speechBtn.disabled = true;
      }
      const down = state.download;
      const dv = speech?.download;
      if ((down && down.state === "running" && (down.active ?? fresh(down.updated_utc)))
          || (dv && dv.state === "running" && (dv.active ?? fresh(dv.at || "")))
          || (speak && speech?.library.present && speech.ready)) {
        timer = setTimeout(() => void refresh(), 2000);
      }
    } catch (e) {
      if (box.isConnected) status.textContent = uiText("ui.app.src.voicecard.3142a17025") + humanError(e).text;
    } finally {
      refreshing = false;
      const again = refreshAgain;
      refreshAgain = false;
      if (again && box.isConnected) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => void refresh(), 0);
      }
    }
  };

  const start = async () => {
    note.className = "receipt";
    note.textContent = uiText("ui.app.src.voicecard.7c9cef0739");
    fetchBtn.disabled = true;
    try {
      const said = await shell<string>("voice_fetch", { model });
      note.textContent = said;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), 1500);
    } catch (e) {
      note.className = "receipt err";
      note.textContent = humanError(e).text;
      fetchBtn.disabled = false;
    }
  };

  // ---- вторая половина: агент говорит сам -----------------------------------
  const speechStatus = el("p", "field-hint", uiText("ui.app.src.voicecard.61bd6769dc"));
  const speechPickBox = el("div");
  const speechActions = el("div", "actions");
  const speechNote = el("span", "receipt");
  const speechBtn = button(uiText("ui.app.src.voicecard.af191218bf"), "quiet", () => void startVoice());
  speechActions.append(speechBtn, speechNote);
  const speechFields = el("div", "form-grid two voice-provider-fields");
  const fishFields = el('div', 'form-grid two voice-provider-fields');
  const fishKeyNote = el('small', 'field-hint');
  const fishKey = el('input', 'field-input') as HTMLInputElement;
  fishKey.type='password';fishKey.autocomplete='new-password';fishKey.maxLength=4096;
  fishKey.setAttribute('aria-label',uiText("ui.app.src.voicecard.0bee7e0dd7"));
  const keyLabel=voiceField(uiText("ui.app.src.voicecard.76e747398c"));keyLabel.classList.add('voice-full-row');keyLabel.append(fishKey,fishKeyNote);
  const keyActions=el('div','actions voice-full-row');
  const saveKey=button(uiText("ui.app.src.voicecard.7beb1b70ac"),'quiet',async()=>{
    const key=fishKey.value;fishKey.value='';saveKey.disabled=true;
    try{const response=await fetch(channelURL('/api/voice/fish-key'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
      if(!response.ok)throw new UiError(uiText("ui.app.src.voicecard.a10dc3d24b"));
      fishKeyNote.textContent=uiText("ui.app.src.voicecard.857e85fc9f");void refresh();
    }catch(e){fishKeyNote.textContent=humanError(e).text}finally{saveKey.disabled=false}
  });
  const deleteKey=button(uiText("ui.app.src.voicecard.43c89af66b"),'quiet',async()=>{
    cancelPreview();fishKey.value='';
    const response=await fetch(channelURL('/api/voice/fish-key'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delete:true})});
    fishKeyNote.textContent=response.ok?uiText("ui.app.src.voicecard.6d50dcd8f6"):uiText("ui.app.src.voicecard.777e1cc6b9");void refresh();
  });
  keyActions.append(saveKey,deleteKey);
  const fishModelLabel=voiceField(uiText("ui.app.src.voicecard.f38c958f98"));
  const fishModel=document.createElement('select');fishModel.className='field-input';
  fishModel.append(new Option(uiText("ui.app.src.voicecard.b1de32fa7b"),''),...['s2.1-pro','s2.1-pro-free','s2-pro','drama-3','s1'].map(id=>new Option(id,id)));
  fishModel.value=String(voice.fish_model||'');fishModel.onchange=()=>{cancelPreview();voice.fish_model=fishModel.value;void refresh()};fishModelLabel.append(fishModel);
  const fishVoiceLabel=voiceField(uiText("ui.app.src.voicecard.10ec11c42f"));
  const fishVoice=el('input','field-input') as HTMLInputElement;
  fishVoice.value=String(voice.fish_voice||'');fishVoice.maxLength=128;
  fishVoice.oninput=()=>{cancelPreview();voice.fish_voice=fishVoice.value;void refresh()};fishVoiceLabel.append(fishVoice);
  const fishDeliveryLabel=voiceField(uiText("ui.app.src.voicecard.291f940f96"));fishDeliveryLabel.classList.add('voice-full-row');
  const fishDelivery=el('textarea','field-input') as HTMLTextAreaElement;
  fishDelivery.value=String(voice.fish_delivery||'');fishDelivery.maxLength=1000;
  fishDelivery.placeholder=uiText("ui.app.src.voicecard.0bf9086213");
  fishDelivery.oninput=()=>{cancelPreview();voice.fish_delivery=fishDelivery.value};fishDeliveryLabel.append(fishDelivery);
  fishFields.append(keyLabel,keyActions,fishModelLabel,fishVoiceLabel,fishDeliveryLabel,
    el('p','field-hint voice-full-row',uiText("ui.app.src.voicecard.69f30070b9")));
  const deviceLabel=voiceField(uiText("ui.app.src.voicecard.d0c6d84ba6"));
  const deviceSelect=document.createElement('select');deviceSelect.className='field-input';
  const cudaOption=new Option(uiText("ui.app.src.voicecard.c375551c50"),'cuda');
  deviceSelect.append(new Option(uiText("ui.app.src.voicecard.3bac43abb9"),'cpu'),cudaOption);
  deviceSelect.value=String(voice.tts_device||'cpu');deviceSelect.onchange=()=>{cancelPreview();voice.tts_device=deviceSelect.value};
  const deviceNote=el('small','field-hint',uiText("ui.app.src.voicecard.de7e8c2d99"));
  deviceLabel.append(deviceSelect,deviceNote);speechFields.append(deviceLabel);
  const speakerLabel = voiceField(uiText("ui.app.src.voicecard.4e5ea2e875"));
  const speakerSelect = document.createElement('select');speakerSelect.className='field-input';
  speakerSelect.onchange=()=>{cancelPreview();voice.tts_speaker=speakerSelect.value};
  const speakerCount=el('small','field-hint',uiText("ui.app.src.voicecard.ffa3ca7ed4"));
  speakerLabel.append(speakerSelect,speakerCount);speechFields.append(speakerLabel);
  const sample=el('input','field-input') as HTMLInputElement;
  sample.value=uiText("ui.app.src.voicecard.f297e08df6");sample.maxLength=300;
  sample.setAttribute('aria-label',uiText("ui.app.src.voicecard.8632e4e78a"));
  const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.hidden=true;
  audio.setAttribute('aria-label',uiText("ui.app.src.voicecard.a79bac2572"));
  let previewing = false;
  let previewEpoch=0;
  let activePreview: {id:string; cancelURL:string; controller:AbortController}|null=null;
  const cancelPreview=()=>{
    previewEpoch++;audio.pause();audio.removeAttribute('src');audio.hidden=true;
    const current=activePreview;activePreview=null;
    if(current){current.controller.abort();void fetch(current.cancelURL,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({request_id:current.id,cancel:true}),keepalive:true}).catch(()=>{});}
    previewing=false;
  };
  sample.oninput=()=>cancelPreview();
  const previewNote=el('span','receipt');
  const previewBtn=button(uiText("ui.app.src.voicecard.adc537d82c"),'quiet',async()=>{
    if (previewing) return;
    cancelPreview();const epoch=previewEpoch;const controller=new AbortController();
    const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
    const url=channelURL('/api/voice/preview');
    activePreview={id,cancelURL:channelURL('/api/voice/intent'),controller};
    previewing=true;previewBtn.disabled=true;previewNote.textContent=speakVoice==='fish'?uiText("ui.app.src.voicecard.5cd6d88974")+id:uiText("ui.app.src.voicecard.58d6b0676e");
    try {
      // Synthesis can exceed the event tunnel's 25s. Use the same authenticated
      // HTTP channel, not a second service or the model/Telegram transport.
      const response=await fetch(url,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},
        body:JSON.stringify({request_id:id,voice:speakVoice,speaker:speakerSelect.value,language:voice.tts_language||'ru',
          reference:voice.tts_reference||'',fish_model:voice.fish_model,fish_voice:voice.fish_voice,fish_delivery:voice.fish_delivery,text:sample.value})});
      if(!response.ok)throw new Error(await response.text());
      const result=await response.json();
      if(result.mime!=='audio/wav'||typeof result.base64!=='string')throw new UiError(uiText("ui.app.src.voicecard.c63362f114"));
      if(epoch!==previewEpoch||!box.isConnected||url!==channelURL('/api/voice/preview'))return;
      const live=await api<VoiceState>('/api/voice');
      if(epoch!==previewEpoch||!box.isConnected||url!==channelURL('/api/voice/preview')
        ||live.speech?.worker_phase==='paused'||live.speech?.enabled!==true)return;
      audio.src='data:audio/wav;base64,'+result.base64;audio.hidden=false;
      await audio.play();previewNote.textContent=uiText("ui.app.src.voicecard.49b2ffebcc");
    }catch(e){if(epoch===previewEpoch){
      let outcome='unknown';
      if(speakVoice==='fish')try{const response=await fetch(channelURL('/api/voice/intent?request_id='+id));
        if(response.ok){const receipt=await response.json();outcome=receipt.status||'unknown';}}catch{}
      if(epoch===previewEpoch)previewNote.textContent=speakVoice==='fish'
        ? uiText("ui.app.src.voicecard.2ae59c2c0d")+id+' · '+outcome+uiText("ui.app.src.voicecard.c83a0b2921")+humanError(e).text:humanError(e).text;
    }
    }finally{if(epoch===previewEpoch){previewing=false;activePreview=null;void refresh()}}
  });
  previewBtn.disabled=true;
  speechActions.append(previewBtn,previewNote);
  const stopPreview=button(uiText("ui.app.src.voicecard.069639047d"),'quiet',()=>{cancelPreview();previewNote.textContent=uiText("ui.app.src.voicecard.fc5ea4087d");void refresh()});
  speechActions.append(stopPreview);
  for (const [key, title, fallback] of [
    ["tts_language", uiText("ui.app.src.voicecard.bce937aa57"), "ru"],
    ["tts_reference", uiText("ui.app.src.voicecard.34ba8077f3"), ""],
  ]) {
    const label = voiceField(title);
    const input = el("input", "field-input") as HTMLInputElement;
    input.value = String(voice[key] || fallback);
    input.oninput = () => { cancelPreview();voice[key] = input.value; };
    label.append(input); speechFields.append(label);
  }
  const speakOff = toggle(uiText("ui.app.src.voicecard.1551881f7b"), speak, (v) => {
    speak = v;
    cancelPreview();
    voice.speak = v;
    void refresh();
  });
  let speechPicker = choice<string>([], speakVoice, () => {});

  const drawVoices = (state: SpeechState) => {
    const items = state.catalog.map((one) => ({
      value: one.id,
      title: voiceOwnedText(one.title),
      text: voiceOwnedText(one.note),
    }));
    speechPicker = choice<string>(items, speakVoice, (v) => {
      speakVoice = v;
      cancelPreview();
      voice.voice = v;
      showProvider();
      void refresh();
    });
    speechPicker.el.classList.add('voice-providers');
    speechPickBox.replaceChildren(speechPicker.el);
  };

  const sayVoice = (state: SpeechState) => {
    if(speakVoice==='fish'){
      speechStatus.textContent=!speak?uiText("ui.app.src.voicecard.6f8f7c0f25"):state.fish?.configured
        ? uiText("ui.app.src.voicecard.49a28346a2")
        : uiText("ui.app.src.voicecard.6a43684333");
      return;
    }
    const down = state.download;
    const busy = !!down && down.state === "running" && (down.active ?? fresh(down.at || ""));
    speechBtn.disabled = busy;
    if (busy) {
      speechNote.className = "receipt";
      speechNote.textContent = (down!.note || uiText("ui.app.src.voicecard.c2f07fa924")) + (down!.total_bytes ? uiText("ui.app.src.voicecard.abf20d83fe", {p0: String(mb(down!.got_bytes)), p1: String(mb(down!.total_bytes))}) : '');
    } else if (down && down.state === "failed") {
      speechNote.className = "receipt err";
      speechNote.textContent = String(down.error || uiText("ui.app.src.voicecard.471c60e516"));
    } else if (down && down.state === "running") {
      speechNote.className = "receipt err";
      speechNote.textContent = uiText("ui.app.src.voicecard.df695371d6");
    } else if (down && down.state === "done") {
      speechNote.className = "receipt ok";
      speechNote.textContent = uiText("ui.app.src.voicecard.0656632773");
    }
    if (!state.library.present) {
      if (state.library.downloadable) {
        speechStatus.textContent =
          uiText("ui.app.src.voicecard.8515c659d3");
        return;
      }
      speechStatus.textContent = voiceOwnedText(state.library.why) + uiText("ui.app.src.voicecard.39a3b81ae2");
      speechBtn.disabled = true;
      return;
    }
    speechStatus.textContent = speechStatusText(state);
  };

  const startVoice = async () => {
    speechNote.className = "receipt";
    speechNote.textContent = uiText("ui.app.src.voicecard.7c9cef0739");
    speechBtn.disabled = true;
    try {
      const said = await shell<string>("voice_fetch", { model: speakVoice, kind: "speak" });
      speechNote.textContent = said;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), 1500);
    } catch (e) {
      speechNote.className = "receipt err";
      speechNote.textContent = humanError(e).text;
      speechBtn.disabled = false;
    }
  };

  box.append(voicePreferencesFields(), onOff, status, pickBox, actions, keepOff,
    el("p", "field-hint",
      uiText("ui.app.src.voicecard.193c1e810e")),
    el("hr", "card-split"),
    speakOff, speechPickBox, speechStatus, speechFields, fishFields,
    (()=>{const field=voiceField(uiText("ui.app.src.voicecard.7e6a2f61ab"));field.append(sample);return field})(), audio, speechActions,
    el("p", "field-hint",
      uiText("ui.app.src.voicecard.e729dcccfb")));
  const showProvider=()=>{speechFields.hidden=speakVoice!=='xtts';fishFields.hidden=speakVoice!=='fish';speechBtn.hidden=speakVoice!=='xtts';speechNote.hidden=speakVoice!=='xtts'};
  showProvider();
  const observer=new MutationObserver(()=>{if(!box.isConnected){cancelPreview();fishKey.value='';observer.disconnect();if(timer)clearTimeout(timer)}});
  observer.observe(document.body,{childList:true,subtree:true});

  const onSection = (event: Event) => {
    if (!box.closest(".page")) {
      removeEventListener("frame-section", onSection);
      if (timer) { clearTimeout(timer); timer = null; }
      return;
    }
    if ((event as CustomEvent).detail === "settings" && box.isConnected) void refresh();
    else {cancelPreview();fishKey.value='';if (timer) { clearTimeout(timer); timer = null; }}
  };
  addEventListener("frame-section", onSection);
  const onControl=()=>{cancelPreview();if(!box.isConnected)removeEventListener('frame-live-action',onControl)};
  addEventListener('frame-live-action',onControl);
  void refresh();

  return {
    el: card(uiText("ui.app.src.voicecard.24a0d703af"), box,
      uiText("ui.app.src.voicecard.2dc4e89ae1")),
    enabled: () => enabled,
    model: () => model,
    keepLoaded: () => keep,
    speak: () => speak,
    speakVoice: () => speakVoice,
  };
}
