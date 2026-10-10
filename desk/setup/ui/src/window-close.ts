import type { Window } from "@tauri-apps/api/window";

// onCloseRequested сам вызывает destroy после обработчика. Забираем закрытие
// себе, чтобы отказ команды тоже остался в сцене, а не в unhandledrejection.
export function setupWindowClose(win: Window | null, blocked: () => boolean, failed: () => void) {
  if (win) void win.onCloseRequested(async event => {
    event.preventDefault();
    if (blocked()) return;
    try { await win.destroy(); }
    catch { failed(); }
  }).catch(failed);
  return async () => {
    if (!win || blocked()) return;
    try { await win.close(); }
    catch { failed(); }
  };
}
