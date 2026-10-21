import { shell } from './api';
import { button, card, el } from '../dom';

type Component = { id: string; ready: boolean; installed: boolean; error?: string; source?: string; toolchain?: string };

/** The startup view and Settings use the same component controls. */
export function componentsCard(): HTMLElement {
  const body = el('div');
  const note = el('p', 'field-hint', 'Python и окно обновляются отдельно. Встроенное реле и его исходники входят в приложение; набор для пересборки можно скачать отдельно — для обычного запуска он не нужен.');
  const rows = el('div');
  const receipt = el('p', 'receipt');
  const render = async () => {
    try {
      const components = await shell<Component[]>('components_status');
      rows.replaceChildren();
      for (const item of components) {
        const row = el('div', 'actions');
        if (item.id !== 'relay') continue;
        row.append(el('span', '', `Встроенное реле: ${item.installed ? 'установлено' : item.ready ? 'готово к установке' : 'комплект недоступен'}`));
        if (item.ready && !item.installed) {
          const install = button('Установить', 'quiet', async () => {
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
