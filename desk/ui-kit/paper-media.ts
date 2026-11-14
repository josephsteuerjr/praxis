import { t as uiText } from "../lang/lang";
import { esc } from "./text";
import { audioHTML, mountPaperAudio } from './paper-audio';
import { fileError, fileSize, paperButton, paperDialog } from "./paper-dialog";

export interface PaperMedia { path: string; kind: string; name: string; size?: number; mime?: string; originalPath?: string }
interface Animation { destroy(): void; pause(): void; play(): void }
interface Player { loadAnimation(config: {container: HTMLElement; renderer: 'svg'; loop: boolean; autoplay: boolean; animationData: unknown}): Animation }
let animationPlayer: (() => Promise<Player>) | null = null;
export function setAnimationPlayer(load: () => Promise<Player>) { animationPlayer = load; }
export interface MediaClient {
  key(media: PaperMedia): string;
  bytes(media: PaperMedia): Promise<Blob>;
  save(media: PaperMedia, blob: Blob): Promise<void>;
  voice?(media: PaperMedia, retry?: boolean): Promise<{status: string; transcript?: string; error?: string; retry_requested?: boolean}>;
}

export function mediaDescriptor(m: { media_path?: string; media_kind?: string; media_name?: string; media_size?: number; media_mime?: string; media_original_path?: string }): PaperMedia | null {
  const path = String(m.media_path || "").trim(); if (!path) return null;
  const name = m.media_name || path.split(/[\\/]/).pop() || uiText("ui.ui.kit.paper.media.f242456b22");
  const kind = ['audio','voice','voice_note'].includes(m.media_kind || '') || (m.media_kind !== 'video' && (m.media_mime?.startsWith('audio/') || ((!m.media_mime || m.media_mime === 'application/octet-stream') && /\.(?:wav|mp3|ogg|oga|opus|m4a|aac|flac)$|^voice-.*\.webm$/i.test(name)))) ? 'audio' : String(m.media_kind || 'file');
  return { path, kind, name: name.replace(/^(?:msg-[a-f0-9]{10}-[a-f0-9]{12}-)+/, ""), size: m.media_size, mime: m.media_mime, originalPath:m.media_original_path };
}
export function paperMediaHTML(m: PaperMedia) {
  const data = `data-paper-path="${esc(m.path)}" data-paper-kind="${esc(m.kind)}" data-paper-name="${esc(m.name)}" data-paper-mime="${esc(m.mime || '')}"${m.originalPath?` data-paper-original="${esc(m.originalPath)}"`:''}`;
  const size = m.size === undefined ? "" : fileSize(m.size);
  if (m.kind === 'audio') return audioHTML(m, data);
  if (m.kind === "image") return uiText("ui.ui.kit.paper.media.5f7bd4f5a4", {p0: String(data), p1: String(esc(m.name)), p2: String(esc(m.name)), p3: String(esc(m.name))});
  if (['video','animation','sticker_tgs','audio'].includes(m.kind)) {
    const visual = m.kind==='sticker_tgs' ? '<div class="paper-sticker" hidden></div>' : m.kind==='audio' ? '<audio preload="metadata" hidden></audio>' : '<video playsinline preload="metadata" hidden></video>';
    return uiText("ui.ui.kit.paper.media.6851868fb1", {p0: String(m.kind==='audio'?'paper-audio':''), p1: String(data), p2: String(visual), p3: String(esc(m.name))});
  }
  return uiText("ui.ui.kit.paper.media.ea54e33f28", {p0: String(data), p1: String(esc(m.name)), p2: String(esc(size))});
}

/** Hide only a typed artifact's historical transport annotation, never rewrite its archive. */
export function artifactCaption(text: string, path?: string) {
  if (!path) return text;
  return text.replace(/^\[(?:файл(?::[^\]]*)?|голос(?:овое)?|изображение|картинка|фото|стикер[^\]]*|анимация|видео)\][^\n]*(?:\n|$)/, "");
}

