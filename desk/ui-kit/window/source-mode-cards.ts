import { el } from './lib';

export type SourceMode = 'clean' | 'preserve';
let sourceModeSequence = 0;

/** Native radios keep click, Tab, arrows and Space on the same paper cards. */
export function sourceModeCards(onChange: (value: SourceMode) => void) {
  const root = el('fieldset', 'source-mode-cards');
  root.append(el('legend', 'field-label', 'Код программы'));
  const grid = el('div', 'choice source-mode-grid');
  const name = `source-mode-${++sourceModeSequence}`;
  const rows = (['clean', 'preserve'] as const).map(value => {
    const label = el('label', 'choice-item source-mode-card');
    label.dataset.sourceMode = value;
    const heading = el('span', 'source-mode-heading');
    const input = el('input');
    input.type = 'radio'; input.name = name; input.value = value;
    input.setAttribute('aria-describedby', 'update-source-mode-hint');
    heading.append(input, el('span', 'choice-title', value === 'clean' ? 'Чистый код' : 'Сохранить правки'));
    const text = el('span', 'choice-text', value === 'clean'
      ? 'Установить исходники нового выпуска. Локальные правки кода не переносятся.'
      : 'Перенести изменения агента в новую версию с проверкой результата и ревью.');
    const state = el('span', 'source-mode-state');
    label.append(heading, text, state); grid.append(label);
    input.addEventListener('change', () => {
      if (!input.checked || input.disabled || root.disabled) return;
      sync(); onChange(value);
    });
    return { value, label, input, state };
  });
  const sync = () => {
    for (const row of rows) {
      row.label.dataset.selected = String(row.input.checked);
      row.state.textContent = row.input.disabled ? 'Недоступно в этой установке'
        : row.input.checked ? root.disabled ? 'Выбрано для этой попытки' : 'Выбрано' : 'Выбрать';
    }
  };
  root.append(grid);
  return {
    el: root,
    configure(available: readonly string[], selected?: SourceMode, locked = false) {
      root.disabled = locked;
      for (const row of rows) {
        row.input.disabled = !available.includes(row.value);
        row.input.checked = selected === row.value;
      }
      sync();
    },
    lock() { root.disabled = true; sync(); },
  };
}
