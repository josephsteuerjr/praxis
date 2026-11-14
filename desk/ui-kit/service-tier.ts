import { t as uiText } from "../lang/lang";
export const FAST_MODE_HINT = () => uiText("ui.ui.kit.service.tier.e8081661d3");

/** Keep a legacy value selectable until its owner explicitly changes it. */
export function serviceTierChoices(current: string, inherited = uiText("ui.ui.kit.service.tier.cfab7eeca6"), fastAvailable = true): Array<[string, string]> {
  const choices: Array<[string, string]> = [
    ['', inherited],
    ['default', uiText("ui.ui.kit.service.tier.7c0ef1469b")],
  ];
  if (fastAvailable) choices.push([current === 'fast' ? 'fast' : 'priority', 'Fast mode']);
  if (current && !['fast','priority'].includes(current) && !choices.some(([value]) => value === current)) {
    choices.push([current, uiText("ui.ui.kit.service.tier.7d451f2a29") + current]);
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
  if (value === 'default') return uiText("ui.ui.kit.service.tier.2b02acb14e");
  return value ? String(value) : uiText("ui.ui.kit.service.tier.74c72fe320");
}

export function serviceTierReceipt(actual: unknown) {
  return actual ? uiText("ui.ui.kit.service.tier.6b28f1f93f") + serviceTierLabel(actual) : uiText("ui.ui.kit.service.tier.3abd259c90");
}
