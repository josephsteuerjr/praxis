import { t } from './lang';

/** Apply explicit product keys once, before conversations or file contents mount.
 * Never infer translations from text, walk user content, or observe later DOM.
 */
export function localizeMarkup(root: ParentNode = document): void {
  for (const node of root.querySelectorAll<HTMLElement>('[data-i18n-text]')) {
    node.textContent = t(node.dataset.i18nText!);
  }
  for (const attribute of ['title', 'aria-label', 'placeholder'] as const) {
    for (const node of root.querySelectorAll<HTMLElement>(`[data-i18n-${attribute}]`)) {
      node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`)!));
    }
  }
}
