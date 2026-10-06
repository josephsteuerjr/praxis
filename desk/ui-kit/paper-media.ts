import { esc } from "./text";
import { fileError, fileSize, paperButton, paperDialog } from "./paper-dialog";

export interface PaperMedia { path: string; kind: string; name: string; size?: number; mime?: string }
export interface MediaClient {
  key(media: PaperMedia): string;
  bytes(media: PaperMedia): Promise<Blob>;
  save(media: PaperMedia, blob: Blob): Promise<void>;
}

export function mediaDescriptor(m: { media_path?: string; media_kind?: string; media_name?: string; media_size?: number; media_mime?: string }): PaperMedia | null {
  const path = String(m.media_path || "").trim(); if (!path) return null;
  const name = m.media_name || path.split(/[\\/]/).pop() || "Файл";
  return { path, kind: String(m.media_kind || "file"), name: name.replace(/^(?:msg-[a-f0-9]{10}-[a-f0-9]{12}-)+/, ""), size: m.media_size, mime: m.media_mime };
}
export function paperMediaHTML(m: PaperMedia) {
  const data = `data-paper-path="${esc(m.path)}" data-paper-kind="${esc(m.kind)}" data-paper-name="${esc(m.name)}"`;
  const size = m.size === undefined ? "" : fileSize(m.size);
  if (m.kind === "image") return `<figure class="paper-media" ${data}><button type="button" class="paper-print" data-paper-open aria-label="Увеличить: ${esc(m.name)}"><span class="paper-image-state" role="status">Изображение</span><img alt="${esc(m.name)}" hidden></button><figcaption><span>${esc(m.name)}</span><button type="button" class="paper-link" data-paper-save>Сохранить…</button></figcaption></figure>`;
  return `<div class="paper-artifact" ${data}><span class="paper-file-icon" aria-hidden="true">▤</span><div><strong>${esc(m.name)}</strong><span class="paper-file-size">${esc(size)}</span><div class="paper-artifact-actions"><button type="button" class="paper-link" data-paper-open>Посмотреть</button><button type="button" class="paper-link" data-paper-save>Сохранить…</button></div></div></div>`;
}

/** Hide only a typed artifact's historical transport annotation, never rewrite its archive. */
export function artifactCaption(text: string, path?: string) {
  if (!path) return text;
  return text.replace(/^\[(?:файл|голос|изображение|картинка)\][^\n]*(?:\n|$)/, "");
}

