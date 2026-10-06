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
    const ui = paperDialog(options.save ? "Сохранить файл" : "Приложить файлы", true);
    let ended = false, busy = false, saving = false, listing = 0, current: Folder | null = null;
    const selected = new Map<string, FileEntry>();
    let firstFolder = true;
    const history: string[] = [];
    const lead = document.createElement("p"); lead.className = "paper-lead";
    lead.textContent = options.save ? "Выбери папку и имя на своём компьютере." : "Выбирай файлы на своём компьютере. До 16 файлов: каждый до 64 МБ, вместе до 128 МБ.";
    const toolbar = document.createElement("div"); toolbar.className = "paper-file-toolbar";
    const back = paperButton("Назад", () => { const path = history.pop(); if (path !== undefined) void load(path, false); }); back.disabled = true;
    const up = paperButton("Выше", () => { if (current?.parent) void load(current.parent); }); up.disabled = true;
    const path = document.createElement("input"); path.type = "text"; path.setAttribute("aria-label", "Путь к папке");
    path.placeholder = "Путь к папке";
    const go = paperButton("Перейти", () => void load(path.value));
    path.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); void load(path.value); } });
    toolbar.append(back, up, path, go);
    const browser = document.createElement("div"); browser.className = "paper-file-browser";
    const places = document.createElement("nav"); places.setAttribute("aria-label", "Места");
    const center = document.createElement("div"); center.className = "paper-file-center";
    const search = document.createElement("input"); search.type = "search"; search.placeholder = "Найти в этой папке"; search.setAttribute("aria-label", "Найти в этой папке");
    const rows = document.createElement("div"); rows.className = "paper-file-rows"; rows.setAttribute("aria-label", "Файлы и папки");
    const hiddenLabel = document.createElement("label"); hiddenLabel.className = "paper-hidden-files";
    const hiddenFiles = document.createElement("input"); hiddenFiles.type = "checkbox";
    hiddenLabel.append(hiddenFiles, "Показать скрытые файлы");
    const info = document.createElement("p"); info.className = "paper-file-info";
    center.append(search, hiddenLabel, rows, info); browser.append(places, center);
    const name = document.createElement("input"); name.type = "text"; name.value = options.save || ""; name.setAttribute("aria-label", "Имя файла");
    const nameLabel = document.createElement("label"); nameLabel.className = "paper-save-name"; nameLabel.append("Имя файла", name);
    nameLabel.hidden = !options.save;
    const receipt = document.createElement("p"); receipt.className = "paper-file-receipt"; receipt.setAttribute("role", "status");
    const confirm = document.createElement("div"); confirm.className = "paper-replace"; confirm.hidden = true;
    const selection = document.createElement("div"); selection.className = "paper-selection";
    selection.setAttribute("aria-label", "Выбранные файлы"); selection.hidden = true;
    const confirmText = document.createElement("p");
    const replace = paperButton("Заменить файл", () => void save(true));
    const keep = paperButton("Вернуться к имени", () => { confirm.hidden = true; name.focus(); name.select(); });
    confirm.append(confirmText, keep, replace);
    const cancel = paperButton("Отмена", () => ui.dialog.close());
    const accept = paperButton(options.save ? "Сохранить" : "Приложить", () => void (options.save ? save(false) : pick()), true);
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
      receipt.textContent = selected.size ? `Выбрано ${selected.size} · ${fileSize(bytes)}` : "";
      selection.replaceChildren(); selection.hidden = !selected.size;
      for (const f of selected.values()) {
        const remove = paperButton(f.name + " ×", () => { selected.delete(f.path); selectionInfo(); paint(); });
        remove.setAttribute("aria-label", "Убрать " + f.name); selection.append(remove);
      }
      accept.textContent = options.save ? "Сохранить" : selected.size ? `Приложить (${selected.size})` : "Приложить";
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
            if (f.size > FILE_BYTES) { receipt.textContent = `${f.name}: больше 64 МБ. Выбери файл поменьше.`; return; }
            if (selected.size >= max) { receipt.textContent = `Можно добавить ещё ${max} файлов. Убери один из выбранных.`; return; }
            if ([...selected.values()].reduce((s, a) => s + a.size, f.size) > budget) { receipt.textContent = "Вложения вместе превышают 128 МБ. Убери один из выбранных."; return; }
            selected.set(f.path, f);
          }
          selectionInfo(); paint();
          rows.querySelector<HTMLButtonElement>(`[data-row="${current!.entries.indexOf(f)}"]`)?.focus({ preventScroll: true });
        });
        row.className += " paper-file-row"; row.dataset.row = String(current.entries.indexOf(f));
        const icon = document.createElement("span"); icon.className = "paper-file-icon"; icon.textContent = f.folder ? "▱" : selected.has(f.path) ? "✓" : "▤"; icon.setAttribute("aria-hidden", "true");
        const label = document.createElement("span"); label.className = "paper-file-label"; label.textContent = f.name;
        const detail = document.createElement("span"); detail.className = "paper-file-size"; detail.textContent = f.folder ? "Папка" : fileSize(f.size);
        if (!f.folder && !options.save) row.setAttribute("aria-pressed", String(selected.has(f.path)));
        row.append(icon, label, detail); rows.append(row);
      }
      info.textContent = !filtered.length ? (search.value ? "Совпадений нет — попробуй другое имя." : "Папка пуста.")
        : filtered.length > 500 ? `Показано 500 из ${filtered.length}. Введи часть имени в поиске.` : `${filtered.length} объектов`;
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
      const seq = ++listing; working(true, "Открываю папку…"); confirm.hidden = true;
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
        receipt.textContent = "Не удалось открыть папку: " + fileError(e);
        // Keep the last readable folder, selected files and typed path on refusal.
        return false;
      } finally { if (!ended && seq === listing) working(false); }
    }
    async function pick() {
      if (busy || !selected.size) return;
      working(true, "Читаю выбранные файлы…");
      try {
        const files: File[] = []; let bytes = 0;
        for (const entry of selected.values()) {
          const f = await access.read(entry.path); if (ended) return;
          bytes += f.size;
          if (f.size > FILE_BYTES || bytes > (options.bytes ?? FILE_TOTAL)) throw new Error("Размер выбранных файлов изменился. Выбери их заново.");
          files.push(f);
        }
        finish(files);
      } catch (e) { if (!ended) receipt.textContent = "Файл не прочитался: " + fileError(e); }
      finally { if (!ended) working(false); }
    }
    async function save(overwrite: boolean) {
      if (busy || !current || !options.save) return;
      const said = name.value.trim();
      if (!said || /^[. ]+$/.test(said) || /[\\/:*?"<>|\x00-\x1f]/.test(said) || /[. ]$/.test(said)) { receipt.textContent = "Введи имя файла без пути и служебных символов."; name.focus(); return; }
      if (path.value.trim() !== current.path && !await load(path.value.trim())) return;
      if (ended) return;
      saving = true; working(true, "Сохраняю…");
      try { finish(await access.save(current.path, said, overwrite)); }
      catch (e) {
        if (ended) return;
        if (String(e).includes("exists") || String(e).includes("уже существует") || (e as { status?: number }).status === 409) {
          receipt.textContent = "Существующий файл оставлен без изменений.";
          confirmText.textContent = `«${said}» уже есть в этой папке. Заменить его содержимое?`;
          confirm.hidden = false; working(false); keep.focus();
        } else receipt.textContent = "Не удалось сохранить: " + fileError(e);
      } finally { saving = false; if (!ended) working(false); }
    }
    working(false); void load("");
  });
}
