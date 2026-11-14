import { t as uiText } from "../lang/lang";
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
  if (owner.stopped || owner.enabled === false) return { text: uiText("ui.ui.kit.tray.state.39e74d43c1"), tone: 'quiet' };
  if (owner.runner_alive === true) return { text: uiText("ui.ui.kit.tray.state.852f1440fa"), tone: 'ok' };
  if (owner.runner_alive === false) return { text: uiText("ui.ui.kit.tray.state.415b29cc9d"), tone: 'quiet' };
  return { text: uiText("ui.ui.kit.tray.state.d0c0f49919"), tone: 'quiet' };
}

export function exitNote(state: TrayState): string {
  if (state.background === null) return uiText("ui.ui.kit.tray.state.3b44ef7af5");
  if (!state.background) return uiText("ui.ui.kit.tray.state.cf59bb7ddb");
  return uiText("ui.ui.kit.tray.state.3383846d33");
}

export function engineCommand(owner: TrayOwner): { cmd: string; args: Record<string, unknown> } | null {
  if (!owner.supported) return null;
  if (owner.enabled === false && owner.service && owner.agent_id) {
    return { cmd: 'agent_enabled_set', args: { id: owner.agent_id, enabled: true } };
  }
  if (!owner.stopped && owner.runner_alive === false) return { cmd: 'engine_restart', args: {} };
  return { cmd: 'owner_control', args: { action: owner.stopped ? 'resume' : 'panic' } };
}
