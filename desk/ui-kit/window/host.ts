// Ответ оболочки о себе (`app_info`) — один раз на окно.
//
// Раньше `app_info` спрашивали дважды (полка — ради версии, настройки — ради
// пути к журналу), и оба места знали только версию. Теперь ответ кэшируется, а
// в нём есть `platform` — по нему окно прячет то, чего на системе агента нет
// (см. ../platform.ts). Вне оболочки (окно открыто браузером — Пульт на
// сервере) ответа нет: null, и не прячется ничего — местных карточек там и так
// нет.
import { inTauri, shell } from "./api";
import { platformOf } from "../platform";

export interface HostInfo {
  version: string;
  exe_dir?: string;
  log?: string;
  /** `windows` | `macos` | `linux`; старая оболочка поля не шлёт. */
  platform?: string;
  arch?: string;
  /** Корень установки (на macOS — папка над бандлом). */
  root?: string;
}

let cached: Promise<HostInfo | null> | null = null;

/** Что оболочка знает о себе. Один вызов на жизнь окна; отказ — null. */
export function hostInfo(): Promise<HostInfo | null> {
  if (!inTauri) return Promise.resolve(null);
  if (!cached) {
    cached = shell<HostInfo>("app_info").then(
      (i) => (i && typeof i === "object" ? i : null),
      () => null,
    );
  }
  return cached;
}

/** Слово системы хоста, проверенное по контракту; "" — неизвестно. */
export async function hostPlatform(): Promise<string> {
  return platformOf(await hostInfo());
}
