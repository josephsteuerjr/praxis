import { t as uiText } from "../lang/lang";
import {effortPlan, fastModeAvailability, type ModelDetails} from './providers';
import {serviceTierChoices} from './service-tier';

export interface ModelCatalog {
  codex?: boolean;
  model_details?: ModelDetails[];
}

/** An unsupported saved step is shown as a disabled receipt, never sent. */
export function setEffortOptions(select: HTMLSelectElement, choices: Array<{value:string;label:string}>, current: string) {
  select.replaceChildren(...choices.map(choice => new Option(choice.label, choice.value)));
  if (current && !choices.some(choice => choice.value === current)) {
    const stored = new Option(uiText("ui.ui.kit.model.controls.54e6d12c0b") + current, '__stored_effort__', true, true);
    stored.disabled = true;
    select.prepend(stored);
    select.dataset.storedEffort = current;
  } else {
    delete select.dataset.storedEffort;
    select.value = current;
  }
}

export function setTierOptions(select: HTMLSelectElement, available: boolean | null, current: string) {
  const choices = serviceTierChoices(current, uiText("ui.ui.kit.model.controls.cfab7eeca6"), available === true);
  select.replaceChildren(...choices.map(([value,label]) => new Option(label,value)));
  if (current && !choices.some(([value]) => value === current)) {
    const stored = new Option(uiText("ui.ui.kit.model.controls.e196cbb8d5"), '__stored_service_tier__', true, true);
    stored.disabled = true;
    select.prepend(stored);
    select.dataset.storedServiceTier = current;
  } else {
    delete select.dataset.storedServiceTier;
    select.value = current;
  }
}

export function bindBrainModelControls(root: HTMLElement, catalogs: Record<string, ModelCatalog>) {
  for (const form of root.querySelectorAll<HTMLElement>('[data-brain-form]')) {
    const framework = form.querySelector<HTMLInputElement | HTMLSelectElement>('[data-f=framework]');
    const model = form.querySelector<HTMLInputElement>('[data-f=model]');
    const effort = form.querySelector<HTMLSelectElement>('[data-f=reasoning_effort]');
    const tier = form.querySelector<HTMLElement>('[data-service-tier-field]');
    const tierSelect = tier?.querySelector<HTMLSelectElement>('[data-f=service_tier]');
    if (!framework || !model || !effort || !tier || !tierSelect) continue;
    const ordinary = Array.from(effort.options).map(option => ({value:option.value,label:option.label}));
    const sync = (event?: Event) => {
      if (event?.target === effort && effort.value !== '__stored_effort__') delete effort.dataset.storedEffort;
      const current = effort.dataset.storedEffort ?? effort.value;
      if (event?.target === tierSelect && tierSelect.value !== '__stored_service_tier__') delete tierSelect.dataset.storedServiceTier;
      const currentTier = tierSelect.dataset.storedServiceTier ?? tierSelect.value;
      const catalog = catalogs[framework.value];
      const original = framework.dataset.was === framework.value && form.dataset.codex === 'true';
      const codex = catalog?.codex === true || catalog?.codex === undefined && original;
      tier.hidden = !codex;
      const details = catalog?.model_details?.find(item => item.id === model.value.trim());
      let available = fastModeAvailability(details);
      if (available === null && original && model.value === model.dataset.was) {
        if (form.dataset.fastAvailable === 'true') available = true;
        if (form.dataset.fastAvailable === 'false') available = false;
      }
      setTierOptions(tierSelect, available, currentTier);
      const choices = codex ? effortPlan('chatgpt', model.value, '', details?.reasoning_efforts).chips : ordinary;
      setEffortOptions(effort, choices, current);
      let hint = form.querySelector<HTMLElement>('[data-codex-effort-hint]');
      if (!hint) {
        hint = document.createElement('p');hint.className='field-hint';hint.dataset.codexEffortHint='';
        effort.closest('label')?.after(hint);
      }
      hint.textContent = codex && current === 'none'
        ? uiText("ui.ui.kit.model.controls.7464edd852") : '';
      hint.hidden = !hint.textContent;
    };
    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    sync();
  }
}

export function brainModelProblem(form: HTMLElement | null) {
  const effort = form?.querySelector<HTMLSelectElement>('[data-f=reasoning_effort]');
  const changed = Array.from(form?.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[data-f=model],[data-f=framework]') || [])
    .some(field => field.value !== field.dataset.was);
  if (changed && effort?.dataset.storedEffort !== undefined) return uiText("ui.ui.kit.model.controls.8667cdb4e7");
  return '';
}

export function tierClearedByRoute(form: HTMLElement | null) {
  const framework = form?.querySelector<HTMLInputElement | HTMLSelectElement>('[data-f=framework]');
  const model = form?.querySelector<HTMLInputElement>('[data-f=model]');
  const tier = form?.querySelector<HTMLElement>('[data-service-tier-field]');
  const select = tier?.querySelector<HTMLSelectElement>('[data-f=service_tier]');
  const value = select?.dataset.storedServiceTier ?? select?.value;
  const changed = !!framework && framework.value !== framework.dataset.was || !!model && model.value !== model.dataset.was;
  return changed && ['priority','fast'].includes(value || '') && (tier?.hidden === true || select?.dataset.storedServiceTier !== undefined);
}
