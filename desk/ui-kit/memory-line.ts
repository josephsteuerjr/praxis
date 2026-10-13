import { foldWords, type FoldState } from "./memory-fold";

const KEY = "helene-hidden-context-rooms";
const volatileHidden = new Set<string>();

function hiddenRooms(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "[]");
    return [...new Set([...(Array.isArray(value) ? value.filter((v) => typeof v === "string") : []), ...volatileHidden])];
  } catch { return [...volatileHidden]; }
}

export const memoryLineHidden = (room: string) => hiddenRooms().includes(room);
export function hideMemoryLine(room: string) {
  try {
    localStorage.setItem(KEY, JSON.stringify([...new Set([...hiddenRooms(), room])].slice(-500)));
    volatileHidden.clear(); // Storage is authoritative, including restores in another tab.
  } catch { volatileHidden.add(room); }
}
export function showMemoryLines() {
  volatileHidden.clear();
  try { localStorage.removeItem(KEY); } catch { /* unavailable storage */ }
}

export function memoryLineWords(st: FoldState, now = Date.now()) {
  const words = foldWords(st, now);
  const status = words.state === "running" ? "Память сворачивается…"
    : words.state === "queued" ? "Свёртка запрошена"
    : words.state === "starting" ? "Свёртка запускается…"
    : words.state === "interrupted" ? "Свёртка прервана"
    : words.state === "unconfirmed" ? "Свёртка не подтверждена"
    : words.state === "failed" ? "Свёртка не завершилась"
    : words.state === "done" ? "Память свёрнута"
    : "";
  const counter = st.hot === null ? "Память пока недоступна" : words.counter.replace("Горячая память:", "В памяти:");
  const text = [counter, status, st.paused ? "Агент на паузе" : ""].filter(Boolean).join(" · ");
  return { text, note: words.note, canFold: words.canFold, state: words.state };
}

export function memoryLineSettingsAvailable(st: FoldState, localSettings: boolean) {
  return localSettings && st.owner_context === true;
}
