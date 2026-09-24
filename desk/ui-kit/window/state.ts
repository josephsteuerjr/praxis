// Состояние окна. Один объект, без магии; экраны читают его и дёргают bus.
import contract from "../contract.json";

export interface AgentState {
  agent: string;
  owner: string;
  level: "ok" | "live" | "warn" | "error";
  phrase: string;
  action: { label: string; target: string } | null;
  runner: { alive: boolean; age_s: number | null; busy: boolean; run: string; since: number; ever?: boolean };
  brain: {
    configured: boolean;
    model: string;
    base_url: string;
    last_call_at: number | null;
    last_error: string | null;
    last_error_raw: string | null;
    /** Эндпойнт основной ноги закрыт лимитом подписки: слова и час восстановления (25.09). */
    quota?: { words: string; until: number | null; code: string; framework: string } | null;
  };
  relay: { used: boolean; authorized: boolean };
  telegram: { enabled: boolean };
  next_wake: string | null;
  alarms: Array<{ kind: string; text: string }>;
  /** Есть ли снимок харнесса (`memory/.state/anatomy.json`). false — дерево ведёт чужой харнесс. */
  anatomy?: boolean;
  /**
   * Чем поднят канал: пакет desk (`desk.json` рядом с `deskapp.py`). Пусто —
   * канал запущен из репозитория или поставкой старше 0.5.1. На сервере это
   * единственный источник версии: оболочки, которая отвечает `app_info`, там
   * нет, и окно в браузере знало только имя продукта.
   */
  desk?: { version: string; flavor: string; digest: string; built_utc: string; skipped?: string[] };
}

/**
 * Чужой харнесс: снимка анатомии нет и живого руннера нет. Так выглядит
 * Praxis — тем же окном владелец ходит в её дерево на VPS, где ходы
 * ведёт её собственный харнесс без сердцебиения Hélène. Судить о жизни там
 * можно только по вызовам модели и манифестам прогонов; «Не запущен» с
 * кнопкой «Перезапустить» было бы враньём.
 */
export function foreignHarness(): boolean {
  const s = S.agentState;
  return !!s && s.anatomy === false && !s.runner?.alive;
}

/** Прогон свежий: создан не позже получаса назад (для чужого харнесса). */
export function runIsRecent(r: Run, minutes = 30): boolean {
  const at = new Date(r.updated_at || r.created_at || "").getTime();
  return !isNaN(at) && Date.now() - at < minutes * 60_000;
}

export interface Run {
  updated_at?: string;
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

/** Комната: чат окна (`window`, `window-<hex>`) или чат Telegram. */
export interface Room {
  key: string;
  name: string;
  kind: "window" | "telegram";
  live: boolean;
  count: number;
  /** Свежесть архива (нс), для сортировки; 0 — неизвестно. */
  mtime: number;
  /** Комната-заглушка: канал ещё не умеет несколько чатов, живёт только в этом окне. */
  stub?: boolean;
}

export type View = "now" | "talk" | "plans" | "wakes" | "frame" | "files" | "journal" | "anatomy"
  // Подвал полки: раздел «Что поручить» и «Настройки». В `SECTIONS` их нет —
  // по тому массиву строятся кнопки полки и раскладка Ctrl+1…8.
  | "learn" | "settings";

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
  view: "now" as View,
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
  /** Канал ответил 404 на ручки комнат: несколько чатов этот харнесс не умеет. */
  roomsUnsupported: false,
  /**
   * Система, на которой живёт агент: `windows` | `macos` | `linux` по слову
   * оболочки (`app_info.platform`, см. host.ts и ../platform.ts). "" — окно
   * открыто браузером или оболочка старая: тогда не прячется ничего.
   */
  platform: "" as string,
};

/** Ключ комнаты окна по умолчанию и префикс новых — из ui-kit/contract.json
 *  (`rooms.default`, `rooms.pattern` = `^window-[0-9a-f]{8}$`). */
export const WINDOW_ROOM: string = contract.rooms.default;
export const WINDOW_PREFIX: string = contract.rooms.pattern.replace(/^\^/, "").split("[")[0];

/**
 * Историческое значение ключа комнаты окна — `contract.rooms.legacy`.
 *
 * До 10.09.2026 окно писало комнату ключом `pult`. Сегодня его не пишет никто,
 * но он лежит в данных: в манифестах прогонов и в реестрах комнат, собранных до
 * той даты. Читатели обязаны его понимать, иначе старые ходы теряют комнату.
 * Слово выкорчевано из кода 18.09.2026; здесь оно одной строкой — надгробие,
 * а не имя. Новым кодом не пользоваться: писать всегда `WINDOW_ROOM`.
 */
export const LEGACY_WINDOW_KEY: string = contract.rooms.legacy;

export function isWindowRoom(key: string): boolean {
  return key === WINDOW_ROOM || key === LEGACY_WINDOW_KEY || key.startsWith(WINDOW_PREFIX);
}

/**
 * Имя ПРОДУКТА — не имя агента (`S.agent`): агента владелец переименовывает в
 * настройках, продукт остаётся собой. Служебные плашки подписаны им, и слот
 * этого имени не должен пересекаться с `sender_name` из Telegram.
 *
 * ⚠ Имя ставит ИЗДАНИЕ при запуске окна (`start({ productName })`), а не
 * константа: приложений два, и «Hélène», вшитая в общий слой, подписывала бы
 * Praxis чужим именем — в заголовке окна, на полке и в служебных плашках чата.
 * Живое имя из канала (`cfg.product`) по-прежнему главнее обоих.
 */
export let PRODUCT_NAME = "Hélène";

/**
 * Живёт ли агент НА ЭТОЙ машине. Факт СБОРКИ, а не живое состояние канала.
 *
 * ⚠ Почему отдельный флаг, а не сравнение имени продукта. До 18.09.2026
 * раздел «Что поручить» гейтил местные карточки строкой
 * `PRODUCT_NAME !== "Пульт Praxis"`, а издание к серверу выставляет себе
 * имя `"Praxis"` (remote/src/main.ts) — такого имени не выставлял НИКТО.
 * Сравнение было вечно истинным, и в окне к серверу показывались карточки
 * про папки и управление этим компьютером, которых там нет вовсе: пустое
 * обещание на самом первом экране, куда идут за «с чего начать».
 * Имя продукта — подпись для человека, и гейтить по нему нельзя: его
 * переопределяет канал (`cfg.product`).
 */
export let LOCAL_AGENT = true;

/** Зовётся один раз, из `start()`. Своего мнения об имени у общего слоя нет. */
/** Ставится один раз при старте окна: `start({ localAgent })`. */
export function setLocalAgent(local: boolean): void {
  LOCAL_AGENT = local;
}

export function setProductName(name: string): void {
  PRODUCT_NAME = name.trim() || PRODUCT_NAME;
}

/**
 * Прогон «идёт» только пока жив и занят руннер.
 *
 * Манифест упавшего хода навсегда остаётся в статусе running, и зелёная
 * пульсирующая точка спорила с красной шапкой «Не запущен» до конца жизни
 * установки. Руннер однопоточный: если он не занят, ни один ход не идёт.
 */
export function runIsLive(status?: string, run?: Run): boolean {
  if (status !== "running") return false;
  const r = S.agentState?.runner;
  if (!r) return true; // состояния ещё нет — верим манифесту
  // Чужой харнесс: сердцебиения нет, верим манифесту, но не старше получаса.
  if (foreignHarness()) return run ? runIsRecent(run) : true;
  return !!r.alive && !!r.busy;
}
