import { t as uiText } from "../../lang/lang";
import { el } from './lib';

export type SourceMode = 'clean' | 'preserve';
let sourceModeSequence = 0;

/** Native radios keep click, Tab, arrows and Space on the same paper cards. */
export function sourceModeCards(onChange: (value: SourceMode) => void) {
  const root = el('fieldset', 'source-mode-cards');
  root.append(el('legend', 'field-label', uiText("ui.ui.kit.window.source.mode.cards.ee2b81adfa")));
  const grid = el('div', 'choice source-mode-grid');
  const name = `source-mode-${++sourceModeSequence}`;
  const rows = (['clean', 'preserve'] as const).map(value => {
    const label = el('label', 'choice-item source-mode-card');
    label.dataset.sourceMode = value;
    const heading = el('span', 'source-mode-heading');
    const input = el('input');
    input.type = 'radio'; input.name = name; input.value = value;
    input.setAttribute('aria-describedby', 'update-source-mode-hint');
    heading.append(input, el('span', 'choice-title', value === 'clean' ? uiText("ui.ui.kit.window.source.mode.cards.087f363519") : uiText("ui.ui.kit.window.source.mode.cards.df5e7863ff")));
    const text = el('span', 'choice-text', value === 'clean'
      ? uiText("ui.ui.kit.window.source.mode.cards.fe0ff11387")
      : uiText("ui.ui.kit.window.source.mode.cards.30e397385e"));
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
      row.state.textContent = row.input.disabled ? uiText("ui.ui.kit.window.source.mode.cards.e045908c0c")
        : row.input.checked ? root.disabled ? uiText("ui.ui.kit.window.source.mode.cards.8ececa03ff") : uiText("ui.ui.kit.window.source.mode.cards.9cf249e2a8") : uiText("ui.ui.kit.window.source.mode.cards.38aa0ac1ff");
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
