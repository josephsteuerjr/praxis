export interface TrayOwner {
  agent_id?: string;
  supported?: boolean;
  stopped?: boolean;
  runner_alive?: boolean | null;
  enabled?: boolean;
  service?: boolean;
}
export interface TrayAgent { id: string; name: string; current?: boolean; enabled?: boolean; conflict?: string }
export interface TrayState { background: boolean | null; owner: TrayOwner; agents: TrayAgent[]; current: string; max_height?: number | null }

export function trayStatus(owner: TrayOwner): { text: string; tone: string } {
  if (owner.stopped || owner.enabled === false) return { text: 'Остановлен', tone: 'quiet' };
  if (owner.runner_alive === true) return { text: 'Движок работает', tone: 'ok' };
  if (owner.runner_alive === false) return { text: 'Не запущен', tone: 'quiet' };
  return { text: 'Состояние уточняется', tone: 'quiet' };
}

export function exitNote(state: TrayState): string {
  if (state.background === null) return 'Состояние фоновой работы пока неизвестно.';
  if (!state.background) return 'Выход остановит запущенных агентов.';
  return 'Фоновая работа останется включённой. Запущенные агенты продолжат принимать сообщения и выполнять задачи.';
}

export function engineCommand(owner: TrayOwner): { cmd: string; args: Record<string, unknown> } | null {
  if (!owner.supported) return null;
  if (owner.enabled === false && owner.service && owner.agent_id) {
    return { cmd: 'agent_enabled_set', args: { id: owner.agent_id, enabled: true } };
  }
  if (!owner.stopped && owner.runner_alive === false) return { cmd: 'engine_restart', args: {} };
  return { cmd: 'owner_control', args: { action: owner.stopped ? 'resume' : 'panic' } };
}
