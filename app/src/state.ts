// Состояние окна. Один объект, без магии; экраны читают его и дёргают bus.

export interface AgentState {
  agent: string;
  owner: string;
  level: "ok" | "live" | "warn" | "error";
  phrase: string;
  action: { label: string; target: string } | null;
  runner: { alive: boolean; age_s: number | null; busy: boolean; run: string; since: number };
  brain: {
    configured: boolean;
    model: string;
    base_url: string;
    last_call_at: number | null;
    last_error: string | null;
    last_error_raw: string | null;
  };
  relay: { used: boolean; authorized: boolean };
  telegram: { enabled: boolean };
  next_wake: string | null;
  alarms: Array<{ kind: string; text: string }>;
}

export interface Run {
  id: string;
  kind: string;
  status: string;
  terminal_status?: string;
  chat_id?: string | number | null;
  chat_title?: string;
  goal_head?: string;
  created_at?: string;
  forge_task_id?: string;
}

export interface Room {
  key: string;
  name: string;
  live: boolean;
  count: number;
}

export type View = "talk" | "plans" | "frame" | "files" | "journal" | "anatomy" | "settings";

/** Реплика владельца, которую он уже отправил, а лента ещё не подтвердила. */
export interface Pending {
  id: number;
  room: string;
  text: string;
  at: string;
  state: "sending" | "queued" | "failed";
  note: string;
}

export const S = {
  agent: "Агент",
  agentState: null as AgentState | null,
  view: "talk" as View,
  room: "window",
  // Комната окна зовётся именем агента; до первого /api/state — как и он сам.
  roomName: "Агент",
  runs: [] as Run[],
  rooms: [] as Room[],
  evOpen: new Set<string>(),
  evCache: new Map<string, unknown>(),
  mdSel: "" as string,
  stream: "" as string,
  captures: [] as string[],
  connected: false,
  pending: [] as Pending[],
};

export const WINDOW_ROOM = "window";

/**
 * Имя ПРОДУКТА — не имя агента (`S.agent`): агента владелец переименовывает в
 * настройках, продукт остаётся Hélène. Служебные плашки подписаны им, и слот
 * этого имени не должен пересекаться с `sender_name` из Telegram, который
 * выбирает сам отправитель.
 */
export const PRODUCT_NAME = "Hélène";

/**
 * Прогон «идёт» только пока жив и занят руннер.
 *
 * Манифест упавшего хода навсегда остаётся в статусе running, и зелёная
 * пульсирующая точка спорила с красной шапкой «Не запущен» до конца жизни
 * установки. Руннер однопоточный: если он не занят, ни один ход не идёт.
 */
export function runIsLive(status?: string): boolean {
  if (status !== "running") return false;
  const r = S.agentState?.runner;
  if (!r) return true; // состояния ещё нет — верим манифесту
  return !!r.alive && !!r.busy;
}
