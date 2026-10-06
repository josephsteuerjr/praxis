// Немедленная запись ключа верхней ступени (`service.session0`) в helene.json.
//
// 05.10, слово владельца: согласие «Разрешить права СИСТЕМЫ» — не черновик, и
// выбор переживал перезапуск программы только если попадал в helene.json в
// момент нажатия. Прежний путь вёл через отдельную кнопку «Сохранить», которую
// после явного согласия никто не ищет, — и на Windows ступень слетала при
// каждом перезапуске.
//
// Здесь — только чистая работа с файлом через shell: config_load → правка
// ОДНОГО ключа → config_save по свежему отпечатку. Черновик остальных настроек
// не трогаем: пишем то, что лежит в файле, плюс один ключ. Отпечаток уходит
// наружу ПАРОЙ base/fresh (ревью 06.10 — уточнение ревью 05.10, не отмена):
// «подтвердить» черновик чужим отпечатком по-прежнему нельзя, поэтому экран
// принимает fresh только при совпадении base с отпечатком своего открытия.
// Поэтому файл импортируется на прямую в стенд без DOM.
export type ShellFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

/** Старая оболочка отказывается строкой `stale:<mtime>` — привести её к словам. */
function toHumanError(e: unknown): Error {
  const text = e instanceof Error ? e.message : String(e ?? "");
  return /^stale:/.test(text) ? new Error("файл менялся, пока экран был открыт") : e instanceof Error ? e : new Error(text);
}

/**
 * Записать service.session0 немедленно.
 *
 * Возвращает отпечаток файла ПАРОЙ (ревью 06.10, P1): `base` — по которому
 * читали перед записью, `fresh` — после неё. Экран принимает `fresh` как свой
 * ТОЛЬКО при совпадении `base` с тем, что он сам читал при открытии: файл мог
 * поменять Блокнот между экраном и согласием, и без сверки базы принятие
 * свежего отпечатка легализовало бы затирание чужой правки ближайшим
 * «Сохранить» (то самое, чего требовало не делать ревью 05.10). Оба поля
 * `null` — старая оболочка без отпечатков: экран ничего не принимает.
 * Бросает ошибку словами при конфликте свежести, нечитаемости или пустом
 * конфиге — карточка показывает её владельцу как есть.
 */
export interface PersistStamp {
  base: string | null;
  fresh: string | null;
}

export async function persistSession0(shell: ShellFn, on: boolean): Promise<PersistStamp> {
  let loaded: { config?: unknown; mtime_ns?: string | number } | null = null;
  try {
    loaded = await shell("config_load") as { config?: unknown; mtime_ns?: string | number } | null;
  } catch (e) {
    throw toHumanError(e);
  }
  const cfg = loaded && typeof loaded === "object" && loaded.config && typeof loaded.config === "object"
    ? { ...(loaded.config as Record<string, unknown>) }
    : null;
  // Пустой конфиг — это «файла нет», а не «конфиг из одного ключа»: согласие
  // не должно создавать helene.json, состоящий из одной галочки (ревью 05.10).
  if (!cfg || !Object.keys(cfg).length) throw new Error("настройки не прочитались");
  const svc = cfg.service && typeof cfg.service === "object"
    ? { ...(cfg.service as Record<string, unknown>) }
    : {};
  svc.session0 = on;
  cfg.service = svc;
  const args: Record<string, unknown> = { config: JSON.stringify(cfg) };
  if (loaded && loaded.mtime_ns != null) args.mtimeNs = String(loaded.mtime_ns);
  let r: { ok?: boolean; code?: string; error?: string; mtime_ns?: string | number } | null = null;
  try {
    r = await shell("config_save", args) as { ok?: boolean; code?: string; error?: string; mtime_ns?: string | number } | null;
  } catch (e) {
    throw toHumanError(e);
  }
  if (r && typeof r === "object" && r.ok === false) {
    throw new Error(r.error || "файл менялся, пока экран был открыт");
  }
  return {
    base: loaded && loaded.mtime_ns != null ? String(loaded.mtime_ns) : null,
    fresh: r && typeof r === "object" && r.mtime_ns != null ? String(r.mtime_ns) : null,
  };
}
