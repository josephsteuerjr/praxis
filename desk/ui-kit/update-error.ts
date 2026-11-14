import { t as uiText } from "../lang/lang";
/** Keep release failures tied to their operation, with the original reason. */
export function updateError(stage: 'download' | 'install', error: unknown): string {
  const raw = (error instanceof Error ? error.message : String(error ?? '')).trim();
  const step = stage === 'download' ? uiText("ui.ui.kit.update.error.1766743687") : uiText("ui.ui.kit.update.error.df2928736f");
  return uiText("ui.ui.kit.update.error.6432324a5e", {p0: String(step), p1: String(raw || uiText("ui.ui.kit.update.error.aaf727b786"))});
}
