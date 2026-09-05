// Файлы: память агента как она есть — маркдауны по группам, чтение справа.
import { ApiError, api, post } from "../api";
import { bindFail, esc, failHTML, fmtAge, humanError, md, q, safeRender, toast } from "../lib";
import { S } from "../state";

interface Group {
  group: string;
  root: string;
  // Настоящее число файлов в группе: список обрезан двумя сотнями, и «200»
  // выдавалось за размер памяти агента. Труба отдаёт total (deskd/readers.py).
  total?: number;
  files: Array<{ path: string; name: string; mtime: number; readonly?: boolean }>;
}

/** Ответ GET /api/md. `mtime_ns` — отпечаток прочитанного, `readonly` — приговор трубы. */
interface Doc {
  error?: string;
  text?: string;
  // СТРОКА, а не число: st_mtime_ns ~1.79e18 больше Number.MAX_SAFE_INTEGER, и
  // JSON.parse округляет его до ближайшего double (…380500 → …380400). Число,
  // отправленное обратно, не совпало бы само с собой — сверка на трубе давала
  // бы конфликт на каждое сохранение. Старая труба ещё шлёт число: такой
  // отпечаток мы не отправляем вовсе (он заведомо испорчен), и запись идёт
  // по-старому, под защитой копии .bak.
  mtime_ns?: string | number;
  readonly?: boolean;
}

/** Отпечаток, который можно вернуть трубе без потери точности. */
function exactStamp(v: unknown): string | undefined {
  return typeof v === "string" && /^\d+$/.test(v) ? v : undefined;
}

/** Последнее дерево файлов: по нему ищем прошлые версии души. */
let lastGroups: Group[] = [];

/**
 * Где лежат ПРОШЛЫЕ версии живого файла души.
 *
 * Правку своей конституции агент применяет сразу и ни с кем не согласует
 * (live/identity.py: «Применяется сразу; согласования нет»), а окно про это
 * молчало: «Журнал» показывает только ошибки модели и пропуски восприятия,
 * «Файлы» — текст без единой пометки. Единственным тормозом на мгновенное
 * изменение зоны K оставалась норма в самом тексте — то есть ровно то, во что
 * бьёт инъекция. Откат существует (история версий настоящая), но только если
 * заметить. Здесь мы делаем факт правки видимым.
 */
function historyOf(path: string): { dir: string; head: string } | null {
  if (path === "soul/SOUL.md") return { dir: "soul/archive/", head: "SOUL_v" };
  if (path === "soul/VOICE.md") return { dir: "soul/archive/", head: "VOICE_v" };
  if (path === "soul/self/CURRENT.md") return { dir: "soul/self/history/", head: "" };
  return null;
}

/** Самая свежая прошлая версия из дерева — она же текст ДО последней правки. */
function prevVersion(path: string): { path: string; mtime: number } | null {
  const where = historyOf(path);
  if (!where) return null;
  let best: { path: string; mtime: number } | null = null;
  for (const g of lastGroups) {
    for (const f of g.files) {
      if (!f.path.startsWith(where.dir)) continue;
      const tail = f.path.slice(where.dir.length);
      if (!tail.startsWith(where.head) || tail.includes("/")) continue;
      if (!best || f.mtime > best.mtime) best = { path: f.path, mtime: f.mtime };
    }
  }
  return best;
}

/** Причина и автор правки из служебной первой строки архивной версии. */
function versionMeta(text: string): { reason: string; by: string; version: string } {
  const line = text.split("\n", 1)[0] || "";
  const open = line.indexOf("{");
  const close = line.lastIndexOf("}");
  if (!line.startsWith("<!--") || open < 0 || close < open) return { reason: "", by: "", version: "" };
  try {
    const m = JSON.parse(line.slice(open, close + 1)) as Record<string, unknown>;
    return {
      reason: String(m.reason || ""),
      by: String(m.by || ""),
      version: String(m.version ?? m.revision ?? ""),
    };
  } catch {
    return { reason: "", by: "", version: "" };
  }
}

/** Служебную первую строку в дифф не тащим — она не часть документа. */
function stripMeta(text: string): string {
  const nl = text.indexOf("\n");
  return text.startsWith("<!--") && nl > 0 ? text.slice(nl + 1) : text;
}

/**
 * Изменившийся участок: общее начало и общий хвост отбрасываем, остальное
 * показываем как «было/стало». Не построчный LCS — но и не враньё: участок
 * назван участком, и внутри него видно обе редакции целиком.
 */