export function mountPaperMedia(root: HTMLElement, client: MediaClient) {
  type Cached = { promise: Promise<{ blob: Blob; url: string }>; refs: number; timer?: ReturnType<typeof setTimeout> };
  const cache = new Map<string, Cached>();
  function acquire(media: PaperMedia) {
    const key = client.key(media); let entry = cache.get(key);
    if (!entry) {
      const promise = client.bytes(media).then(blob => {
        if (!blob.size) throw new Error("Файл пуст");
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
  const bound = new Map<HTMLElement, { release?: () => void; seq: number; failed?: boolean }>();
  const descriptor = (node: HTMLElement): PaperMedia => ({ path: node.dataset.paperPath!, kind: node.dataset.paperKind!, name: node.dataset.paperName! });
  async function print(node: HTMLElement, retry = false) {
    const state = bound.get(node); if (!state || (state.release && !retry) || (state.failed && !retry)) return;
    state.release?.(); state.failed = false; const seq = ++state.seq;
    const held = acquire(descriptor(node)); state.release = held.release;
    const image = node.querySelector<HTMLImageElement>(".paper-print img")!;
    const words = node.querySelector<HTMLElement>(".paper-image-state")!;
    words.textContent = "Проявляю изображение…"; words.hidden = false; image.hidden = true;
    try {
      const value = await held.promise;
      if (!node.isConnected || state.seq !== seq) return;
      image.src = value.url; await image.decode();
      if (!node.isConnected || state.seq !== seq) return;
      image.hidden = false; words.hidden = true;
      node.dataset.paperLoaded = "true";
    } catch (e) {
      if (!node.isConnected || state.seq !== seq) return;
      image.removeAttribute("src"); image.hidden = true; state.failed = true;
      words.textContent = "Изображение не открылось. Нажми, чтобы повторить.";
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
        const image = node.querySelector<HTMLImageElement>("img")!; image.hidden = true; image.removeAttribute("src");
        const words = node.querySelector<HTMLElement>(".paper-image-state")!; words.hidden = false; words.textContent = st.failed ? "Нажми, чтобы повторить." : "Изображение";
      }
    }
  }, { rootMargin: "400px" });
  function reconcile() {
    root.querySelectorAll<HTMLElement>(".paper-media").forEach(node => {
      if (!bound.has(node)) { bound.set(node, { seq: 0 }); observer.observe(node); }
    });
    for (const [node, state] of bound) if (!root.contains(node)) { ++state.seq; state.release?.(); observer.unobserve(node); bound.delete(node); }
  }
  new MutationObserver(reconcile).observe(root, { childList: true, subtree: true }); reconcile();
  root.addEventListener("click", event => {
    const button = (event.target as Element).closest<HTMLButtonElement>("[data-paper-open], [data-paper-save]");
    const node = button?.closest<HTMLElement>("[data-paper-path]"); if (!button || !node) return;
    event.preventDefault();
    if (button.hasAttribute("data-paper-open") && bound.get(node)?.failed) { void print(node, true); return; }
    void open(descriptor(node), button.hasAttribute("data-paper-save"));
  });
  async function open(media: PaperMedia, saving = false) {
    const ui = paperDialog(saving ? "Сохранение файла" : media.name, true);
    const status = document.createElement("p"); status.className = "paper-file-receipt"; status.setAttribute("role", "status"); status.textContent = "Открываю файл…"; ui.body.append(status);
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
        ui.footer.append(paperButton("−", () => change(1 / 1.25)), scale, paperButton("+", () => change(1.25)), paperButton("Вписать", () => { fit = true; zoom(); }), paperButton("100%", () => { fit = false; factor = 1; zoom(); }));
        viewport.addEventListener("dblclick", () => { fit = !fit; factor = 1; zoom(); });
        viewport.addEventListener("wheel", e => { if (e.ctrlKey) { e.preventDefault(); change(e.deltaY < 0 ? 1.25 : 1 / 1.25); } }, { passive: false });
        const resize = new ResizeObserver(zoom); resize.observe(viewport); ui.dialog.addEventListener("close", () => resize.disconnect(), { once: true }); zoom();
      } else if (/^(text\/|application\/(json|xml))/.test(value.blob.type) || /\.(txt|md|csv|tsv|json|log|xml|yaml|yml|py|js|ts|css|html)$/i.test(media.name)) {
        const preview = document.createElement("pre"); preview.className = "paper-text-preview";
        preview.textContent = await value.blob.slice(0, 512 * 1024).text(); if (closed) return;
        ui.body.prepend(preview); if (value.blob.size > 512 * 1024) status.textContent += " · показано начало файла";
      } else {
        const note = document.createElement("p"); note.className = "paper-lead"; note.textContent = "Сохрани файл, чтобы открыть его в подходящем приложении на своём компьютере."; ui.body.prepend(note);
      }
      const save = paperButton("Сохранить…", () => {
        save.disabled = true;
        void client.save(media, value.blob).catch(e => { if (!closed) status.textContent = "Не удалось сохранить: " + fileError(e); }).finally(() => { save.disabled = false; });
      }, true);
      ui.footer.append(save);
    } catch (e) {
      if (closed) return;
      held.invalidate();
      status.textContent = "Не удалось открыть файл: " + fileError(e);
      ui.footer.append(paperButton("Повторить", () => { ui.dialog.close(); void open(media, saving); }, true));
    }
  }
}
