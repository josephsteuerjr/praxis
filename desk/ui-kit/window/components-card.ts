import { shell } from './api';
import { button, card, el } from '../dom';

type Progress = { active?: boolean; state?: string; stage?: string; bytes?: number; total_bytes?: number; files?: number; files_done?: number; total_files?: number; error?: string };
type Component = { id: string; ready: boolean; installed: boolean; restart_required?:boolean; error?: string; source?: string; toolchain?: string; download_bytes?: number; progress?: Progress };

export function componentProgress(value: Progress): { label: string; fraction?: number } {
  const labels: Record<string, string> = { starting: 'Подготовка', preparing: 'Проверка комплекта', downloading: 'Скачивание', unpacking: 'Распаковка', extracting: 'Распаковка', installing: 'Установка', completed: 'Установлено' };
  let label = labels[value.stage || ''] || 'Установка';
  let done: number | undefined, total: number | undefined;
  if (value.stage === 'downloading' && value.total_bytes) {
    done = value.bytes || 0; total = value.total_bytes;
    label += ` · ${(done / 1048576).toFixed(1)} из ${(total / 1048576).toFixed(1)} МБ`;
  } else if (value.total_files) {
    done = value.files_done ?? value.files ?? 0; total = value.total_files;
    label += ` · ${done} из ${total} файлов`;
  }
  return { label, fraction: total && done !== undefined ? Math.max(0, Math.min(1, done / total)) : undefined };
}

/** The startup view and Settings use the same component controls. */
export function componentsCard(): HTMLElement {
  const body = el('div');
  const note = el('p', 'field-hint', 'Голосовые компоненты устанавливаются по желанию. praxis-relay — отдельный продукт под лицензией MIT; скачивается только когда нужен. Инструменты сборки не требуются.');
  const rows = el('div');
  const receipt = el('p', 'receipt');
  const entries = new Map<string, { row: HTMLElement; label: HTMLElement; detail: HTMLElement; bar: HTMLProgressElement; install: HTMLButtonElement; error: HTMLElement }>();
  const starting = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (body.isConnected) void render(); }, 1000);
  };
  const render = async () => {
    try {
      const components = await shell<Component[]>('components_status');
      for (const item of components) {
        let entry = entries.get(item.id);
        if (!entry) {
          const row = el('div', 'component-install');
          const label = el('span'), detail = el('p', 'field-hint'), bar = el('progress');
          bar.max = 1;
          const error = el('p', 'receipt err');
          const install = button('', 'quiet', async () => {
            starting.add(item.id); install.disabled = true; detail.textContent = 'Подготовка'; receipt.textContent = '';
            try {
              const result = await shell<Progress>('component_install', { name: item.id });
              receipt.textContent = result?.state === 'running' ? 'Установка началась.' : 'Компонент установлен.';
            } catch (e) { receipt.textContent = String(e); }
            finally { starting.delete(item.id); await render(); }
          });
          row.append(label, detail, bar, install, error); rows.append(row);
          entry = { row, label, detail, bar, install, error }; entries.set(item.id, entry);
        }
        const names: Record<string, string> = {relay:'praxis-relay · MIT', xtts:'XTTS · CPML, некоммерческая лицензия', whisper:'Whisper · распознавание речи','server-bridge':'Серверный мост'};
        const active = starting.has(item.id) || item.progress?.active === true;
        const caption = componentProgress(item.progress || {});
        entry.label.textContent = `${names[item.id] || item.id} · ${active ? 'устанавливается' : item.installed ? 'установлено' : item.ready ? 'можно установить' : 'комплект недоступен'}`;
        entry.detail.textContent = active ? caption.label : item.installed ? item.restart_required===false ? 'Готово. Перезапуск не нужен.' : 'Готово. Для применения перезапусти приложение.' : item.download_bytes ? `${(item.download_bytes / 1048576).toFixed(0)} МБ · проверенный кеш используется повторно` : '';
        entry.bar.hidden = !active;
        if (caption.fraction === undefined) entry.bar.removeAttribute('value'); else entry.bar.value = caption.fraction;
        entry.install.hidden = item.installed || !item.ready;
        entry.install.disabled = active || item.progress?.state === 'attention';
        entry.install.textContent = active ? 'Устанавливается…' : item.progress?.error ? 'Повторить' : item.id === 'relay' ? 'Установить' : 'Скачать и установить';
        entry.error.textContent = item.error || item.progress?.error || '';
      }
      if (components.some(item => item.progress?.active) || starting.size) schedule();
    } catch (e) { receipt.textContent = String(e); }
  };
  body.append(note, rows, receipt);
  void render();
  return card('Компоненты приложения', body);
}
