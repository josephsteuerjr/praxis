export const FAST_MODE_HINT = 'Fast mode увеличивает расход лимитов подписки или кредитов. Доступность ускорения определяет Codex.';

/** Keep a legacy value selectable until its owner explicitly changes it. */
export function serviceTierChoices(current: string, inherited = 'По настройке провайдера', fastAvailable = true): Array<[string, string]> {
  const choices: Array<[string, string]> = [
    ['', inherited],
    ['default', 'Обычный'],
  ];
  if (fastAvailable) choices.push([current === 'fast' ? 'fast' : 'priority', 'Fast mode']);
  if (current && !['fast','priority'].includes(current) && !choices.some(([value]) => value === current)) {
    choices.push([current, 'Сохранённое значение · ' + current]);
  }
  return choices;
}

export function collectServiceTier(model: {service_tier?: string}, value: string, changed: boolean) {
  if (!changed) return;
  if (value) model.service_tier = value === 'fast' ? 'priority' : value;
  else delete model.service_tier;
}

export function serviceTierLabel(value: unknown) {
  if (value === 'priority' || value === 'fast') return 'Fast mode';
  if (value === 'default') return 'обычный режим';
  return value ? String(value) : 'провайдер не сообщил';
}

export function serviceTierReceipt(actual: unknown) {
  return actual ? 'Последний ответ · ' + serviceTierLabel(actual) : 'Последний ответ · режим обработки неизвестен';
}
