import { t as uiText, UiError } from "../lang/lang";
import { FILE_BYTES } from "./paper-files";
import { fileError, paperButton, paperDialog } from "./paper-dialog";
import type { PaperMedia } from "./paper-media";

export async function artifactBytes(url: string): Promise<Blob> {
  const r = await fetch(url, { signal: AbortSignal.timeout(60000) });
  if (!r.ok) throw new Error(r.status === 404 ? uiText("ui.ui.kit.paper.transfer.b492849f9d") : r.status === 403 ? uiText("ui.ui.kit.paper.transfer.c78374de0b") : uiText("ui.ui.kit.paper.transfer.1377c636b6") + r.status + ")");
  if (Number(r.headers.get("content-length")) > FILE_BYTES) throw new UiError(uiText("ui.ui.kit.paper.transfer.9aaebecf86"));
  const reader = r.body?.getReader(); if (!reader) throw new UiError(uiText("ui.ui.kit.paper.transfer.d43b644987"));
  const chunks: BlobPart[] = []; let size = 0;
  try { while (true) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength; if (size > FILE_BYTES) { await reader.cancel(); throw new UiError(uiText("ui.ui.kit.paper.transfer.9aaebecf86")); } chunks.push(value); } }
  finally { reader.releaseLock(); }
  return new Blob(chunks, { type: r.headers.get("content-type") || "application/octet-stream" });
}
export function saveName(media: PaperMedia) {
  if (/^generated-image\./i.test(media.name)) {
    const stamp = media.path.match(/[a-f0-9]{12,}/i)?.[0].slice(0, 8);
    if (stamp) return media.name.replace(/^(generated-image)(\.)/i, "$1-" + stamp + "$2");
  }
  return media.name;
}
/** A web page has no directory access. Ask for the name before a browser download,
 * and describe the hand-off honestly instead of claiming a path we cannot observe. */
export async function browserSave(media: PaperMedia, blob: Blob) {
  const picker=(window as Window & {showSaveFilePicker?: (options:{suggestedName:string})=>Promise<{createWritable():Promise<{write(blob:Blob):Promise<void>;close():Promise<void>;abort():Promise<void>}>}>}).showSaveFilePicker;
  const ui = paperDialog(uiText("ui.ui.kit.paper.transfer.33799fd003"));
  const label = document.createElement("label"); label.className = "paper-save-name"; label.textContent = uiText("ui.ui.kit.paper.transfer.186081f3ae");
  const name = document.createElement("input"); name.value = saveName(media); name.setAttribute("aria-label", uiText("ui.ui.kit.paper.transfer.186081f3ae")); label.append(name);
  const status = document.createElement("p"); status.className = "paper-file-receipt"; status.setAttribute("role", "status");
  status.textContent = picker ? uiText("ui.ui.kit.paper.transfer.f24dff44da") : uiText("ui.ui.kit.paper.transfer.dbeb6e8e9d");
  ui.body.append(label,status);
  ui.footer.append(paperButton(uiText("ui.ui.kit.paper.transfer.8fbe9b75cb"), () => ui.dialog.close()));
  const action = paperButton(uiText("ui.ui.kit.paper.transfer.b4d30cae52"), async () => {
    if (!name.value.trim() || /[\\/\x00-\x1f]/.test(name.value)) { status.textContent = uiText("ui.ui.kit.paper.transfer.a98474b3aa"); name.focus(); return; }
    try {
      if(picker) {
        const handle=await picker.call(window,{suggestedName:name.value.trim()});
        const output=await handle.createWritable();
        try { await output.write(blob); await output.close(); }
        catch(e) { await output.abort();throw e; }
        status.textContent=uiText("ui.ui.kit.paper.transfer.44d7ad534b");
        name.disabled=true;action.disabled=true;ui.footer.append(paperButton(uiText("ui.ui.kit.paper.transfer.ef05d57959"),()=>ui.dialog.close(),true));
        return;
      }
      const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = name.value.trim(); document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = uiText("ui.ui.kit.paper.transfer.b4e3b14e33");
      name.disabled = true; action.disabled = true; ui.footer.append(paperButton(uiText("ui.ui.kit.paper.transfer.ef05d57959"), () => ui.dialog.close(), true));
    } catch (e) { status.textContent = uiText("ui.ui.kit.paper.transfer.7ab5203b0b") + fileError(e); }
  }, true);
  ui.footer.append(action); name.focus(); name.select();
}
