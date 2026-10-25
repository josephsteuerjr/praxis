import { shell } from './api';
import { button, card, el } from '../dom';

type Component = { id: string; ready: boolean; installed: boolean; error?: string; source?: string; toolchain?: string; download_bytes?: number };

/** The startup view and Settings use the same component controls. */
export function componentsCard(): HTMLElement {
  const body = el('div');
  const note = el('p', 'field-hint', 'Голосовые компоненты устанавливаются по желанию. praxis-relay — отдельный продукт под лицензией MIT; скачивается только когда нужен. Инструменты сборки не требуются.');
  const rows = el('div');
  const receipt = el('p', 'receipt');
  const render = async () => {
    try {
      const components = await shell<Component[]>('components_status');
      rows.replaceChildren();
      for (const item of components) {
        const row = el('div', 'actions');
        const names: Record<string, string> = {relay:'praxis-relay · MIT', xtts:'XTTS · CPML, некоммерческая лицензия', whisper:'Whisper · распознавание речи'};
        row.append(el('span', '', `${names[item.id] || item.id}: ${item.installed ? 'установлено' : item.ready ? 'можно установить' : 'комплект недоступен'}`));
        if (!item.installed && item.download_bytes) row.append(el('span', 'field-hint', `${(item.download_bytes / 1024 / 1024).toFixed(0)} МБ · проверенный кеш используется повторно`));
        if (item.ready && !item.installed) {
          const install = button(item.id === 'relay' ? 'Установить' : 'Скачать и установить', 'quiet', async () => {
            install.disabled = true;
            try {
              await shell('component_install', { name: item.id });
              receipt.textContent = 'Компонент установлен. Перезапусти приложение для его использования.';
              await render();
            } catch (e) { receipt.textContent = String(e); install.disabled = false; }
          });
          row.append(install);
        }
        if (item.error) row.append(el('p', 'receipt err', item.error));
        rows.append(row);
      }
    } catch (e) { receipt.textContent = String(e); }
  };
  body.append(note, rows, receipt);
  void render();
  return card('Компоненты приложения', body);
}
