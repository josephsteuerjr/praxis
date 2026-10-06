import { FILE_BYTES } from "./paper-files";
import { fileError, paperButton, paperDialog } from "./paper-dialog";
import type { PaperMedia } from "./paper-media";

export async function artifactBytes(url: string): Promise<Blob> {
  const r = await fetch(url, { signal: AbortSignal.timeout(60000) });
  if (!r.ok) throw new Error(r.status === 404 ? "Файл больше не найден в архиве" : r.status === 403 ? "Нет доступа к этому файлу" : "Канал не отдал файл (" + r.status + ")");
  if (Number(r.headers.get("content-length")) > FILE_BYTES) throw new Error("Файл больше 64 МБ");
  const reader = r.body?.getReader(); if (!reader) throw new Error("Канал не отдал содержимое файла");
  const chunks: BlobPart[] = []; let size = 0;
  try { while (true) { const { value, done } = await reader.read(); if (done) break; size += value.byteLength; if (size > FILE_BYTES) { await reader.cancel(); throw new Error("Файл больше 64 МБ"); } chunks.push(value); } }
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
  const ui = paperDialog("Сохранить на устройство");
  const label = document.createElement("label"); label.className = "paper-save-name"; label.textContent = "Имя файла";
  const name = document.createElement("input"); name.value = saveName(media); name.setAttribute("aria-label", "Имя файла"); label.append(name);
  const status = document.createElement("p"); status.className = "paper-file-receipt"; status.setAttribute("role", "status");
  status.textContent = "Место сохранения выбирает браузер или система устройства.";
  ui.body.append(label,status);
  ui.footer.append(paperButton("Отмена", () => ui.dialog.close()));
  const action = paperButton("Сохранить", () => {
    if (!name.value.trim() || /[\\/\x00-\x1f]/.test(name.value)) { status.textContent = "Введи имя файла без пути."; name.focus(); return; }
    try {
      const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = name.value.trim(); document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = "Файл передан браузеру. Проверь загрузки устройства.";
      name.disabled = true; action.disabled = true; ui.footer.append(paperButton("Готово", () => ui.dialog.close(), true));
    } catch (e) { status.textContent = "Сохранение не началось: " + fileError(e); }
  }, true);
  ui.footer.append(action); name.focus(); name.select();
}
