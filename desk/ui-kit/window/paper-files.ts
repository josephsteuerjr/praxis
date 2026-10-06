import { ApiError, artifactURL, cfg, channelURL, inTauri, shell } from "./api";
import { fileDialog, FILE_BYTES, type FileAccess, type Folder } from "../paper-files";
import { fileError, paperButton, paperDialog } from "../paper-dialog";
import { mountPaperMedia, type PaperMedia } from "../paper-media";
import { artifactBytes, saveName } from "../paper-transfer";

type ReadFile = { name: string; mime: string; data: string; size: number };
export function bytesToFile(value: ReadFile): File {
  const decoded = atob(value.data), bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  if (bytes.length !== value.size || bytes.length > FILE_BYTES) throw new Error("Файл изменился при чтении");
  return new File([bytes], value.name, { type: value.mime });
}
function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader(); r.onerror = () => reject(r.error);
    r.onload = () => resolve(String(r.result).split(",", 2)[1]); r.readAsDataURL(blob);
  });
}
let native: boolean | undefined;
function localChannel() {
  if (!cfg.base) return /^localhost$|^127\.0\.0\.1$|^\[::1\]$/.test(location.hostname);
  try { const u = new URL(cfg.base); return /^localhost$|^127\.0\.0\.1$|^\[::1\]$/.test(u.hostname); } catch { return false; }
}
async function localHTTP<T>(path: string, body?: object): Promise<T> {
  const r = await fetch(channelURL(path), body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  if (!r.ok) throw new ApiError(await r.text(), r.status); return r.json();
}
function access(localAgent: boolean, media?: PaperMedia, blob?: Blob): FileAccess {
  const list = async (path: string): Promise<Folder> => {
    if (inTauri && native !== false) {
      try { const value = await shell<Folder>("local_files_list", { path }); native = true; return value; }
      catch (e) { if (!/not found|unknown.*command|неизвестн.*команд/i.test(String(e))) throw e; native = false; }
    }
    if (!localAgent || !localChannel()) throw new Error("Для файлов этого компьютера нужна обновлённая оболочка Hélène. Соединение с сервером сохранено.");
    return localHTTP<Folder>("/api/local-files?path=" + encodeURIComponent(path));
  };
  return { list,
    read: async path => bytesToFile(native ? await shell<ReadFile>("local_files_read", { path }) : await localHTTP<ReadFile>("/api/local-files", { action: "read", path })),
    save: async (folder, name, overwrite) => {
      if (!media || !blob) throw new Error("Нет файла для сохранения");
      return native ? shell("local_files_save", { folder, name, overwrite, data: await toBase64(blob) })
        : localHTTP("/api/local-files", { action: "save", rel: media.path, folder, name, overwrite, data: await toBase64(blob) });
    },
  };
}
export async function pickPaperFiles(localAgent: boolean, count: number, bytes: number): Promise<File[]> {
  const result = await fileDialog(access(localAgent), { count, bytes }); return Array.isArray(result) ? result : [];
}
async function save(localAgent: boolean, media: PaperMedia, blob: Blob) {
  try {
    const result = await fileDialog(access(localAgent, media, blob), { save: saveName(media) });
    if (!result || Array.isArray(result)) return;
    const ui = paperDialog("Файл сохранён");
    const text = document.createElement("p"); text.className = "paper-file-receipt"; text.textContent = result.path;
    ui.body.append(text); ui.footer.append(paperButton("Готово", () => ui.dialog.close(), true));
  } catch (e) {
    const ui = paperDialog("Не удалось сохранить"); const text = document.createElement("p"); text.textContent = fileError(e); ui.body.append(text);
  }
}
export function mountWindowMedia(root: HTMLElement, localAgent: boolean) {
  mountPaperMedia(root, { key: m => artifactURL(m.path, true), bytes: m => artifactBytes(artifactURL(m.path, true)), save: (m,b) => save(localAgent,m,b) });
}
