// Комнаты окна: список, создание, переименование, удаление.
//
// Контракт — desk-notes/КОНТРАКТ-B→A.md §1: `GET /api/chats` (с `kind`),
// `POST /api/rooms`, `POST /api/rooms/{peer}`, `DELETE /api/rooms/{peer}`.
// Пока харнесс этих ручек не знает (404/405), окно живёт с ЗАГЛУШКОЙ той же
// формы: комнаты хранятся в localStorage этого окна, писать в них нельзя, и
// владелец видит это словами. Заглушка включается сама по первому 404 и
// помечает комнаты `stub: true`.
import { ApiError, api, del, post } from "./api";
import { S, WINDOW_PREFIX, WINDOW_ROOM, isWindowRoom, runIsLive, type Room, type Run } from "./state";

export interface ChatRow {
  peer_id: string;
  title?: string;
  kind?: "window" | "telegram";
  messages?: number;
  mtime_ns?: number | string;
}

const STUB_KEY = "frame.rooms.stub";

function readStub(): Array<{ key: string; name: string; at: number }> {
  try {
    const raw = localStorage.getItem(STUB_KEY);
    const rows = raw ? (JSON.parse(raw) as Array<{ key: string; name: string; at: number }>) : [];
    return Array.isArray(rows) ? rows.filter((r) => r && typeof r.key === "string") : [];
  } catch {
    return [];
  }
}

function writeStub(rows: Array<{ key: string; name: string; at: number }>) {
  try {
    localStorage.setItem(STUB_KEY, JSON.stringify(rows));
  } catch {
    // без хранилища заглушка живёт до перезапуска
  }
}

function hex8(): string {
  const b = new Uint8Array(4);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** 404/405 от канала — ручки нет: харнесс старее контракта. */
function unsupported(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 405);
}

/** Собрать список комнат из ответа канала, прогонов и заглушки. */
export function buildRooms(runs: Run[], chats?: ChatRow[]): Room[] {
  const byKey = new Map<string, Room>();
  // Комната окна носит имя агента, а не слово «Окно» (слово владельца 06.09).
  byKey.set(WINDOW_ROOM, { key: WINDOW_ROOM, name: S.agent, kind: "window", live: false, count: 0, mtime: Number.MAX_SAFE_INTEGER });
  for (const c of chats || []) {
    const key = String(c.peer_id);
    if (key === "pult" || key === WINDOW_ROOM) continue;
    const kind: Room["kind"] = c.kind === "window" || (c.kind !== "telegram" && isWindowRoom(key)) ? "window" : "telegram";
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        name: c.title || (kind === "window" ? "Новый чат" : "чат " + key),
        kind,
        live: false,
        count: c.messages || 0,
        mtime: Number(c.mtime_ns) || 0,
      });
    }
  }
  for (const s of readStub()) {
    if (!byKey.has(s.key)) byKey.set(s.key, { key: s.key, name: s.name, kind: "window", live: false, count: 0, mtime: s.at * 1e6, stub: true });
  }
  for (const r of runs) {
    if (r.kind !== "chat_turn" || r.chat_id == null) continue;
    let key = String(r.chat_id);
    if (key === "pult") key = WINDOW_ROOM;
    // A successful catalog is authoritative for local rooms. Historical runs
    // remain visible in the activity list, but must not resurrect archived chats.
    if (chats !== undefined && isWindowRoom(key) && !byKey.has(key)) continue;
    const room = byKey.get(key) ?? {
      key,
      name: r.chat_title || (isWindowRoom(key) ? "Новый чат" : "чат " + key),
      kind: isWindowRoom(key) ? "window" : "telegram",
      live: false,
      count: 0,
      mtime: 0,
    };
    if (runIsLive(r.status, r)) room.live = true;
    room.count += 1;
    if (!byKey.has(key)) byKey.set(key, room);
  }
  return [...byKey.values()].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "window" ? -1 : 1;
    return b.mtime - a.mtime;
  });
}

export async function createRoom(title: string): Promise<Room> {
  const clean = title.trim() || "Новый чат";
  try {
    const r = await post<{ peer_id: string; title: string }>("/api/rooms", { title: clean });
    return { key: r.peer_id, name: r.title || clean, kind: "window", live: false, count: 0, mtime: Date.now() * 1e6 };
  } catch (e) {
    if (!unsupported(e)) throw e;
    S.roomsUnsupported = true;
    const row = { key: WINDOW_PREFIX + hex8(), name: clean, at: Date.now() };
    writeStub([...readStub(), row]);
    return { key: row.key, name: row.name, kind: "window", live: false, count: 0, mtime: row.at * 1e6, stub: true };
  }
}

export async function renameRoom(room: Room, title: string): Promise<void> {
  const clean = title.trim();
  if (!clean) return;
  if (room.stub) {
    writeStub(readStub().map((r) => (r.key === room.key ? { ...r, name: clean } : r)));
    room.name = clean;
    return;
  }
  try {
    await post("/api/rooms/" + encodeURIComponent(room.key), { title: clean });
    room.name = clean;
  } catch (e) {
    if (!unsupported(e)) throw e;
    S.roomsUnsupported = true;
    throw new Error("Эта программа агента ещё не умеет переименовывать чаты: нужна версия с контрактом комнат (0.3.3).");
  }
}

export async function deleteRoom(room: Room): Promise<void> {
  if (room.key === WINDOW_ROOM) throw new Error("Основной чат удалить нельзя — это голос агента здесь.");
  if (room.stub) {
    writeStub(readStub().filter((r) => r.key !== room.key));
    return;
  }
  try {
    await del("/api/rooms/" + encodeURIComponent(room.key));
  } catch (e) {
    if (!unsupported(e)) throw e;
    S.roomsUnsupported = true;
    throw new Error("Эта программа агента ещё не умеет убирать чаты: нужна версия с контрактом комнат (0.3.3).");
  }
}

/** Комнаты и прогоны одним чтением; отказ `/api/chats` не роняет список. */
export async function fetchRooms(): Promise<{ runs: Run[]; chats?: ChatRow[] }> {
  const [runs, chats] = await Promise.all([api<Run[]>("/api/runs?limit=200"), api<ChatRow[]>("/api/chats").catch(() => undefined)]);
  return { runs, chats };
}
