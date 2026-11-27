// «Файлы»: её память и навыки — заметки, дневник, душа, работа, желания.
// Читает и правит по её же правилам: только её группы, только маркдаун, и если файл
// изменился, пока он был открыт, — окно не перезапишет чужую правку, а скажет об этом.
import { esc, md } from "../../../ui-kit/text";
import { toast } from "../../../ui-kit/dom";
import { ApiError, api, bytes, el, listToggle, post, why, type Dispose } from "../core";

interface Entry { path: string; name: string; size: number; mtime: number; readonly: boolean }
interface Group { group: string; root: string; total: number; files: Entry[] }
interface Doc { path?: string; text?: string; mtime_ns?: string; size?: number; readonly?: boolean; error?: string; code?: string }

let refreshed = 0;                          // когда дерево читалось в последний раз
let reread: (() => void) | null = null;     // перечитать дерево в фоне, не трогая открытый файл

/** «Файлы» показаны снова: дерево постарело — перечитывается в фоне, прежнее остаётся на месте,
 *  пока не придёт новое. Открытый файл и его позиция не трогаются. */
export function wake(): void {
  if (reread && Date.now() - refreshed > 3 * 60_000) reread();
}

export function mount(root: HTMLElement): Dispose | undefined {
  root.classList.add("files");
  const search = el("input", "chat-search");
  search.type = "search";
  search.placeholder = "Найти файл";
  search.autocomplete = "off";
  search.setAttribute("aria-label", "Найти файл");
  const tree = el("nav", "file-tree");
  tree.setAttribute("aria-label", "Её файлы");
  tree.append(el("div", "muted", "Читаю её файлы — канал обходит все заметки, это занимает несколько секунд…"));
  const side = el("aside", "file-side");
  side.append(search, tree);
  const columns = el("div", "file-columns");
  // Дерево файлов сворачивается кнопкой; открытый файл остаётся открытым.
  const listButton = listToggle("praxis.window.fileTree", "дерево файлов", columns);
  const path = el("h2", "file-path");
  const meta = el("p", "muted file-meta");
  const headMain = el("div", "file-head-main");
  headMain.append(path, meta);
  const bar = el("div", "file-bar");
  const status = el("span", "receipt");
  const heading = el("header", "file-head");
  heading.append(listButton, headMain, bar);
  const body = el("div", "file-body");
  const reader = el("section", "file-reader");
  reader.append(heading, body);
  columns.append(side, reader);
  root.append(columns);

  const edit = el("button", "btn", "Править");
  const save = el("button", "btn btn-primary", "Сохранить");
  const cancel = el("button", "btn btn-quiet", "Отмена");
  for (const item of [edit, save, cancel]) item.type = "button";
  edit.hidden = true;
  save.hidden = cancel.hidden = true;

  let groups: Group[] = [];
  let current = "";
  let doc: Doc | null = null;
  let draft: string | null = null;      // текст правки, пока она не сохранена или не отменена
  let alive = true;
  let version = 0;

  function paintTree() {
    const filter = search.value.trim().toLocaleLowerCase("ru");
    const nodes: HTMLElement[] = [];
    if (!groups.length) { tree.replaceChildren(el("div", "empty", "Файлы не прочитались.")); return; }
    for (const group of groups) {
      const files = group.files.filter((file) => !filter || file.path.toLocaleLowerCase("ru").includes(filter));
      if (!files.length) continue;
      const details = el("details", "fold file-group");
      details.open = true;
      const summary = el("summary");
      summary.innerHTML = `${esc(group.group)} <span class="n">${Number(group.total) || files.length}</span>`;
      details.append(summary);
      for (const file of files) {
        const button = el("button", "file" + (file.readonly ? " readonly" : "") + (file.path === current ? " chosen" : ""));
        button.type = "button";
        button.title = file.path;
        const when = file.mtime ? new Date(file.mtime * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
        button.innerHTML = `<span class="file-name">${esc(file.name)}</span><span class="file-when">${esc(when)}</span>`;
        button.addEventListener("click", () => void open(file.path));
        details.append(button);
      }
      nodes.push(details);
    }
    tree.replaceChildren(...(nodes.length ? nodes : [el("div", "empty", groups.length ? "Ничего не нашлось." : "Файлы не прочитались.")]));
  }

  async function loadTree() {
    try {
      groups = await api<Group[]>("/api/md-tree");
      refreshed = Date.now();
      if (alive) paintTree();
    } catch { /* дерево останется прежним; его обновит следующее открытие файла */ }
  }
  reread = () => void loadTree();

  function paint() {
    bar.replaceChildren();
    status.textContent = "";
    status.className = "receipt";
    body.replaceChildren();
    if (!doc) return;
    path.textContent = doc.path || "";
    if (doc.error) { meta.textContent = ""; body.append(el("div", "empty", doc.error)); return; }
    const changed = Number(doc.mtime_ns) ? new Date(Number(doc.mtime_ns) / 1e6) : null;
    meta.textContent = [bytes(doc.size),
      changed && !Number.isNaN(changed.getTime()) ? `изменён ${changed.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })}` : "",
      doc.readonly ? "только чтение: файл подписан агентом" : ""].filter(Boolean).join(" · ");
    if (draft !== null) {
      const area = document.createElement("textarea");
      area.className = "file-edit mono";
      area.spellcheck = false;
      area.value = draft;
      area.setAttribute("aria-label", `Правка ${doc.path}`);
      area.addEventListener("input", () => { draft = area.value; });
      body.append(area);
      bar.append(save, cancel, status);
      area.focus();
      return;
    }
    const paper = el("article", "card md file-paper");
    paper.innerHTML = md(doc.text || "");
    body.append(paper);
    edit.hidden = !!doc.readonly;
    bar.append(edit);
  }

  async function open(target: string) {
    if (draft !== null && !window.confirm("Правка не сохранена. Открыть другой файл и потерять её?")) return;
    draft = null;
    current = target;
    const mine = ++version;
    doc = null;
    path.textContent = target;
    meta.textContent = "Читаю…";
    paintTree();
    paint();
    try {
      const value = await api<Doc>(`/api/md?path=${encodeURIComponent(target)}`);
      if (alive && mine === version) { doc = value; paint(); }
    } catch (error) {
      if (alive && mine === version) { doc = { path: target, error: why(error) }; paint(); }
    }
  }

  edit.addEventListener("click", () => { if (doc && !doc.readonly && !doc.error) { draft = doc.text || ""; paint(); } });
  cancel.addEventListener("click", () => { draft = null; paint(); });
  save.addEventListener("click", async () => {
    if (draft === null || !doc?.path) return;
    const text = draft;
    save.disabled = true;
    try {
      const saved = await post<Doc>("/api/md", { path: doc.path, text, mtime_ns: doc.mtime_ns });
      draft = null;
      doc = { ...doc, ...saved, text, error: undefined };
      paint();
      toast("Сохранено.");
      void loadTree();
    } catch (error) {
      // 409: она успела поправить файл своей рукой — правка владельца остаётся в поле.
      status.textContent = error instanceof ApiError && error.status === 409
        ? "Файл изменился, пока он был открыт. Правка осталась в поле: откройте файл заново и перенесите её."
        : why(error);
      status.className = "receipt err";
    } finally {
      save.disabled = false;
    }
  });
  search.addEventListener("input", paintTree);

  void loadTree().then(() => {
    if (!alive) return;
    const first = groups[0]?.files[0];
    if (first) void open(first.path);
  });
  return () => { alive = false; version++; };
}
