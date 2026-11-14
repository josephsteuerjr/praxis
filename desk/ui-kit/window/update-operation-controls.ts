import { t as uiText } from "../../lang/lang";
/** Attention still owns a durable job, but must expose a way to close it. */
export function updateOperationControls(stage: string) {
  const attention=stage==='attention';
  return {
    active:!['completed','failed','needs_resolution','cancelled','superseded'].includes(stage),
    cancellable:attention||['waiting_review','waiting_agent'].includes(stage),
    cancelLabel:attention?uiText("ui.ui.kit.window.update.operation.controls.1c45a130c3"):uiText("ui.ui.kit.window.update.operation.controls.326245a16e"),
  };
}
