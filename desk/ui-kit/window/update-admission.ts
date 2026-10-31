// One durable admission before network/model work; the view owns no updater.
export interface UpdateAdmission { state: 'admitted'; job: {id: string}; observer_may_close: true }
export async function requestAgentUpdate(
  invoke: (command: string, args: Record<string, unknown>) => Promise<unknown>,
  url: string, sha256: string, sourceMode?: 'preserve' | 'clean',
): Promise<UpdateAdmission> {
  const value = await invoke('update_start', {url, sha256, ...(sourceMode ? {sourceMode} : {})}) as Partial<UpdateAdmission> | null;
  if (!value || value.state !== 'admitted' || value.observer_may_close !== true || !value.job?.id) {
    throw Error('Долговечное принятие обновления не подтверждено; повторная отправка не выполнена.');
  }
  return value as UpdateAdmission;
}
