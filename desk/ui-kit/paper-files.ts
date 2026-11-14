import { t as uiText, UiError } from "../lang/lang";
import { fileError, fileSize, paperButton, paperDialog } from "./paper-dialog";

export interface FileEntry { name: string; path: string; folder: boolean; size: number; mtime?: number }
export interface Folder { path: string; parent: string; locations: { name: string; path: string }[]; entries: FileEntry[]; total: number }
export interface FileAccess {
  list(path: string): Promise<Folder>;
  read(path: string): Promise<File>;
  save(folder: string, name: string, overwrite: boolean): Promise<{ path: string; bytes: number }>;
}
export const FILE_BYTES = 64 * 1024 * 1024;
export const FILE_TOTAL = 128 * 1024 * 1024;

/** Browse only the viewer's computer. Server directories never stand in for local places. */
export function fileDialog(access: FileAccess, options: { save?: string; count?: number; bytes?: number } = {}): Promise<File[] | { path: string; bytes: number } | null> {
  return new Promise((resolve) => {
    const ui = paperDialog(options.save ? uiText("ui.ui.kit.paper.files.5b41e3f6e4") : uiText("ui.ui.kit.paper.files.8bec1fdea7"), true);
    let ended = false, busy = false, saving = false, listing = 0, current: Folder | null = null;
    const selected = new Map<string, FileEntry>();
    let firstFolder = true;
    const history: string[] = [];
    const lead = document.createElement("p"); lead.className = "paper-lead";
    lead.textContent = options.save ? uiText("ui.ui.kit.paper.files.dafee5ea17") : uiText("ui.ui.kit.paper.files.a3d19fb85b");
    const toolbar = document.createElement("div"); toolbar.className = "paper-file-toolbar";
    const back = paperButton(uiText("ui.ui.kit.paper.files.1a9fb1f3cf"), () => { const path = history.pop(); if (path !== undefined) void load(path, false); }); back.disabled = true;
    const up = paperButton(uiText("ui.ui.kit.paper.files.357ccc3e75"), () => { if (current?.parent) void load(current.parent); }); up.disabled = true;
    const path = document.createElement("input"); path.type = "text"; path.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.f538dd191e"));
    path.placeholder = uiText("ui.ui.kit.paper.files.f538dd191e");
    const go = paperButton(uiText("ui.ui.kit.paper.files.91f7bab064"), () => void load(path.value));
    path.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); void load(path.value); } });
    toolbar.append(back, up, path, go);
    const browser = document.createElement("div"); browser.className = "paper-file-browser";
    const places = document.createElement("nav"); places.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.9c7c106bac"));
    const center = document.createElement("div"); center.className = "paper-file-center";
    const search = document.createElement("input"); search.type = "search"; search.placeholder = uiText("ui.ui.kit.paper.files.95ca93d9ed"); search.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.95ca93d9ed"));
    const rows = document.createElement("div"); rows.className = "paper-file-rows"; rows.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.62521e09a4"));
    const hiddenLabel = document.createElement("label"); hiddenLabel.className = "paper-hidden-files";
    const hiddenFiles = document.createElement("input"); hiddenFiles.type = "checkbox";
    hiddenLabel.append(hiddenFiles, uiText("ui.ui.kit.paper.files.e0536c5fbd"));
    const info = document.createElement("p"); info.className = "paper-file-info";
    center.append(search, hiddenLabel, rows, info); browser.append(places, center);
    const name = document.createElement("input"); name.type = "text"; name.value = options.save || ""; name.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.186081f3ae"));
    const nameLabel = document.createElement("label"); nameLabel.className = "paper-save-name"; nameLabel.append(uiText("ui.ui.kit.paper.files.186081f3ae"), name);
    nameLabel.hidden = !options.save;
    const receipt = document.createElement("p"); receipt.className = "paper-file-receipt"; receipt.setAttribute("role", "status");
    const confirm = document.createElement("div"); confirm.className = "paper-replace"; confirm.hidden = true;
    const selection = document.createElement("div"); selection.className = "paper-selection";
    selection.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.0bf96d8181")); selection.hidden = true;
    const confirmText = document.createElement("p");
    const replace = paperButton(uiText("ui.ui.kit.paper.files.190e2d85be"), () => void save(true));
    const keep = paperButton(uiText("ui.ui.kit.paper.files.e2210052fa"), () => { confirm.hidden = true; name.focus(); name.select(); });
    confirm.append(confirmText, keep, replace);
    const cancel = paperButton(uiText("ui.ui.kit.paper.files.8fbe9b75cb"), () => ui.dialog.close());
    const accept = paperButton(options.save ? uiText("ui.ui.kit.paper.files.b4d30cae52") : uiText("ui.ui.kit.paper.files.14a0b06d8e"), () => void (options.save ? save(false) : pick()), true);
    ui.body.append(lead, toolbar, browser, selection, nameLabel, receipt, confirm); ui.footer.append(cancel, accept);
    ui.dialog.addEventListener("close", () => { ended = true; ++listing; resolve(null); }, { once: true });
    function finish(value: File[] | { path: string; bytes: number }) { resolve(value); ui.dialog.close(); }
    function working(on: boolean, text = "") {
      busy = on; accept.disabled = on || !current || (!options.save && selected.size === 0);
      replace.disabled = on; keep.disabled = on; name.disabled = on; search.disabled = on; path.disabled = on; go.disabled = on;
      back.disabled = on || !history.length; up.disabled = on || !current?.parent;
      browser.inert = on; if (text) receipt.textContent = text;
      // During the atomic save, closing would lose its receipt and invite a duplicate save.
      cancel.disabled = saving; ui.close.disabled = cancel.disabled;
    }
    ui.dialog.addEventListener("cancel", e => { if (saving) e.preventDefault(); });
    function selectionInfo() {
      const bytes = [...selected.values()].reduce((sum, f) => sum + f.size, 0);
      receipt.textContent = selected.size ? uiText("ui.ui.kit.paper.files.c39d0e38cc", {p0: String(selected.size), p1: String(fileSize(bytes))}) : "";
      selection.replaceChildren(); selection.hidden = !selected.size;
      for (const f of selected.values()) {
        const remove = paperButton(f.name + " ×", () => { selected.delete(f.path); selectionInfo(); paint(); });
        remove.setAttribute("aria-label", uiText("ui.ui.kit.paper.files.57a7fd2992") + f.name); selection.append(remove);
      }
      accept.textContent = options.save ? uiText("ui.ui.kit.paper.files.b4d30cae52") : selected.size ? uiText("ui.ui.kit.paper.files.d0f6deac0b", {p0: String(selected.size)}) : uiText("ui.ui.kit.paper.files.14a0b06d8e");
      working(false);
    }
    function paint() {
      if (!current) return;
      const filtered = current.entries.filter(f => (hiddenFiles.checked || !f.name.startsWith(".")) && f.name.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()));
      rows.replaceChildren();
      for (const f of filtered.slice(0, 500)) {
        const row = paperButton("", () => {
          if (busy) return;
          confirm.hidden = true;
          if (f.folder) { void load(f.path); return; }
          if (options.save) { name.value = f.name; name.focus(); name.select(); return; }
          if (selected.has(f.path)) selected.delete(f.path);
          else {
            const max = options.count ?? 16, budget = options.bytes ?? FILE_TOTAL;
            if (f.size > FILE_BYTES) { receipt.textContent = uiText("ui.ui.kit.paper.files.7dbcf1cef7", {p0: String(f.name)}); return; }
            if (selected.size >= max) { receipt.textContent = uiText("ui.ui.kit.paper.files.5576553c0a", {p0: String(max)}); return; }
            if ([...selected.values()].reduce((s, a) => s + a.size, f.size) > budget) { receipt.textContent = uiText("ui.ui.kit.paper.files.9d4a2b833f"); return; }
            selected.set(f.path, f);
          }
          selectionInfo(); paint();
          rows.querySelector<HTMLButtonElement>(`[data-row="${current!.entries.indexOf(f)}"]`)?.focus({ preventScroll: true });
        });
        row.className += " paper-file-row"; row.dataset.row = String(current.entries.indexOf(f));
        const icon = document.createElement("span"); icon.className = "paper-file-icon"; icon.textContent = f.folder ? "▱" : selected.has(f.path) ? "✓" : "▤"; icon.setAttribute("aria-hidden", "true");
        const label = document.createElement("span"); label.className = "paper-file-label"; label.textContent = f.name;
        const detail = document.createElement("span"); detail.className = "paper-file-size"; detail.textContent = f.folder ? uiText("ui.ui.kit.paper.files.73a0f69d54") : fileSize(f.size);
        if (!f.folder && !options.save) row.setAttribute("aria-pressed", String(selected.has(f.path)));
        row.append(icon, label, detail); rows.append(row);
      }
      info.textContent = !filtered.length ? (search.value ? uiText("ui.ui.kit.paper.files.866109a3cd") : uiText("ui.ui.kit.paper.files.e428423963"))
        : filtered.length > 500 ? uiText("ui.ui.kit.paper.files.226ebeafb4", {p0: String(filtered.length)}) : uiText("ui.ui.kit.paper.files.d61d1c844a", {p0: String(filtered.length)});
    }
    search.addEventListener("input", paint);
    hiddenFiles.addEventListener("change", paint);
    rows.addEventListener("keydown", e => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const buttons = [...rows.querySelectorAll<HTMLButtonElement>(".paper-file-row")];
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement); if (at < 0) return;
      e.preventDefault();
      const next = e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : Math.min(buttons.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)));
      buttons[next]?.focus();
    });
    name.addEventListener("input", () => { confirm.hidden = true; receipt.textContent = ""; });
    name.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); void save(false); } });
    async function load(said: string, remember = true): Promise<boolean> {
      if (ended || busy) return false;
      const seq = ++listing; working(true, uiText("ui.ui.kit.paper.files.a32cca8704")); confirm.hidden = true;
      try {
        const value = await access.list(said);
        if (ended || seq !== listing) return false;
        if (remember && current && current.path !== value.path) history.push(current.path);
        current = value; path.value = value.path; search.value = ""; places.replaceChildren();
        for (const p of value.locations) {
          const b = paperButton(p.name, () => void load(p.path)); b.setAttribute("aria-current", String(p.path === value.path)); places.append(b);
        }
        paint(); selectionInfo();
        if (firstFolder && (document.activeElement === ui.close || document.activeElement === ui.dialog)) {
          if (options.save) { name.focus(); name.setSelectionRange(0, Math.max(0, name.value.lastIndexOf(".")) || name.value.length); }
          else search.focus();
        }
        firstFolder = false;
        return true;
      } catch (e) {
        if (ended || seq !== listing) return false;
        receipt.textContent = uiText("ui.ui.kit.paper.files.c75339bf36") + fileError(e);
        // Keep the last readable folder, selected files and typed path on refusal.
        return false;
      } finally { if (!ended && seq === listing) working(false); }
    }
    async function pick() {
      if (busy || !selected.size) return;
      working(true, uiText("ui.ui.kit.paper.files.a21f01276d"));
      try {
        const files: File[] = []; let bytes = 0;
        for (const entry of selected.values()) {
          const f = await access.read(entry.path); if (ended) return;
          bytes += f.size;
          if (f.size > FILE_BYTES || bytes > (options.bytes ?? FILE_TOTAL)) throw new UiError(uiText("ui.ui.kit.paper.files.fef5893b18"));
          files.push(f);
        }
        finish(files);
      } catch (e) { if (!ended) receipt.textContent = uiText("ui.ui.kit.paper.files.61a6deba9d") + fileError(e); }
      finally { if (!ended) working(false); }
    }
    async function save(overwrite: boolean) {
      if (busy || !current || !options.save) return;
      const said = name.value.trim();
      if (!said || /^[. ]+$/.test(said) || /[\\/:*?"<>|\x00-\x1f]/.test(said) || /[. ]$/.test(said)) { receipt.textContent = uiText("ui.ui.kit.paper.files.26db89c5bd"); name.focus(); return; }
      if (path.value.trim() !== current.path && !await load(path.value.trim())) return;
      if (ended) return;
      saving = true; working(true, uiText("ui.ui.kit.paper.files.81742d9d13"));
      try { finish(await access.save(current.path, said, overwrite)); }
      catch (e) {
        if (ended) return;
        // Legacy native errors are protocol input, independent of the UI locale.
        if (String(e).includes("exists") || String(e).includes("уже существует") || (e as { status?: number }).status === 409) {
          receipt.textContent = uiText("ui.ui.kit.paper.files.013b943ef3");
          confirmText.textContent = uiText("ui.ui.kit.paper.files.f9af0939d1", {p0: String(said)});
          confirm.hidden = false; working(false); keep.focus();
        } else receipt.textContent = uiText("ui.ui.kit.paper.files.c7891cd092") + fileError(e) + uiText("ui.ui.kit.paper.files.9e1ce92c4f");
      } finally { saving = false; if (!ended) working(false); }
    }
    working(false); void load("");
  });
}
