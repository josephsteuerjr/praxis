// Монтирование: ПРАВИЛА разбора и записи списка папок. Ни строчки DOM.
//
// Отдельный файл от карточки (`mounts.ts`) по одной причине: это то, что
// можно проверить прогоном без окна — и проверено, app/test/mounts.test.mjs.
// Карточка рисует, этот файл решает.
//
// Монтирование: какие папки владельца открыты агенту сверх его дома.
//
// До этого экрана списка не было ВООБЩЕ: владелец правил `helene.json` руками, а
// просьбы агента копились в `data/memory/.state/mounts.json` и были видны только
// в анатомии — то есть человек, ради которого монтирование и сделано, о просьбе
// узнавал в последнюю очередь.
//
// Где что лежит (разбор — localharness/fence.py):
//   * `sandbox.mounts` в helene.json — РЕШЕНИЕ ВЛАДЕЛЬЦА, что открыто. Список
//     правят и это окно, и человек с блокнотом, поэтому окно его СЛИВАЕТ, а не
//     пересобирает (см. settings.ts, «Сохранить»).
//   * `sandbox.mounts_denied` — отказы владельца. Нужны, чтобы «нет» пережило
//     перезапуск и агент не спрашивал по кругу: `Mounts.answer_for` читает их и
//     отвечает агенту «владелец отказал, второй раз не спрашивай».
//   * `data/memory/.state/mounts.json` — ПРОСЬБЫ АГЕНТА (рука `mount_request`).
//     Это его слово, а не решение владельца: окно их только показывает и
//     никогда не пишет.
//
// Живую правду о том, что из списка ДЕЙСТВИТЕЛЬНО открылось (плохой путь,
// системная папка, не заведённый стык в `mnt/`), знает только харнесс, и она
// приезжает снимком в анатомии. Своей проверки путей окно не заводит: вторая
// копия правил `mount_refusal` разошлась бы с первой, и владелец получал бы
// «всё хорошо» на папку, которую агент всё равно не увидит. Здесь только
// заведомо пустой ввод отбивается сразу — чтобы не писать в файл мусор.

/** Одна строка `sandbox.mounts`, как её пишет и читает конфиг. */
export interface MountRow {
  path: string;
  access: "read" | "write";
  why?: string;
  at?: string;
  [k: string]: unknown;
}

/** Одна строка `sandbox.mounts_denied`. */
export interface DeniedRow {
  path: string;
  why?: string;
  at?: string;
  [k: string]: unknown;
}

/** Просьба агента из `memory/.state/mounts.json` (через анатомию). */
export interface MountRequest {
  path?: string;
  real?: string;
  access?: string;
  why?: string;
  at?: string;
  asked?: number;
}

/** Строка живого списка из анатомии: та же папка, но с приговором харнесса. */
export interface LiveMount {
  path?: string;
  real?: string;
  access?: string;
  access_text?: string;
  error?: string;
  link?: string;
  link_error?: string;
}

/** Блок `sandbox` из анатомии — снимок ограды на старте харнесса. */
export interface LiveSandbox {
  enabled?: boolean;
  container?: boolean;
  reason?: string;
  mounts?: LiveMount[];
  denied_mounts?: DeniedRow[];
  mount_requests?: MountRequest[];
}

// Слова доступа — те же, которыми харнесс отвечает агенту (fence._ACCESS_WORDS).
// Две строки, но разойтись им нельзя: владелец даёт «чтение и запись», а агент
// читает про себя то же самое.
export const ACCESS_WORDS: Record<string, string> = { read: "чтение", write: "чтение и запись" };

/** Слово из конфига -> "read" | "write". Незнакомое — чтение: опечатка не должна давать запись. */
export function mountAccess(value: unknown): "read" | "write" {
  if (value === true) return "write";
  const raw = String(value ?? "").trim().toLowerCase();
  return ["write", "w", "rw", "readwrite", "read-write", "read_write", "modify", "m", "запись", "чтение и запись", "чтение-запись"].includes(raw)
    ? "write"
    : "read";
}

/**
 * Ключ сравнения путей: одна папка — один ключ.
 *
 * Регистр, хвостовой разделитель, косая черта в другую сторону и кавычки из
 * проводника папку не различают — так же её видит `os.path.normpath` в
 * харнессе. Ведущие `\\` сетевого пути при этом целы: там две черты значащие.
 */
export function mountKey(path: unknown): string {
  return String(path ?? "")
    .trim()
    .replace(/^"+|"+$/g, "")
    .replace(/\//g, "\\")
    .replace(/\\+$/, "")
    .toLowerCase();
}

/**
 * `sandbox.mounts` из конфига -> строки. Формы те же, что понимает `fence.parse_mounts`.
 *
 * Чужие поля строки СОХРАНЯЮТСЯ: файл правят руками, и `why`/`at` владельца —
 * не мусор окна. Выбрасываем только `write`, когда сами пишем `access`: два
 * имени одной ручки — это две правды, и харнесс читает первую.
 */
export function parseMounts(raw: unknown): MountRow[] {
  const out: MountRow[] = [];
  const seen = new Set<string>();
  for (const item of Array.isArray(raw) ? raw : []) {
    const obj: Record<string, unknown> = typeof item === "string" ? { path: item } : item && typeof item === "object" ? { ...(item as object) } : {};
    const path = String(obj.path ?? "").trim();
    if (!path) continue;
    const key = mountKey(path);
    if (seen.has(key)) continue; // две строки на одну папку: побеждает первая, как в fence
    seen.add(key);
    const access = mountAccess("access" in obj ? obj.access : obj.write);
    delete obj.write;
    out.push({ ...obj, path, access });
  }
  return out;
}

/** `sandbox.mounts_denied` из конфига -> строки отказов. */
export function parseDenied(raw: unknown): DeniedRow[] {
  const out: DeniedRow[] = [];
  const seen = new Set<string>();
  for (const item of Array.isArray(raw) ? raw : []) {
    const obj: Record<string, unknown> = typeof item === "string" ? { path: item } : item && typeof item === "object" ? { ...(item as object) } : {};
    const path = String(obj.path ?? "").trim();
    if (!path) continue;
    const key = mountKey(path);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ ...obj, path });
  }
  return out;
}

/** Дата в том же виде, что пишет харнесс (`fence._stamp_text`): 04.09.2026 18:20. */
export function stamp(now = new Date()): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(now.getDate())}.${two(now.getMonth() + 1)}.${now.getFullYear()} ${two(now.getHours())}:${two(now.getMinutes())}`;
}

/**
 * Заведомо негодный ввод. "" — путь можно записать (годен ли он на самом деле,
 * решает харнесс: системная папка, корень диска, папка самой Hélène).
 */
export function pathProblem(raw: string): string {
  const text = String(raw || "").trim().replace(/^"+|"+$/g, "");
  if (!text) return "Впиши путь к папке — например C:\\Users\\Имя\\Документы.";
  const looksAbsolute = /^[a-zA-Z]:[\\/]/.test(text) || /^\\\\/.test(text) || /^%[^%]+%/.test(text) || text.startsWith("~");
  if (!looksAbsolute) {
    return "Нужен полный путь: с буквой диска (C:\\Users\\Имя\\Документы), сетевой (\\\\сервер\\папка) или через %USERPROFILE%.";
  }
  return "";
}

