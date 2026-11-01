/** Attention still owns a durable job, but must expose a way to close it. */
export function updateOperationControls(stage: string) {
  const attention=stage==='attention';
  return {
    active:!['completed','failed','needs_resolution','cancelled','superseded'].includes(stage),
    cancellable:attention||['waiting_review','waiting_agent'].includes(stage),
    cancelLabel:attention?'Закрыть неудавшуюся попытку':'Отменить автопродолжение',
  };
}