function diffHTML(before: string, after: string): string {
  const a = stripMeta(before).split("\n");
  const b = after.split("\n");
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const gone = a.slice(head, a.length - tail);
  const came = b.slice(head, b.length - tail);
  if (!gone.length && !came.length) return '<p class="field-hint">Текст совпадает с прошлой версией.</p>';
  const cap = 200;
  const block = (rows: string[], cls: string, sign: string) =>
    rows
      .slice(0, cap)
      .map((r) => `<div class="${cls}">${esc(sign + r)}</div>`)
      .join("") + (rows.length > cap ? `<div class="muted">…и ещё ${rows.length - cap} строк</div>` : "");
  return `<p class="field-hint">Изменившийся участок, строки с ${head + 1}-й.</p>
    <div class="card md diff">${block(gone, "diff-gone", "− ")}${block(came, "diff-came", "+ ")}</div>`;
}

export async function render(container: HTMLElement): Promise<void> {
  const groups = await api<Group[]>("/api/md-tree");
  lastGroups = groups;
  if (!groups.length) {
    container.innerHTML = '<div class="empty"><b>Файлов пока нет</b>Память появится после первых ходов.</div>';
    return;
  }
  const cols = groups
    .map((g, i) => {
      const shown = g.files.length;
      const total = typeof g.total === "number" ? g.total : shown;
      // Молчаливый срез был хуже пустоты: из тысячи заметок окно показывало
      // произвольные двести и печатало «200» как их полное число.
      const count = total > shown ? `${shown} из ${total}` : String(shown);
      return `<details class="fold" ${i === 0 || (S.mdSel || "").startsWith(g.root) ? "open" : ""}>
      <summary><b>${esc(g.group)}</b> <span class="muted">${esc(count)}</span></summary>
      <div class="fold-body" style="padding:0 6px 8px">${
        total > shown ? `<p class="field-hint" style="margin:4px 2px 8px">Показаны ${shown} самых свежих из ${total}.</p>` : ""
      }${g.files
        .map(
          (f) => `<div class="item" role="button" aria-current="${f.path === S.mdSel}" data-path="${esc(f.path)}">
          <span>${esc(f.name)}${f.readonly ? ' <span class="badge">только чтение</span>' : ""}</span><span class="n">${fmtAge(f.mtime)}</span></div>`,
        )
        .join("")}</div>
    </details>`;
    })
    .join("");
  container.innerHTML = `<div class="split wide">
    <div class="list">${cols}</div>
    <div class="reading" id="md-main"><div class="empty">Выбери файл слева</div></div>
  </div>`;
  for (const el of container.querySelectorAll<HTMLElement>(".item[data-path]")) {
    // Раньше здесь стоял голый `void open(...)`: при обрыве трубы api() бросал,
    // ловца не было, и правая половина навсегда оставалась в «читаю…».
    el.addEventListener("click", () => {
      const main = q<HTMLElement>("#md-main", container);
      safeRender(main, () => open(container, el.dataset.path!));
    });
  }
  if (S.mdSel) safeRender(q<HTMLElement>("#md-main", container), () => open(container, S.mdSel));
}

