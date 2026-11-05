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
  if (!state.ready) return `Агент отвечает текстом: ${state.why}.`;
  const device = state.device_actual ? ` · ${state.device_actual.toUpperCase()}` : '';
  switch (state.worker_phase) {
    case 'loading': return 'XTTS загружает модель. Первый запуск займёт больше времени.';
    case 'warming_voice': return 'XTTS подготавливает выбранный голос. Пробный синтез проходит без звука.';
    case 'busy': return `XTTS озвучивает ответ${device}.`;
    case 'paused': return 'Голос на паузе. Продолжи работу агента, чтобы он снова говорил.';
    case 'error': return `Не удалось подготовить голос: ${state.worker_error || 'подробности в журнале XTTS'}`;
    case 'unknown': return 'XTTS установлен. Не удалось проверить процесс; смотри журнал.';
  }
  return state.worker_ready === true
    ? `Голос готов${device}. Модель и выбранный голос прогреты.`
    : 'XTTS установлен. Ожидаю запуск голоса после сохранения настроек.';
}

function mb(bytes: number | undefined): string {
  return bytes ? `${Math.round(bytes / MB)} МБ` : "0 МБ";
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
  const status = el("p", "field-hint", "спрашиваю канал…");
  const pickBox = el("div");
  const actions = el("div", "actions");
  const note = el("span", "receipt");
  const fetchBtn = button("Скачать модель", "quiet", () => void start());
  actions.append(fetchBtn, note);

  const onOff = toggle("Расшифровывать голосовые на этом ПК", enabled, (v) => {
    enabled = v;
    voice.enabled = v;
    void refresh();
  });
  const keepOff = toggle("Держать модель в памяти между голосовыми", keep, (v) => {
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
      title: m.title + (m.installed ? " · скачана" : ` · ${m.size_mb} МБ`),
      text: m.note,
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
      note.textContent = `${down!.note || 'Скачиваю'} · ${down!.model}: ${mb(down!.got_bytes)} из ~${mb(total)}`;
    } else if (down && down.state === "failed") {
      note.className = "receipt err";
      note.textContent = down.note;
    } else if (down && down.state === "running") {
      // Помощник качал и пропал: окно закрыли, машину усыпили, процесс убит.
      // Вечное «качаю…» здесь было бы враньём, поэтому запись читается со
      // сроком годности.
      note.className = "receipt err";
      note.textContent = "скачивание оборвалось — нажми ещё раз";
    } else if (down && down.state === "done" && state.installed.path) {
      note.className = "receipt ok";
      note.textContent = `модель на месте, ${mb(state.installed.bytes)}`;
    }
    if (!state.library.present) {
      if (state.library.downloadable) {
        // 1.2.1: движок не в установщике — он приедет той же кнопкой, первым.
        status.textContent =
          `Агент не слышит: движок голоса ещё не скачан. «Скачать модель» сначала скачает его ` +
          `(~${state.library.size_mb ?? "?"} МБ, один раз на обе половины голоса), потом модель.`;
        return;
      }
      status.textContent = state.library.why + ". Голос в этой сборке не поднимется.";
      fetchBtn.disabled = true;
      return;
    }
    const where = `Модели лежат в ${state.dir}.`;
    if (state.ready) {
      status.textContent =
        `Агент слышит: модель ${state.installed.model || "?"} (${mb(state.installed.bytes)}) на диске. ` +
        (state.why ? state.why + ". " : "") +
        `Расшифровка идёт на процессоре, примерно за половину длительности записи. ${where}`;
    } else {
      status.textContent = `Агент не слышит: ${state.why}. ${where}`;
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
        deviceNote.textContent=speech.library.cuda_supported===false?'Этот runtime собран для CPU. Для CUDA нужен совместимый runtime и видеокарта; веса модели остаются теми же.':'CUDA требует совместимого runtime и видеокарты. На машине без GPU используй CPU; менять модель не нужно.';
        if (names.join('\n') !== speakerSelect.dataset.catalog) {
          speakerSelect.replaceChildren(...names.map(name => new Option(name, name)));
          const selected = String(voice.tts_speaker || speech.speaker || 'Ana Florence');
          if (!names.includes(selected)) speakerSelect.append(new Option(selected+' · недоступен в каталоге', selected));
          speakerSelect.value = selected;
          speakerSelect.dataset.catalog = names.join('\n');
          speakerSelect.disabled = !names.length;
          speakerCount.textContent = names.length ? `${names.length} голосов установленной модели` : 'Каталог появится после установки XTTS';
        }
        fishKeyNote.textContent = speech.fish?.configured ? 'Ключ сохранён приватно на этом ПК.' : 'Собственный ключ Fish ещё не добавлен.';
        if(!previewing&&speakVoice==='fish'&&speech.fish?.last_request?.status==='unknown'){
          const last=speech.fish.last_request;
          previewNote.textContent='Fish · '+last.request_id+' · результат неизвестен'+(last.cancelled?' · аудио отменено':'')+'. Автоматического повтора нет.';
        }
        previewBtn.disabled = previewing || !speak || speech.worker_phase === 'paused' || (speakVoice === 'xtts' && speech.worker_ready !== true)
          || (speakVoice === 'fish' && (!speech.fish?.configured || !voice.fish_model || !voice.fish_voice));
        if (!previewing && speakVoice === 'xtts') previewNote.textContent = speech.worker_ready === true ? '' : speechStatusText(mine);
      } else {
        speechStatus.textContent = "этот канал про голос агента ничего не знает";
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
      if (box.isConnected) status.textContent = "канал не ответил про голос: " + humanError(e).text;
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
    note.textContent = "запускаю помощника…";
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
  const speechStatus = el("p", "field-hint", "спрашиваю канал…");
  const speechPickBox = el("div");
  const speechActions = el("div", "actions");
  const speechNote = el("span", "receipt");
  const speechBtn = button("Скачать и установить XTTS", "quiet", () => void startVoice());
  speechActions.append(speechBtn, speechNote);
  const speechFields = el("div", "form-grid two voice-provider-fields");
  const fishFields = el('div', 'form-grid two voice-provider-fields');
  const fishKeyNote = el('small', 'field-hint');
  const fishKey = el('input', 'field-input') as HTMLInputElement;
  fishKey.type='password';fishKey.autocomplete='new-password';fishKey.maxLength=4096;
  fishKey.setAttribute('aria-label','Собственный API-ключ Fish');
  const keyLabel=voiceField('API-ключ Fish');keyLabel.classList.add('voice-full-row');keyLabel.append(fishKey,fishKeyNote);
  const keyActions=el('div','actions voice-full-row');
  const saveKey=button('Сохранить ключ','quiet',async()=>{
    const key=fishKey.value;fishKey.value='';saveKey.disabled=true;
    try{const response=await fetch(channelURL('/api/voice/fish-key'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
      if(!response.ok)throw new Error('Не удалось приватно сохранить ключ Fish');
      fishKeyNote.textContent='Ключ сохранён приватно на этом ПК.';void refresh();
    }catch(e){fishKeyNote.textContent=humanError(e).text}finally{saveKey.disabled=false}
  });
  const deleteKey=button('Удалить ключ','quiet',async()=>{
    cancelPreview();fishKey.value='';
    const response=await fetch(channelURL('/api/voice/fish-key'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delete:true})});
    fishKeyNote.textContent=response.ok?'Ключ удалён.':'Не удалось удалить ключ.';void refresh();
  });
  keyActions.append(saveKey,deleteKey);
  const fishModelLabel=voiceField('Модель Fish');
  const fishModel=document.createElement('select');fishModel.className='field-input';
  fishModel.append(new Option('Выбери модель явно',''),...['s2.1-pro','s2.1-pro-free','s2-pro','drama-3','s1'].map(id=>new Option(id,id)));
  fishModel.value=String(voice.fish_model||'');fishModel.onchange=()=>{cancelPreview();voice.fish_model=fishModel.value;void refresh()};fishModelLabel.append(fishModel);
  const fishVoiceLabel=voiceField('Идентификатор голоса Fish');
  const fishVoice=el('input','field-input') as HTMLInputElement;
  fishVoice.value=String(voice.fish_voice||'');fishVoice.maxLength=128;
  fishVoice.oninput=()=>{cancelPreview();voice.fish_voice=fishVoice.value;void refresh()};fishVoiceLabel.append(fishVoice);
  const fishDeliveryLabel=voiceField('Подача и интонация');fishDeliveryLabel.classList.add('voice-full-row');
  const fishDelivery=el('textarea','field-input') as HTMLTextAreaElement;
  fishDelivery.value=String(voice.fish_delivery||'');fishDelivery.maxLength=1000;
  fishDelivery.placeholder='Например: спокойно, тепло, с лёгкой улыбкой';
  fishDelivery.oninput=()=>{cancelPreview();voice.fish_delivery=fishDelivery.value};fishDeliveryLabel.append(fishDelivery);
  fishFields.append(keyLabel,keyActions,fishModelLabel,fishVoiceLabel,fishDeliveryLabel,
    el('p','field-hint voice-full-row','Текст и подача отправляются Fish. Каждый новый образец — отдельный запрос по тарифу Fish. При обрыве результат может остаться неизвестным; автоматического повтора нет. Ключ не входит в настройки агента.'));
  const deviceLabel=voiceField('Вычисления XTTS');
  const deviceSelect=document.createElement('select');deviceSelect.className='field-input';
  const cudaOption=new Option('CUDA · совместимая NVIDIA GPU','cuda');
  deviceSelect.append(new Option('CPU · работает без видеокарты','cpu'),cudaOption);
  deviceSelect.value=String(voice.tts_device||'cpu');deviceSelect.onchange=()=>{cancelPreview();voice.tts_device=deviceSelect.value};
  const deviceNote=el('small','field-hint','CPU — базовый путь; поддержку CUDA определяет runtime.');
  deviceLabel.append(deviceSelect,deviceNote);speechFields.append(deviceLabel);
  const speakerLabel = voiceField('Голос XTTS');
  const speakerSelect = document.createElement('select');speakerSelect.className='field-input';
  speakerSelect.onchange=()=>{cancelPreview();voice.tts_speaker=speakerSelect.value};
  const speakerCount=el('small','field-hint','Читаю каталог установленной модели…');
  speakerLabel.append(speakerSelect,speakerCount);speechFields.append(speakerLabel);
  const sample=el('input','field-input') as HTMLInputElement;
  sample.value='Привет. Это мой голос. Его можно изменить.';sample.maxLength=300;
  sample.setAttribute('aria-label','Текст для прослушивания голоса');
  const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.hidden=true;
  audio.setAttribute('aria-label','Образец выбранного голоса');
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
  const previewBtn=button('Прослушать голос','quiet',async()=>{
    if (previewing) return;
    cancelPreview();const epoch=previewEpoch;const controller=new AbortController();
    const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
    const url=channelURL('/api/voice/preview');
    activePreview={id,cancelURL:channelURL('/api/voice/intent'),controller};
    previewing=true;previewBtn.disabled=true;previewNote.textContent=speakVoice==='fish'?'Fish готовит образец · запрос '+id:'Готовлю образец локально…';
    try {
      // Synthesis can exceed the event tunnel's 25s. Use the same authenticated
      // HTTP channel, not a second service or the model/Telegram transport.
      const response=await fetch(url,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},
        body:JSON.stringify({request_id:id,voice:speakVoice,speaker:speakerSelect.value,language:voice.tts_language||'ru',
          reference:voice.tts_reference||'',fish_model:voice.fish_model,fish_voice:voice.fish_voice,fish_delivery:voice.fish_delivery,text:sample.value})});
      if(!response.ok)throw new Error(await response.text());
      const result=await response.json();
      if(result.mime!=='audio/wav'||typeof result.base64!=='string')throw new Error('Нет аудио в ответе синтеза');
      if(epoch!==previewEpoch||!box.isConnected||url!==channelURL('/api/voice/preview'))return;
      const live=await api<VoiceState>('/api/voice');
      if(epoch!==previewEpoch||!box.isConnected||url!==channelURL('/api/voice/preview')
        ||live.speech?.worker_phase==='paused'||live.speech?.enabled!==true)return;
      audio.src='data:audio/wav;base64,'+result.base64;audio.hidden=false;
      await audio.play();previewNote.textContent='Образец готов. Выбор сохранится вместе с настройками.';
    }catch(e){if(epoch===previewEpoch){
      let outcome='unknown';
      if(speakVoice==='fish')try{const response=await fetch(channelURL('/api/voice/intent?request_id='+id));
        if(response.ok){const receipt=await response.json();outcome=receipt.status||'unknown';}}catch{}
      if(epoch===previewEpoch)previewNote.textContent=speakVoice==='fish'
        ? 'Запрос '+id+' · '+outcome+'. Автоматического повтора нет. '+humanError(e).text:humanError(e).text;
    }
    }finally{if(epoch===previewEpoch){previewing=false;activePreview=null;void refresh()}}
  });
  previewBtn.disabled=true;
  speechActions.append(previewBtn,previewNote);
  const stopPreview=button('Остановить образец','quiet',()=>{cancelPreview();previewNote.textContent='Прослушивание отменено. Уже принятый Fish запрос может быть оплачен.';void refresh()});
  speechActions.append(stopPreview);
  for (const [key, title, fallback] of [
    ["tts_language", "Язык синтеза", "ru"],
    ["tts_reference", "Свой образец голоса (локальный WAV, необязательно)", ""],
  ]) {
    const label = voiceField(title);
    const input = el("input", "field-input") as HTMLInputElement;
    input.value = String(voice[key] || fallback);
    input.oninput = () => { cancelPreview();voice[key] = input.value; };
    label.append(input); speechFields.append(label);
  }
  const speakOff = toggle("Отвечать голосом, когда сочтёт нужным", speak, (v) => {
    speak = v;
    cancelPreview();
    voice.speak = v;
    void refresh();
  });
  let speechPicker = choice<string>([], speakVoice, () => {});

  const drawVoices = (state: SpeechState) => {
    const items = state.catalog.map((one) => ({
      value: one.id,
      title: one.title,
      text: one.note,
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
      speechStatus.textContent=!speak?'Голос агента выключен.':state.fish?.configured
        ? 'Fish озвучивает через твой API-ключ. Выбери модель, голос и подачу; сохрани настройки.'
        : 'Добавь собственный API-ключ Fish, выбери модель и голос. Скачивать XTTS не требуется.';
      return;
    }
    const down = state.download;
    const busy = !!down && down.state === "running" && (down.active ?? fresh(down.at || ""));
    speechBtn.disabled = busy;
    if (busy) {
      speechNote.className = "receipt";
      speechNote.textContent = (down!.note || 'Устанавливаю XTTS') + (down!.total_bytes ? ` · ${mb(down!.got_bytes)} из ${mb(down!.total_bytes)}` : '');
    } else if (down && down.state === "failed") {
      speechNote.className = "receipt err";
      speechNote.textContent = String(down.error || "не скачалось");
    } else if (down && down.state === "running") {
      speechNote.className = "receipt err";
      speechNote.textContent = "скачивание оборвалось — нажми ещё раз";
    } else if (down && down.state === "done") {
      speechNote.className = "receipt ok";
      speechNote.textContent = "голос на месте";
    }
    if (!state.library.present) {
      if (state.library.downloadable) {
        speechStatus.textContent =
          `XTTS ещё не установлен. Кнопка устанавливает отдельный runtime и модель из комплекта; Whisper остаётся независимым.`;
        return;
      }
      speechStatus.textContent = state.library.why + ". Говорить в этой сборке нечем.";
      speechBtn.disabled = true;
      return;
    }
    speechStatus.textContent = speechStatusText(state);
  };

  const startVoice = async () => {
    speechNote.className = "receipt";
    speechNote.textContent = "запускаю помощника…";
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
      "Распознавание использует около 1,5 ГБ памяти. На компьютере модель выгружается после обработки, " +
      "на сервере остаётся в памяти. Изменения применяются после перезапуска."),
    el("hr", "card-split"),
    speakOff, speechPickBox, speechStatus, speechFields, fishFields,
    (()=>{const field=voiceField('Текст для прослушивания');field.append(sample);return field})(), audio, speechActions,
    el("p", "field-hint",
      "Whisper распознаёт твои голосовые независимо от выбранного синтеза. XTTS работает локально, Fish — через API. Выбранный голос применяется после сохранения. XTTS-v2 и её аудио — только для некоммерческого использования (Coqui Public Model License)."));
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
    el: card("Голос", box,
      "Whisper распознаёт речь на этом ПК. Для озвучки выбери локальный XTTS или Fish с собственным API-ключом. Распознанный текст получает выбранная модель агента."),
    enabled: () => enabled,
    model: () => model,
    keepLoaded: () => keep,
    speak: () => speak,
    speakVoice: () => speakVoice,
  };
}
