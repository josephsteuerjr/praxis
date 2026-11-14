import { t as uiText } from "../lang/lang";
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
  const status = words.state === "running" ? uiText("ui.ui.kit.memory.line.ee4c71ffcf")
    : words.state === "queued" ? uiText("ui.ui.kit.memory.line.e5821c3c9c")
    : words.state === "starting" ? uiText("ui.ui.kit.memory.line.8200a40108")
    : words.state === "interrupted" ? uiText("ui.ui.kit.memory.line.735bd51fa8")
    : words.state === "unconfirmed" ? uiText("ui.ui.kit.memory.line.b2df9a3b8f")
    : words.state === "failed" ? uiText("ui.ui.kit.memory.line.4245c2a142")
    : words.state === "done" ? uiText("ui.ui.kit.memory.line.609c5ac413")
    : "";
  const counter = st.hot === null ? uiText("ui.ui.kit.memory.line.99c5b03249") : uiText("ui.ui.kit.memory.line.a6aa9390b8", {p0: String(words.quantity)});
  const text = [counter, status, st.paused ? uiText("ui.ui.kit.memory.line.a53faedcbe") : ""].filter(Boolean).join(" · ");
  return { text, note: words.note, canFold: words.canFold, state: words.state };
}

export function memoryLineSettingsAvailable(st: FoldState, localSettings: boolean) {
  return localSettings && st.owner_context === true;
}