async function open(container: HTMLElement, path: string) {
  S.mdSel = path;
  for (const el of container.querySelectorAll<HTMLElement>(".item[data-path]")) {
    el.setAttribute("aria-current", String(el.dataset.path === path));
  }
  const main = q<HTMLElement>("#md-main", container);
  main.innerHTML = '<div class="empty">читаю…</div>';
  const doc = await api<Doc>("/api/md?path=" + encodeURIComponent(path));
  if (doc.error) {
    // Труба отдаёт сырое `FileNotFoundError: [WinError 2] … 'C:\…'`; владельцу
    // нужно слово, а не имя класса исключения. Сырое — в складку.
    main.innerHTML = failHTML(doc.error, { retry: false });
    bindFail(main);
    return;
  }
  const text = doc.text || "";
  // Отпечаток прочитанного. Труба сверяет его при записи и отвечает 409, если
  // файл сменился, — но только если окно его ПРИСЛАЛО. Пока окно не присылало,
  // защита не срабатывала никогда: правку агента (включая конституцию) окно
  // затирало молча, и единственным рубежом оставалась копия .md.bak.
  const seenMtime = exactStamp(doc.mtime_ns);
  // «Править» на файле, который труба править не даст, — обещание, за которое
  // владелец платит потерянным текстом: отказ приходил только по «Сохранить».
  const readonly = !!doc.readonly;
  const prev = prevVersion(path);
  main.innerHTML = `<div class="tools-row"><span class="muted mono">${esc(path)}</span>
      ${readonly ? "" : '<button class="btn btn-quiet" id="md-edit" type="button">Править</button>'}</div>
    ${readonly ? '<p class="field-hint">Этот файл подписан агентом: ручная правка снаружи его обнуляет, поэтому окно его только показывает.</p>' : ""}
    ${
      prev
        ? `<div class="notice" id="md-revised">
        <span class="dot"></span>
        <span>Агент правит этот файл сам и без спроса. Последняя его правка: ${esc(fmtAge(prev.mtime))} назад.</span>
        <button class="notice-action" id="md-diff" type="button">Что изменилось</button>
      </div><div id="md-diff-out"></div>`
        : ""
    }
    <div class="card md" id="md-view">${md(text)}</div>
    <div id="md-editor" hidden>
      <textarea class="md-editor" spellcheck="false"></textarea>
      <div class="actions" style="margin-top:12px">
        <button class="btn btn-primary" id="md-save" type="button">Сохранить</button>
        <button class="btn btn-quiet" id="md-cancel" type="button">Отмена</button>
        <span class="receipt" id="md-receipt"></span>
      </div>
      <div id="md-conflict" hidden></div>
    </div>`;
  if (prev) bindDiff(main, prev.path, text);
  if (readonly) return;
  const editor = q<HTMLTextAreaElement>(".md-editor", main);
  const viewBox = q<HTMLElement>("#md-view", main);
  const editBox = q<HTMLElement>("#md-editor", main);
  const receipt = q<HTMLElement>("#md-receipt", main);
  const conflictBox = q<HTMLElement>("#md-conflict", main);
  editor.value = text;
  q("#md-edit", main).addEventListener("click", () => {
    viewBox.hidden = true;
    editBox.hidden = false;
    editor.focus();
  });
  q("#md-cancel", main).addEventListener("click", () => {
    editor.value = text;
    conflictBox.hidden = true;
    editBox.hidden = true;
    viewBox.hidden = false;
  });

  /** `stamp` = undefined — писать поверх сознательно, без сверки отпечатка. */
  const write = async (stamp: string | undefined) => {
    receipt.className = "receipt";
    receipt.textContent = "пишу…";
    try {
      await post("/api/md", { path, text: editor.value, mtime_ns: stamp });
      toast("Сохранено: агент увидит это в следующем ходу");
      await open(container, path);
    } catch (e) {
      if (e instanceof ApiError && e.code === "conflict") {
        // Развилка вместо молчания. Текст владельца остаётся в поле — что бы он
        // ни выбрал, набранное не пропадает.
        receipt.className = "receipt err";
        receipt.textContent = "Файл изменился, пока он был открыт.";
        await showConflict();
        return;
      }
      receipt.className = "receipt err";
      receipt.textContent = "Не сохранилось: " + humanError(e).text;
    }
  };

  const showConflict = async () => {
    let theirs = "";
    try {
      const fresh = await api<Doc>("/api/md?path=" + encodeURIComponent(path));
      theirs = fresh.text || "";
    } catch {
      theirs = "";   // не прочиталось — развилку всё равно показываем
    }
    conflictBox.hidden = false;
    conflictBox.innerHTML = `<div class="notice err" style="margin-top:12px">
      <span class="dot failed"></span>
      <span>Агент изменил этот файл, пока он был открыт в окне. Если сохранить как есть, его правка пропадёт.</span>
    </div>
    ${theirs ? `<details class="fail-detail"><summary>Что в файле сейчас</summary><pre class="mono">${esc(theirs)}</pre></details>` : ""}
    <div class="actions" style="margin-top:10px">
      <button class="btn btn-primary" id="md-reread" type="button">Перечитать (мои правки потеряются)</button>
      <button class="btn btn-quiet" id="md-force" type="button">Перезаписать своим текстом</button>
    </div>`;
    q("#md-reread", conflictBox).addEventListener("click", () => {
      safeRender(main, () => open(container, path));
    });
    q("#md-force", conflictBox).addEventListener("click", () => {
      conflictBox.hidden = true;
      void write(undefined);
    });
  };

  q("#md-save", main).addEventListener("click", () => void write(seenMtime));
}

/** «Что изменилось»: прошлая версия читается по нажатию, а не всегда. */
function bindDiff(main: HTMLElement, prevPath: string, current: string) {
  const btn = main.querySelector<HTMLButtonElement>("#md-diff");
  const out = main.querySelector<HTMLElement>("#md-diff-out");
  if (!btn || !out) return;
  btn.addEventListener("click", () => {
    btn.disabled = true;
    btn.textContent = "читаю…";
    safeRender(out, async () => {
      const doc = await api<Doc>("/api/md?path=" + encodeURIComponent(prevPath));
      btn.disabled = false;
      btn.textContent = "Что изменилось";
      if (doc.error) {
        out.innerHTML = failHTML(doc.error, { retry: false });
        bindFail(out);
        return;
      }
      const before = doc.text || "";
      const meta = versionMeta(before);
      const why = meta.reason
        ? `<p class="field-hint">Основание, записанное агентом${meta.by ? " (" + esc(meta.by) + ")" : ""}: ${esc(meta.reason)}</p>`
        : "";
      out.innerHTML = `<p class="field-hint mono">${esc(prevPath)}${meta.version ? " · версия " + esc(meta.version) : ""}</p>${why}${diffHTML(before, current)}`;
    });
  });
}
