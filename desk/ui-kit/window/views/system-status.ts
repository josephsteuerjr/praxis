import type { ComputerLive } from "../mode";

/** A startup anatomy snapshot cannot establish current driver availability. */
export function computerOverview(config: { enabled?: boolean; scopes?: string[] } | undefined,
                                 live: ComputerLive | undefined, now = Date.now()) {
  const scopes = Array.isArray(config?.scopes) ? config.scopes.filter((v) => typeof v === "string") : [];
  if (config?.enabled === false) return { status: "Выключен владельцем", scopes };
  if (!live || !Object.keys(live).length) return { status: "Снимка пока нет", scopes };
  const written = Date.parse(live.written_at || "");
  if (!Number.isFinite(written) || now - written > 30_000 || written > now + 5_000) return { status: "Снимок компьютера устарел", scopes };
  if (live.enabled === false) return { status: "Выключен владельцем", scopes };
  if (live.available === false) return { status: "Драйвер не найден", scopes };
  const checked = Date.parse(live.checked_at || "");
  if (!Number.isFinite(checked) || now - checked > 30_000 || checked > now + 5_000) return { status: "Связь ещё не проверена", scopes };
  if (live.platform === "linux" && live.connected === true) {
    if (live.console === false) return { status: "Драйвер подключён; графический стол недоступен", scopes };
    if (live.console !== true) return { status: "Драйвер подключён; рабочий стол ещё не проверен", scopes };
  }
  return { status: live.connected === true ? "Готов к работе" : live.connected === false ? "Драйвер не отвечает" : "Связь ещё не проверена", scopes };
}
