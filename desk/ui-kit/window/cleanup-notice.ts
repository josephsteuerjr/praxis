/** An update may finish after this window has opened. Keep its leftovers visible. */
export interface CleanupStatus { id?: string; messages: string[]; receipt?: string }
export function cleanupNotice(host: HTMLElement, check: () => Promise<CleanupStatus>, intervalMs = 30000) {
  let alive = true, pending = false;
  host.className = 'release-notice'; host.hidden = true; host.setAttribute('role', 'status');
  const details = document.createElement('details');
  const title = document.createElement('summary');
  title.textContent = 'Обновление установлено. Остались прежние файлы';
  const explanation = document.createElement('p');
  explanation.textContent = 'Новая Hélène работает. Защищённые файлы сохранены без запроса прав администратора. Прежнюю программу из этих папок не запускай.';
  const list = document.createElement('ul');
  const receipt = document.createElement('p');
  details.append(title, explanation, list, receipt); host.append(details);
  let identity = '';
  const refresh = async () => {
    if (!alive || pending || document.hidden) return;
    pending = true;
    try {
      const value = await check();
      if (!alive) return;
      const messages = Array.isArray(value.messages) ? value.messages.filter(x => typeof x === 'string') : [];
      host.hidden = messages.length === 0;
      const next = JSON.stringify([value.id, messages, value.receipt]);
      if (next !== identity) {
        identity = next; list.replaceChildren();
        for (const message of messages) {
          const row = document.createElement('li'); row.textContent = message; list.append(row);
        }
        receipt.textContent = value.receipt ? `Отчёт: ${value.receipt}` : '';
      }
    } catch { /* An unavailable local bridge never interrupts the running app. */ }
    finally { pending = false; }
  };
  const visible = () => { if (!document.hidden) void refresh(); };
  document.addEventListener('visibilitychange', visible);
  const timer = window.setInterval(visible, intervalMs);
  void refresh();
  return { refresh, dispose: () => { alive = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); host.remove(); } };
}
