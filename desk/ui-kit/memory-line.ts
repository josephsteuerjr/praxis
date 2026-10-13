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
  const text = words.state === "running" ? "Память сворачивается…"
    : words.state === "failed" ? "Свёртка не завершилась"
    : words.state === "done" ? "Память свёрнута"
    : st.hot === null ? "Память пока недоступна"
    : words.counter.replace("Горячая память:", "В памяти:");
  return { text, note: words.note, canFold: words.canFold, state: words.state };
}
