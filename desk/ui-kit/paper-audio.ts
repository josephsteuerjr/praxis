import { esc } from './text';
import type { PaperMedia, MediaClient } from './paper-media';

interface Wave { loadBlob(blob: Blob): Promise<void>; destroy(): void }
interface WaveModule { create(options: {container: HTMLElement; media: HTMLMediaElement; height: number; waveColor: string; progressColor: string; barWidth: number; barGap: number; dragToSeek: boolean; interact: boolean; normalize: boolean}): Wave }
let loader: (() => Promise<WaveModule>) | undefined;
export function setAudioPlayer(load: () => Promise<WaveModule>) { loader = load; }
const activePlayers = new Set<HTMLAudioElement>();
export function pausePaperAudio() { for (const player of activePlayers) player.pause(); }
const clock = (n: number) => { n = Number.isFinite(n) ? Math.max(0, n) : 0; return `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`; };
const title = (media: PaperMedia) => /^(?:voice[-_]|audio[-_]|voice$|[a-f0-9]{32}\.)/i.test(media.name) ? 'Голосовое сообщение' : media.name;

export function audioHTML(m: PaperMedia, data: string) {
  return `<figure class="paper-media paper-audio" ${data}><button type="button" class="paper-audio-toggle" data-audio-toggle aria-label="Воспроизвести">▶</button><div class="paper-audio-main"><div class="paper-audio-heading"><span class="paper-audio-name">${esc(title(m))}</span><span data-audio-time>0:00</span></div><span data-audio-status role="status"></span><div class="paper-audio-detail" hidden><div class="paper-wave" aria-hidden="true"></div><input class="paper-audio-seek" type="range" min="0" max="1000" value="0" aria-label="Позиция воспроизведения"><div class="paper-audio-meta"><button type="button" class="paper-link" data-paper-save>Сохранить…</button></div><div class="paper-transcription" data-voice-state hidden></div><button type="button" class="paper-link" data-voice-retry hidden>Повторить расшифровку</button></div></div><button type="button" class="paper-audio-details" data-audio-details aria-label="Детали голосового" aria-expanded="false">⌄</button></figure>`;
}

