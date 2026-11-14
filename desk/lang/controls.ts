import { el } from '../ui-kit/dom';
import { LANGS, t, type Lang } from './lang';

export function languageField(label: string, value: string, changed: (value: Lang | '') => void,
                              automatic = false): HTMLLabelElement {
  const wrap = el('label', 'field');
  wrap.append(el('span', 'field-label', label));
  const input = el('select', 'field-input');
  const rows = automatic ? [{id:'',label:t('language.automatic')}, ...LANGS] : LANGS;
  for (const row of rows) {
    const option = el('option', '', row.label);option.value = row.id;input.append(option);
  }
  input.value = value;
  input.addEventListener('change', () => changed(input.value as Lang | ''));
  wrap.append(input);return wrap;
}