export function mountPaperMedia(root: HTMLElement, client: MediaClient) {
  const disposeAudio = mountPaperAudio(root, client);
  type Cached = { promise: Promise<{ blob: Blob; url: string }>; refs: number; timer?: ReturnType<typeof setTimeout> };
  const cache = new Map<string, Cached>();
  function acquire(media: PaperMedia) {
    const key = client.key(media); let entry = cache.get(key);
    if (!entry) {
      const promise = client.bytes(media).then(blob => {
        if (!blob.size) throw new Error(uiText("ui.ui.kit.paper.media.3362268c24"));
        return { blob, url: URL.createObjectURL(blob) };
      });
      entry = { promise, refs: 0 }; cache.set(key, entry);
      promise.catch(() => { if (cache.get(key) === entry) cache.delete(key); });
    }
    const held = entry; clearTimeout(held.timer); held.refs++;
    let released = false;
    return { promise: held.promise, invalidate: () => { if (cache.get(key) === held) cache.delete(key); }, release: () => {
      if (released) return; released = true; held.refs--;
      if (!held.refs) held.timer = setTimeout(() => {
        if (held.refs) return;
        if (cache.get(key) === held) cache.delete(key);
        void held.promise.then(v => URL.revokeObjectURL(v.url), () => {});
      }, 15000);
    } };
  }
  const bound = new Map<HTMLElement, { release?: () => void; seq: number; failed?: boolean; animation?: Animation; paused?: boolean }>();
  const descriptor = (node: HTMLElement): PaperMedia => ({ path: node.dataset.paperPath!, kind: node.dataset.paperKind!, name: node.dataset.paperName!,originalPath:node.dataset.paperOriginal });
  async function print(node: HTMLElement, retry = false) {
    const state = bound.get(node); if (!state || (state.release && !retry) || (state.failed && !retry)) return;
    state.release?.(); state.failed = false; const seq = ++state.seq;
    const held = acquire(descriptor(node)); state.release = held.release;
    const image = node.querySelector<HTMLImageElement | HTMLMediaElement | HTMLDivElement>(".paper-print img, .paper-print video, .paper-print audio, .paper-sticker")!;
    const words = node.querySelector<HTMLElement>(".paper-image-state")!;
    words.textContent = uiText("ui.ui.kit.paper.media.bbc5be4deb"); words.hidden = false; image.hidden = true;
    try {
      const value = await held.promise;
      if (!node.isConnected || state.seq !== seq) return;
      if (image instanceof HTMLImageElement) { image.src = value.url; await image.decode(); }
      else if (image instanceof HTMLMediaElement) {
        image.src=value.url; image.muted=node.dataset.paperKind==='animation'; image.loop=node.dataset.paperKind==='animation';
        await new Promise<void>((resolve,reject)=>{
          const done=(error?: Error)=>{clearTimeout(timer);image.removeEventListener('loadeddata',ready);image.removeEventListener('error',failed);error?reject(error):resolve();};
          const ready=()=>done(),failed=()=>done(new Error(uiText("ui.ui.kit.paper.media.8fcd33495a")));
          const timer=setTimeout(()=>done(new Error(uiText("ui.ui.kit.paper.media.40f63e5b7a"))),15000);
          image.addEventListener('loadeddata',ready,{once:true});image.addEventListener('error',failed,{once:true});image.load();
        });
        if (image.loop) await image.play().catch(() => {});
        const button=node.querySelector('[data-paper-play]'); if(button) button.textContent=image.paused?uiText("ui.ui.kit.paper.media.bdd37eb217"):uiText("ui.ui.kit.paper.media.65530fd463");
      } else {
        if (!animationPlayer) throw new Error(uiText("ui.ui.kit.paper.media.da7b5751c9"));
        const data=JSON.parse(await value.blob.text());
        const safe=(value: unknown): boolean => {
          if (!value || typeof value!=='object') return true;
          return Object.entries(value).every(([key,val]) => !((key==='x'||key==='fPath')&&typeof val==='string') && safe(val));
        };
        if (!safe(data) || data.assets?.some((a:{u?:string;p?:string})=>a.u||a.p)) throw new Error(uiText("ui.ui.kit.paper.media.eededf047d"));
        const player=await animationPlayer();
        if (!node.isConnected || state.seq!==seq) return;
        state.animation?.destroy(); image.replaceChildren();
        state.animation=player.loadAnimation({container:image,renderer:'svg',loop:true,autoplay:true,animationData:data});state.paused=false;
        const button=node.querySelector('[data-paper-play]'); if(button) button.textContent=uiText("ui.ui.kit.paper.media.65530fd463");
      }
      if (!node.isConnected || state.seq !== seq) return;
      image.hidden = false; words.hidden = true;
      node.dataset.paperLoaded = "true";
    } catch (e) {
      if (!node.isConnected || state.seq !== seq) return;
      image.removeAttribute("src"); image.hidden = true; state.failed = true;
      words.textContent = uiText("ui.ui.kit.paper.media.25d411ea3f");
      words.title = fileError(e); delete node.dataset.paperLoaded;
      held.invalidate(); held.release(); state.release = undefined;
    }
  }
  const observer = new IntersectionObserver(entries => {
    for (const e of entries) {
      const node = e.target as HTMLElement, st = bound.get(node); if (!st) continue;
      if (e.isIntersecting) void print(node);
      else if (st.release) {
        ++st.seq; st.release(); st.release = undefined;
        stateCleanup(node,st);
        const words = node.querySelector<HTMLElement>(".paper-image-state")!; words.hidden = false; words.textContent = st.failed ? uiText("ui.ui.kit.paper.media.8f5423a894") : uiText("ui.ui.kit.paper.media.e5f534734c");
      }
    }
  }, { rootMargin: "400px" });
  function stateCleanup(node: HTMLElement, state: {animation?: Animation}) {
    state.animation?.destroy(); state.animation=undefined;
    const image=node.querySelector<HTMLImageElement | HTMLMediaElement | HTMLDivElement>('img,video,audio,.paper-sticker');
    if (image instanceof HTMLMediaElement) image.pause();
    if (image) { image.hidden=true; image.removeAttribute('src'); }
  }
  function reconcile() {
    root.querySelectorAll<HTMLElement>(".paper-media:not(.paper-audio)").forEach(node => {
      if (!bound.has(node)) { bound.set(node, { seq: 0 }); observer.observe(node); }
    });
    for (const [node, state] of bound) if (!root.contains(node)) { ++state.seq; state.release?.(); stateCleanup(node,state); observer.unobserve(node); bound.delete(node); }
  }
  const mutations=new MutationObserver(reconcile);
  mutations.observe(root, { childList: true, subtree: true }); reconcile();
  const onClick = (event: Event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>("[data-paper-open], [data-paper-save], [data-paper-play]");
    const node = button?.closest<HTMLElement>("[data-paper-path]"); if (!button || !node) return;
    event.preventDefault();
    if (button.hasAttribute('data-paper-play')) {
      const state=bound.get(node),video=node.querySelector<HTMLMediaElement>('video,audio');
      if(video) { if(video.paused) { void video.play().then(()=>button.textContent=uiText("ui.ui.kit.paper.media.65530fd463")).catch(()=>button.textContent=uiText("ui.ui.kit.paper.media.5189135a61")); } else { video.pause(); button.textContent=uiText("ui.ui.kit.paper.media.bdd37eb217"); } }
      else if(state?.animation) { state.paused=!state.paused; if(state.paused) { state.animation.pause(); button.textContent=uiText("ui.ui.kit.paper.media.bdd37eb217"); } else { state.animation.play(); button.textContent=uiText("ui.ui.kit.paper.media.65530fd463"); } }
      return;
    }
    if (button.hasAttribute("data-paper-open") && bound.get(node)?.failed) { void print(node, true); return; }
    void open(descriptor(node), button.hasAttribute("data-paper-save"));
  };
  root.addEventListener('click', onClick);
  async function open(media: PaperMedia, saving = false) {
    if(saving&&media.originalPath) media=original(media);
    const ui = paperDialog(saving ? uiText("ui.ui.kit.paper.media.69024748c0") : media.name, true);
    const status = document.createElement("p"); status.className = "paper-file-receipt"; status.setAttribute("role", "status"); status.textContent = uiText("ui.ui.kit.paper.media.c9b0e38cb8"); ui.body.append(status);
    const held = acquire(media); let closed = false;
    ui.dialog.addEventListener("close", () => { closed = true; held.release(); }, { once: true });
    try {
      const value = await held.promise; if (closed) return;
      if (saving) { ui.dialog.close(); await client.save(media, value.blob); return; }
      status.textContent = fileSize(value.blob.size);
      if (media.kind === "image") {
        const viewport = document.createElement("div"); viewport.className = "paper-viewer";
        const image = document.createElement("img"); image.src = value.url; image.alt = media.name; viewport.append(image); ui.body.prepend(viewport);
        await image.decode(); if (closed) return;
        let factor = 1, fit = true;
        const scale = document.createElement("span"); scale.className = "paper-zoom-label"; scale.setAttribute("role", "status");
        function zoom() {
          const oldWidth = image.getBoundingClientRect().width || 1, oldHeight = image.getBoundingClientRect().height || 1;
          const centerX = (viewport.scrollLeft + viewport.clientWidth / 2) / oldWidth;
          const centerY = (viewport.scrollTop + viewport.clientHeight / 2) / oldHeight;
          const fitted = Math.min(1, (viewport.clientWidth - 24) / image.naturalWidth, (viewport.clientHeight - 24) / image.naturalHeight);
          const k = fit ? fitted : factor;
          image.style.width = image.naturalWidth * k + "px"; image.style.height = image.naturalHeight * k + "px";
          image.style.marginTop = Math.max(0, (viewport.clientHeight - 24 - image.naturalHeight * k) / 2) + "px";
          viewport.scrollLeft = fit ? 0 : centerX * image.naturalWidth * k - viewport.clientWidth / 2;
          viewport.scrollTop = fit ? 0 : centerY * image.naturalHeight * k - viewport.clientHeight / 2;
          scale.textContent = Math.round(k * 100) + "%";
        }
        const change = (dir: number) => { factor = Math.min(4, Math.max(0.1, (fit ? parseInt(scale.textContent!) / 100 : factor) * dir)); fit = false; zoom(); };
        ui.footer.append(paperButton("−", () => change(1 / 1.25)), scale, paperButton("+", () => change(1.25)), paperButton(uiText("ui.ui.kit.paper.media.3681a2658a"), () => { fit = true; zoom(); }), paperButton("100%", () => { fit = false; factor = 1; zoom(); }));
        viewport.addEventListener("dblclick", () => { fit = !fit; factor = 1; zoom(); });
        viewport.addEventListener("wheel", e => { if (e.ctrlKey) { e.preventDefault(); change(e.deltaY < 0 ? 1.25 : 1 / 1.25); } }, { passive: false });
        const resize = new ResizeObserver(zoom); resize.observe(viewport); ui.dialog.addEventListener("close", () => resize.disconnect(), { once: true }); zoom();
      } else if (['video','animation','audio'].includes(media.kind)) {
        const video=document.createElement(media.kind==='audio'?'audio':'video'); video.className='paper-video'; video.src=value.url; video.loop=media.kind==='animation';
        if(video instanceof HTMLVideoElement) video.playsInline=true;
        ui.body.prepend(video);
        const controls=document.createElement('div'); controls.className='paper-playback';
        const play=paperButton(uiText("ui.ui.kit.paper.media.bdd37eb217"),()=>{ if(video.paused) void video.play().catch(e=>status.textContent=fileError(e)); else video.pause(); });
        const seek=document.createElement('input'); seek.type='range'; seek.min='0'; seek.max='100'; seek.value='0'; seek.setAttribute('aria-label',uiText("ui.ui.kit.paper.media.483ce8aaa0"));
        seek.addEventListener('input',()=>{ if(Number.isFinite(video.duration)) video.currentTime=video.duration*Number(seek.value)/100; });
        video.addEventListener('timeupdate',()=>{ seek.value=String(video.duration?100*video.currentTime/video.duration:0); });
        video.addEventListener('play',()=>play.textContent=uiText("ui.ui.kit.paper.media.65530fd463")); video.addEventListener('pause',()=>play.textContent=uiText("ui.ui.kit.paper.media.bdd37eb217"));
        video.addEventListener('error',()=>status.textContent=uiText("ui.ui.kit.paper.media.8f3fa332a7"));
        const sound=paperButton(uiText("ui.ui.kit.paper.media.7b46add449"),()=>{ video.muted=!video.muted; sound.textContent=video.muted?uiText("ui.ui.kit.paper.media.aa1a4d976e"):uiText("ui.ui.kit.paper.media.7b46add449"); });
        controls.append(play,seek,sound); ui.body.insertBefore(controls,status);
        ui.dialog.addEventListener('close',()=>video.pause(),{once:true});
      } else if (media.kind==='sticker_tgs') {
        const figure=document.createElement('div'); figure.innerHTML=paperMediaHTML(media); figure.querySelector('[data-paper-open]')?.remove(); figure.querySelector('[data-paper-save]')?.remove(); ui.body.prepend(figure);
        const dispose=mountPaperMedia(figure,client); ui.dialog.addEventListener('close',dispose,{once:true});
      } else if (/^(text\/|application\/(json|xml))/.test(value.blob.type) || /\.(txt|md|csv|tsv|json|log|xml|yaml|yml|py|js|ts|css|html)$/i.test(media.name)) {
        const preview = document.createElement("pre"); preview.className = "paper-text-preview";
        preview.textContent = await value.blob.slice(0, 512 * 1024).text(); if (closed) return;
        ui.body.prepend(preview); if (value.blob.size > 512 * 1024) status.textContent += uiText("ui.ui.kit.paper.media.46b81e2b59");
      } else {
        const note = document.createElement("p"); note.className = "paper-lead"; note.textContent = uiText("ui.ui.kit.paper.media.9c01c392d4"); ui.body.prepend(note);
      }
      const save = paperButton(uiText("ui.ui.kit.paper.media.ee96e239c6"), () => {
        save.disabled = true;
        const download=async()=>{
          if(!media.originalPath) return client.save(media,value.blob);
          const target=original(media),raw=acquire(target);
          try { await client.save(target,(await raw.promise).blob); } finally { raw.release(); }
        };
        void download().catch(e => { if (!closed) status.textContent = uiText("ui.ui.kit.paper.media.62f229c5b3") + fileError(e); }).finally(() => { save.disabled = false; });
      }, true);
      ui.footer.append(save);
    } catch (e) {
      if (closed) return;
      held.invalidate();
      status.textContent = uiText("ui.ui.kit.paper.media.5509570b3e") + fileError(e);
      ui.footer.append(paperButton(uiText("ui.ui.kit.paper.media.5189135a61"), () => { ui.dialog.close(); void open(media, saving); }, true));
    }
  }
  function original(media:PaperMedia):PaperMedia {
    const extension=media.originalPath?.split('.').pop()||'';
    return {...media,path:media.originalPath!,originalPath:undefined,name:media.name.toLowerCase().endsWith('.'+extension)?media.name:media.name+'.'+extension};
  }
  return () => { disposeAudio(); root.removeEventListener('click', onClick); mutations.disconnect(); observer.disconnect(); for(const [node,state] of bound) { ++state.seq; state.release?.(); stateCleanup(node,state); } bound.clear(); };
}