/** Audio belongs to the mounted conversation, not its scrolling/virtualized row. */
export function mountPaperAudio(root: HTMLElement, client: MediaClient) {
  type Track = {media: PaperMedia; audio: HTMLAudioElement; waveform: HTMLElement; wave?: Wave; waveMedia?: HTMLAudioElement; waveLoading?: Promise<void>; blob?: Blob; url?: string; loading?: Promise<void>; error?: string; listeners: (() => void)[]};
  const tracks = new Map<string, Track>();
  const statuses = new Map<string, Awaited<ReturnType<NonNullable<MediaClient['voice']>>>>();
  const retrying = new Set<string>();
  let polling = false;
  let active: Track | undefined, disposed = false, generation = 0, volume = 1, speed = 1;
  const bar = document.createElement('section'); bar.className = 'paper-audio-bar'; bar.hidden = true; bar.setAttribute('aria-label', 'Текущая аудиозапись');
  bar.innerHTML = '<button type="button" data-audio-prev aria-label="Предыдущая запись">◀</button><button type="button" data-audio-bar-play aria-label="Воспроизвести">▶</button><button type="button" data-audio-next aria-label="Следующая запись">▶|</button><span data-audio-title></span><span data-audio-bar-time></span><label>Громкость <input data-audio-volume aria-label="Громкость" type="range" min="0" max="1" step="0.05" value="1"></label><label>Скорость <select data-audio-speed aria-label="Скорость"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label><button type="button" data-audio-close aria-label="Закрыть проигрыватель">×</button>';
  // Outside the feed's replaceChildren/scroll transform; disposed with the owner.
  const composer=root.querySelector('#composer');
  if(composer?.parentElement) { bar.dataset.docked='true'; composer.before(bar); }
  else document.body.append(bar);
  const nodes = () => Array.from(root.querySelectorAll<HTMLElement>('.paper-audio[data-paper-path]'));
  const media = (node: HTMLElement): PaperMedia => ({path: node.dataset.paperPath!, kind: 'audio', name: node.dataset.paperName!, mime: node.dataset.paperMime, originalPath: node.dataset.paperOriginal});
  const key = (node: HTMLElement) => client.key(media(node));
  function paint() {
    if (disposed) return;
    for (const node of nodes()) {
      const status = statuses.get(key(node));
      const recognition=status?.status==='error'?'Не расшифровано · детали':status?.status==='transcribing'?'Распознаю речь…':status?.status==='unavailable'?'Расшифровка недоступна · детали':'';
      node.querySelector('[data-audio-status]')!.textContent=recognition;
      if (status) {
        const label = node.querySelector<HTMLElement>('[data-voice-state]')!, retry = node.querySelector<HTMLButtonElement>('[data-voice-retry]')!;
        label.hidden = false;
        label.textContent = status.status === 'ready' ? '[Транскрипт голосового]: '+status.transcript : status.status === 'error' ? 'Не расшифровано: '+status.error : status.status === 'transcribing' ? 'Распознаю речь…' : status.status === 'unavailable' ? 'Состояние расшифровки недоступно' : 'Ожидает распознавания';
        retry.hidden = status.status !== 'error'; retry.disabled = retrying.has(key(node)) || !!status.retry_requested;
        retry.textContent = status.retry_requested ? 'Повтор запрошен' : 'Повторить расшифровку';
      }
      const track = tracks.get(key(node)); if (!track) continue;
      const a = track.audio, toggle = node.querySelector<HTMLButtonElement>('[data-audio-toggle]')!;
      toggle.textContent = a.paused ? '▶' : 'Ⅱ'; toggle.setAttribute('aria-label', a.paused ? 'Воспроизвести' : 'Пауза');
      node.querySelector('[data-audio-time]')!.textContent = `${clock(a.currentTime)} / ${clock(a.duration)}`;
      node.querySelector('[data-audio-status]')!.textContent = track.error || (track.loading ? 'Загружаю…' : recognition);
      const seek = node.querySelector<HTMLInputElement>('.paper-audio-seek')!; seek.value = String(Number.isFinite(a.duration) && a.duration ? a.currentTime / a.duration * 1000 : 0);
      const slot = node.querySelector<HTMLElement>('.paper-wave')!;
      if (track.waveform.parentElement !== slot) slot.replaceChildren(track.waveform);
    }
    const visibleActive = active && nodes().some(node => {
      if(key(node)!==client.key(active!.media))return false;
      const rect=node.getBoundingClientRect();return rect.bottom>0 && rect.top<innerHeight;
    });
    bar.hidden = !active || active.audio.paused || !!visibleActive || !root.classList.contains('with-panel');
    if (active) {
      const a = active.audio;
      bar.querySelector('[data-audio-title]')!.textContent = title(active.media);
      bar.querySelector('[data-audio-bar-time]')!.textContent = `${clock(a.currentTime)} / ${clock(a.duration)}`;
      const b = bar.querySelector<HTMLButtonElement>('[data-audio-bar-play]')!; b.textContent = a.paused ? '▶' : 'Ⅱ'; b.setAttribute('aria-label', a.paused ? 'Воспроизвести' : 'Пауза');
      const queue = nodes(), i = queue.findIndex(n => key(n) === client.key(active!.media));
      bar.querySelector<HTMLButtonElement>('[data-audio-prev]')!.disabled = i <= 0;
      bar.querySelector<HTMLButtonElement>('[data-audio-next]')!.disabled = i < 0 || i >= queue.length - 1;
    }
  }
  function clearWave(t: Track) {
    t.wave?.destroy();t.wave=undefined;
    if(t.waveMedia){t.waveMedia.removeAttribute('src');t.waveMedia.load();}
    t.waveMedia=undefined;
  }
  function drop(k: string, t: Track) {
    t.audio.pause(); activePlayers.delete(t.audio);clearWave(t);
    for (const off of t.listeners) off();
    t.audio.removeAttribute('src'); t.audio.load();
    if (t.url) URL.revokeObjectURL(t.url);
    tracks.delete(k);
  }
  function reconcile() {
    if (disposed) return;
    const kept = new Set(nodes().map(key));
    for (const [k,t] of tracks) if (!kept.has(k) && t !== active) drop(k,t);
    paint(); void pollVoice();
  }
  async function pollVoice() {
    if(disposed || polling || !client.voice || document.hidden) return;
    polling = true;
    try {
      for(const node of nodes()) {
        const m=media(node), k=key(node);
        if(!(m.path.startsWith('memory/.control/desk_inbox/attachments/') || m.path.startsWith('memory/telegram-media/')) || statuses.get(k)?.status === 'ready') continue;
        try { const status=await client.voice(m); if(!disposed) statuses.set(k,status); }
        catch(e) { if(!disposed) statuses.set(k,{status:'unavailable',error:String(e)}); }
        if(disposed) break;
      }
      paint();
    } finally { polling=false; }
  }
  function trackForMedia(m: PaperMedia) {
    const k = client.key(m); let t = tracks.get(k);
    if (!t) {
      const audio = new Audio(); audio.preload = 'metadata'; audio.volume = volume; audio.playbackRate = speed;
      t = {media:m, audio, waveform:document.createElement('div'), listeners:[]}; tracks.set(k,t);
      for (const event of ['play','pause','timeupdate','loadedmetadata','ended','error']) {
        const update = () => { if(event === 'play') { for(const other of activePlayers) if(other !== audio) other.pause(); activePlayers.add(audio); } if(event === 'error') t!.error = 'Формат не воспроизводится'; paint(); };
        audio.addEventListener(event,update); t.listeners.push(() => audio.removeEventListener(event,update));
      }
    }
    return t;
  }
  const trackFor = (node: HTMLElement) => trackForMedia(media(node));
  async function load(t: Track) {
    if (t.blob) return;
    if (t.loading) return t.loading;
    t.error = undefined;
    t.loading = (async () => {
      const blob = await client.bytes(t.media); if (!blob.size) throw new Error('Файл пуст');
      if (disposed || tracks.get(client.key(t.media))!==t) return;
      t.blob = blob;
      // Source first: neither a waveform import nor audio decoding gates play.
      t.url = URL.createObjectURL(blob); t.audio.src = t.url;
    })();
    try { await t.loading; } catch(e) {
      t.blob=undefined;
      if(t.url) URL.revokeObjectURL(t.url); t.url=undefined; t.error = 'Не удалось открыть. Нажми, чтобы повторить.';
      throw e;
    } finally { t.loading=undefined; paint(); }
  }
  async function waveform(t: Track) {
    if(t.wave || t.waveLoading || !loader)return;
    t.waveLoading=(async()=>{
      await load(t);const module=await loader!();
      if(disposed || !t.blob || tracks.get(client.key(t.media))!==t)return;
      const color=getComputedStyle(root).getPropertyValue('--ink-3').trim() || '#8a8277';
      // A separate, silent media element keeps optional history decoding from
      // changing the source, playback position or state of the live audio.
      t.waveMedia=new Audio();t.waveMedia.muted=true;
      t.wave=module.create({container:t.waveform,media:t.waveMedia,height:32,waveColor:color,progressColor:color,barWidth:2,barGap:2,dragToSeek:false,interact:false,normalize:true});
      await t.wave.loadBlob(t.blob);
    })();
    try {await t.waveLoading;}catch {clearWave(t);}
    finally{t.waveLoading=undefined;}
  }
  async function play(t: Track) {
    const seq = ++generation;
    if (active !== t) { active?.audio.pause(); active = t; }
    for(const a of activePlayers) if(a !== t.audio) a.pause();
    paint();
    try { await load(t); if(disposed || seq !== generation || active !== t) return; await t.audio.play(); }
    catch(e) { if(!disposed && seq === generation) { t.error = e instanceof Error ? e.message : String(e); paint(); } }
  }
  function toggle(t: Track) { if(t.audio.paused) void play(t); else { ++generation; t.audio.pause(); } }
  function onClick(e: Event) {
    const node = (e.target as Element).closest<HTMLElement>('.paper-audio[data-paper-path]');
    if(!node) return;
    if((e.target as Element).closest('[data-audio-details]')){
      const button=node.querySelector<HTMLButtonElement>('[data-audio-details]')!,detail=node.querySelector<HTMLElement>('.paper-audio-detail')!;
      detail.hidden=!detail.hidden;button.setAttribute('aria-expanded',String(!detail.hidden));
      if(!detail.hidden)void waveform(trackFor(node));
      return;
    }
    if((e.target as Element).closest('[data-voice-retry]') && client.voice) {
      const k=key(node); if(retrying.has(k)) return; retrying.add(k); paint();
      void client.voice(media(node),true).then(status=>{if(!disposed) statuses.set(k,status);},()=>{if(!disposed) statuses.set(k,{status:'unavailable',error:'Ответ о повторе не получен; проверь состояние.'});}).finally(()=>{retrying.delete(k);paint();});
      return;
    }
    if(!(e.target as Element).closest('[data-audio-toggle]')) return;
    e.preventDefault(); toggle(trackFor(node));
  }
  function onSeek(e: Event) {
    const input = e.target as HTMLInputElement; if(!input.matches('.paper-audio-seek')) return;
    const node = input.closest<HTMLElement>('.paper-audio')!, t=trackFor(node);
    if(Number.isFinite(t.audio.duration)) t.audio.currentTime = Number(input.value)/1000*t.audio.duration;
  }
  function onBar(e: Event) {
    const b=(e.target as Element).closest('button'); if(!b || !active) return;
    if(b.hasAttribute('data-audio-close')) { ++generation; active.audio.pause(); active=undefined; reconcile(); return; }
    if(b.hasAttribute('data-audio-bar-play')) { toggle(active); return; }
    const queue=nodes(), i=queue.findIndex(n=>key(n)===client.key(active!.media));
    const next=queue[i+(b.hasAttribute('data-audio-prev')?-1:1)]; if(next) void play(trackFor(next));
  }
  function onSettings(e: Event) {
    const input=e.target as HTMLInputElement;
    if(input.hasAttribute('data-audio-volume')) volume=Number(input.value);
    if(input.hasAttribute('data-audio-speed')) speed=Number(input.value);
    for(const t of tracks.values()) { t.audio.volume=volume; t.audio.playbackRate=speed; }
  }
  const autoplay: PaperMedia[]=[];
  let autoRunning=false, recording=false, autoEpoch=0, scope='';
  let detachAuto=()=>{};
  const cancelAuto=()=>{++autoEpoch;detachAuto();detachAuto=()=>{};autoRunning=false;};
  async function playNext() {
    if(autoRunning||recording||disposed||!autoplay.length)return;
    autoRunning=true;const epoch=autoEpoch;
    const t=trackForMedia(autoplay.shift()!);
    const done=()=>{detach();if(epoch!==autoEpoch)return;autoRunning=false;void playNext();};
    const detach=()=>{t.audio.removeEventListener('ended',done);t.audio.removeEventListener('error',done);};
    detachAuto=detach;
    t.audio.addEventListener('ended',done,{once:true});t.audio.addEventListener('error',done,{once:true});
    await play(t);
    if(disposed||epoch!==autoEpoch)return;
    if(t.audio.paused){detach();autoRunning=false;void playNext();}
  }
  const incoming=(event:Event)=>{autoplay.push((event as CustomEvent<PaperMedia>).detail);void playNext();};
  const hush=()=>{recording=true;++generation;cancelAuto();pausePaperAudio();autoplay.length=0;};
  const quietEnd=()=>{recording=false;void playNext();};
  const roomChanged=(event:Event)=>{
    const next=(event as CustomEvent<{scope?:string}>).detail?.scope;
    if(!next || next===scope)return;
    scope=next;++generation;cancelAuto();autoplay.length=0;active=undefined;
    for(const [k,t] of tracks)drop(k,t);statuses.clear();paint();
  };
  const preferences=(event:Event)=>{if((event as CustomEvent<{autoplay?:boolean}>).detail?.autoplay===false){++generation;cancelAuto();autoplay.length=0;pausePaperAudio();}};
  addEventListener('helene:voice-incoming',incoming);addEventListener('helene:recording',hush);addEventListener('helene:recording-ended',quietEnd);
  addEventListener('frame-room',roomChanged);addEventListener('helene:voice-preferences',preferences);
  root.addEventListener('click',onClick); root.addEventListener('input',onSeek);
  bar.addEventListener('click',onBar); bar.addEventListener('input',onSettings); bar.addEventListener('change',onSettings);
  const containsAudio = (n: Node) => n instanceof Element && (n.matches('.paper-audio') || !!n.querySelector('.paper-audio'));
  const observer=new MutationObserver(records => { if(records.some(r => [...r.addedNodes,...r.removedNodes].some(containsAudio))) reconcile(); }); observer.observe(root,{childList:true,subtree:true});
  const pollTimer=setInterval(()=>void pollVoice(),2500); void pollVoice();
  reconcile();
  return () => { disposed=true; clearInterval(pollTimer); ++generation;cancelAuto(); observer.disconnect();removeEventListener('frame-room',roomChanged);removeEventListener('helene:voice-preferences',preferences);removeEventListener('helene:voice-incoming',incoming);removeEventListener('helene:recording',hush);removeEventListener('helene:recording-ended',quietEnd);root.removeEventListener('click',onClick); root.removeEventListener('input',onSeek); bar.removeEventListener('click',onBar); bar.removeEventListener('input',onSettings); bar.removeEventListener('change',onSettings); for(const [k,t] of tracks) drop(k,t); active=undefined; bar.remove(); };
}
