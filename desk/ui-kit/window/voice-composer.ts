import { VoiceSession, type VoiceTarget } from './voice-session';
import { voiceHelp, voicePreferences, shortcutFromEvent, setVoiceNativeState, type VoiceNativeState } from './voice-preferences';
import { electron, shell } from './api';
import { toast } from './lib';
import { pausePaperAudio } from '../paper-audio';
import './styles/voice.css';

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2,'0')}`;
/** Real waveform only. Until decoding finishes, the neutral track has no invented amplitude. */
export async function drawVoiceWave(canvas: HTMLCanvasElement, blob: Blob) {
  const context = new AudioContext();
  try {
    const audio = await context.decodeAudioData(await blob.arrayBuffer());
    if (!canvas.isConnected) return;
    const samples = audio.getChannelData(0), ctx = canvas.getContext('2d'); if (!ctx) return;
    const count = 64, step = Math.max(1, Math.floor(samples.length / count));
    const peaks = Array.from({length:count}, (_, i) => {
      let peak = 0; for (let j=i*step; j<Math.min(samples.length,(i+1)*step); j++) peak=Math.max(peak,Math.abs(samples[j])); return peak;
    });
    const maximum = Math.max(.01,...peaks), width=canvas.width, height=canvas.height;
    ctx.clearRect(0,0,width,height); ctx.fillStyle=getComputedStyle(canvas).color;
    peaks.forEach((p,i) => { const h=Math.max(2,p/maximum*(height-4));ctx.fillRect(i*width/count,(height-h)/2,Math.max(2,width/count-2),h); });
  } catch { /* Partial live chunks can be undecodable until the next flush. */ }
  finally { await context.close(); }
}
export function mountVoiceAttachment(node: HTMLElement, url: string) {
  const audio=new Audio(url), play=node.querySelector<HTMLButtonElement>('[data-voice-play]')!, canvas=node.querySelector<HTMLCanvasElement>('canvas')!, time=node.querySelector<HTMLElement>('[data-voice-time]')!;
  let disposed=false;
  const paint=()=>{play.textContent=audio.paused?'▶':'Ⅱ';play.setAttribute('aria-label',audio.paused?'Прослушать':'Пауза');time.textContent=clock(audio.currentTime)+' / '+clock(Number.isFinite(audio.duration)?audio.duration:0);};
  play.onclick=()=>{if(audio.paused){pausePaperAudio();void audio.play().catch(e=>toast(String(e)));}else audio.pause();};
  audio.addEventListener('timeupdate',paint);audio.addEventListener('loadedmetadata',paint);audio.addEventListener('pause',paint);audio.addEventListener('play',paint);
  canvas.onclick=e=>{if(Number.isFinite(audio.duration))audio.currentTime=Math.max(0,Math.min(1,e.offsetX/canvas.clientWidth))*audio.duration;};
  void fetch(url).then(r=>r.blob()).then(blob=>{if(!disposed)return drawVoiceWave(canvas,blob);}).catch(()=>{});
  const quiet=()=>audio.pause();addEventListener('helene:recording',quiet);
  return ()=>{disposed=true;audio.pause();audio.removeAttribute('src');audio.load();removeEventListener('helene:recording',quiet);};
}
export function mountVoiceComposer(options: { mic: HTMLButtonElement; composer: HTMLElement; target(): VoiceTarget | null; send(blob: Blob,target: VoiceTarget): Promise<void> }) {
  const bar=document.createElement('section');bar.className='voice-draft';bar.hidden=true;bar.setAttribute('aria-label','Голосовое сообщение');
  bar.innerHTML='<button type="button" class="voice-round" data-preview aria-label="Прослушать">▶</button><div class="voice-draft-main"><div class="voice-draft-heading"><span data-state role="status"></span><span data-time>0:00</span></div><canvas width="384" height="36" aria-label="Звуковая волна"></canvas><span class="voice-recipient" data-target></span></div><button type="button" data-resume class="voice-round" aria-label="Продолжить запись">●</button><button type="button" data-send class="voice-round voice-send" aria-label="Отправить голосовое">↑</button><button type="button" data-cancel class="voice-round" aria-label="Удалить запись">×</button>';
  options.composer.before(bar);
  const hint=document.createElement('p');hint.className='voice-hint';hint.innerHTML='Для записи голосом нажмите на значок микрофона или используйте <button type="button">горячие клавиши</button>.';
  options.composer.after(hint);hint.querySelector('button')!.onclick=voiceHelp;
  const canvas=bar.querySelector<HTMLCanvasElement>('canvas')!, preview=new Audio();
  let previewUrl='', previewBlob: Blob | null=null, native=false, held=false, heldCode='', disposed=false;
  let analyser: AnalyserNode | null=null, context: AudioContext | null=null, stream: MediaStream | null=null, previousState='idle';
  const paint = (session: VoiceSession) => {
    if(disposed)return;
    bar.hidden=session.state==='idle';bar.dataset.state=session.state;
    options.mic.classList.toggle('recording',session.state==='recording');
    options.mic.title=`Записать голосовое · ${voicePreferences().hotkey}`;
    options.mic.disabled=session.state==='starting'||session.state==='sending';
    bar.querySelector('[data-state]')!.textContent=({idle:'',starting:'Включаю микрофон…',recording:'Запись',paused:'Можно послушать или продолжить',sending:'Отправляю…'})[session.state];
    bar.querySelector('[data-time]')!.textContent=clock(session.seconds);
    bar.querySelector('[data-target]')!.textContent=session.target?'Для '+session.target.name:'';
    const busy=session.state==='starting'||session.state==='sending';
    bar.querySelector<HTMLButtonElement>('[data-preview]')!.disabled=session.state!=='paused'||!session.blob;
    bar.querySelector<HTMLButtonElement>('[data-resume]')!.disabled=busy;
    bar.querySelector('[data-resume]')!.textContent=session.state==='recording'?'Ⅱ':'●';
    bar.querySelector<HTMLButtonElement>('[data-send]')!.disabled=busy;
    bar.querySelector<HTMLButtonElement>('[data-cancel]')!.disabled=session.state==='sending';
    if(session.state==='recording') {
      preview.pause();pausePaperAudio();
      if(previousState!=='recording')dispatchEvent(new Event('helene:recording'));
      if(session.stream && stream!==session.stream){
        stream=session.stream;void context?.close();context=new AudioContext();analyser=context.createAnalyser();analyser.fftSize=256;context.createMediaStreamSource(stream).connect(analyser);
      }
    } else if(session.state==='paused' && session.blob && session.blob!==previewBlob) {
      dispatchEvent(new Event('helene:recording-ended'));
      preview.pause();if(previewUrl)URL.revokeObjectURL(previewUrl);previewBlob=session.blob;previewUrl=URL.createObjectURL(session.blob);preview.src=previewUrl;void drawVoiceWave(canvas,session.blob);
    }
    if(session.state==='idle'){dispatchEvent(new Event('helene:recording-ended'));preview.pause();if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl='';previewBlob=null;stream=null;analyser=null;void context?.close();context=null;}
    previousState=session.state;
    if(electron?.shell==='python')void shell('voice_status',{state:session.state,seconds:Math.floor(session.seconds),target:session.target?.name||''}).catch(()=>{});
  };
  const session=new VoiceSession({
    media:()=>{if(!navigator.mediaDevices?.getUserMedia)throw Error('В этом окне микрофон недоступен');return navigator.mediaDevices.getUserMedia({audio:true});},
    recorder:stream=>{const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'].find(m=>MediaRecorder.isTypeSupported(m));return new MediaRecorder(stream,mime?{mimeType:mime}:undefined);},
    send:options.send,changed:paint,error:toast,
  });
  const start=(mode:'pause'|'send')=>{const target=options.target();if(!target){toast('Выбери доступный чат для записи.');return;}void session.hold(target,mode);};
  options.mic.onclick=()=>{if(session.state==='recording')session.pause();else start('pause');};
  bar.querySelector<HTMLButtonElement>('[data-resume]')!.onclick=options.mic.onclick as () => void;
  bar.querySelector<HTMLButtonElement>('[data-send]')!.onclick=()=>void session.send();
  bar.querySelector<HTMLButtonElement>('[data-cancel]')!.onclick=()=>session.cancel();
  bar.querySelector<HTMLButtonElement>('[data-preview]')!.onclick=()=>{if(preview.paused){pausePaperAudio();void preview.play().catch(e=>toast(String(e)));}else preview.pause();};
  for(const event of ['play','pause','ended'])preview.addEventListener(event,()=>{bar.querySelector('[data-preview]')!.textContent=preview.paused?'▶':'Ⅱ';});
  const down=(e:KeyboardEvent)=>{
    if((e.target as Element)?.closest?.('[data-hotkey]'))return;
    if(e.key==='Escape'&&session.state!=='idle'){e.preventDefault();session.cancel();return;}
    if(e.key==='Shift'&&session.state==='recording'&&!e.repeat){e.preventDefault();session.pause();return;}
    if(native||e.repeat||e.isComposing||e.defaultPrevented||shortcutFromEvent(e)!==voicePreferences().hotkey)return;
    e.preventDefault();held=true;heldCode=e.code;start('pause');
  };
  const up=(e:KeyboardEvent)=>{if(held&&(e.code===heldCode||['Control','Alt','Meta','Shift'].includes(e.key))){held=false;e.preventDefault();session.release();}};
  const blur=()=>{if(held){held=false;session.pause();}};
  const shortcut=(e:Event)=>{
    const detail=(e as CustomEvent).detail;
    if(detail.action==='down')start(document.hasFocus()&&!document.hidden?'pause':'send');
    else if(detail.action==='up')session.release();
    else if(detail.action==='pause')session.pause();
    else if(detail.action==='cancel')session.cancel();
  };
  const configure=async()=>{
    if(electron?.shell!=='python')return;
    try{const state=await shell<VoiceNativeState>('voice_shortcuts',{...voicePreferences()});native=state.active;setVoiceNativeState(state);}catch{native=false;}
    options.mic.title=`Записать голосовое · ${voicePreferences().hotkey}`;
  };
  addEventListener('keydown',down);addEventListener('keyup',up);addEventListener('blur',blur);
  addEventListener('helene:voice-shortcut',shortcut);addEventListener('helene:voice-preferences',configure);
  const nativeState=(e:Event)=>{const state=(e as CustomEvent<VoiceNativeState>).detail;native=state.active;setVoiceNativeState(state);};
  addEventListener('helene:voice-native-state',nativeState);
  const timer=setInterval(()=>{
    if(session.state!=='recording')return;
    bar.querySelector('[data-time]')!.textContent=clock(session.seconds);
    if(session.seconds>=300){session.pause();toast('Пять минут — предел одного сообщения. Запись осталась на паузе.');}
    if(analyser){const values=new Uint8Array(analyser.frequencyBinCount);analyser.getByteTimeDomainData(values);const ctx=canvas.getContext('2d');if(ctx){ctx.clearRect(0,0,384,36);ctx.fillStyle=getComputedStyle(canvas).color;for(let i=0;i<64;i++){const h=Math.max(2,Math.abs(values[i*2]-128)/128*36);ctx.fillRect(i*6,(36-h)/2,3,h);}}}
  },100);
  void configure();
  const dispose=()=>{disposed=true;clearInterval(timer);session.dispose();preview.pause();if(previewUrl)URL.revokeObjectURL(previewUrl);void context?.close();removeEventListener('keydown',down);removeEventListener('keyup',up);removeEventListener('blur',blur);removeEventListener('helene:voice-shortcut',shortcut);removeEventListener('helene:voice-preferences',configure);removeEventListener('helene:voice-native-state',nativeState);};
  addEventListener('pagehide',dispose,{once:true});
  return {session,dispose,sync:()=>{options.mic.disabled=session.state==='starting'||session.state==='sending'||(session.state==='idle'&&!options.target());}};
}
