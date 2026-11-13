/** Observable lifecycle contracts. No timers, DOM or inferred text state here. */
export interface OwnerState {
  agent_id?: string;
  supported: boolean;
  stopped: boolean;
  runner_alive: boolean | null;
  pid: number;
  note: string;
  /** Флаг из конфига ЭТОГО агента (1.4.1): снятый агент — не «сломанный
   *  движок», а погашенный. Старая оболочка поля не шлёт — undefined. */
  enabled?: boolean;
  /** Служебная ли установка (1.4.1): гашение/подъём исполняет служба, и окно
   *  говорит об этом вместо молчаливого «горит». */
  service?: boolean;
}

export interface EngineOperation {
  agent_id?: string;
  action: "stop" | "resume" | "restart";
  started: number;
  previousPid: number;
  accepted: boolean;
  acceptedAt?: number;
}

export function engineOperationObserved(op: EngineOperation, owner: OwnerState | null,
    channelReady: boolean, readerAlive: boolean, pid: number, stateReadStarted = 0): boolean {
  if (!op.accepted) return false;
  if (op.agent_id && owner?.agent_id && op.agent_id !== owner.agent_id) return false;
  if (op.action === "stop") return owner?.stopped === true && owner.runner_alive === false;
  if (stateReadStarted < (op.acceptedAt ?? op.started)) return false;
  if (owner?.stopped || !channelReady || !readerAlive) return false;
  if (owner?.supported && (owner.runner_alive !== true || owner.pid !== pid)) return false;
  if (op.action === "restart") return op.previousPid > 0 && pid > 0 && pid !== op.previousPid;
  return true;
}

export function engineWords(owner: OwnerState | null, op: EngineOperation | null): string {
  // Restart — не мгновенное действие: движок принимает просьбу и выходит на
  // границе ТЕКУЩЕГО хода (06.10, живая жалоба владельца «харнесс не
  // торопится»). Молчаливое «Перезапускается…» в это время неотличимо от
  // зависания — говорим прямо, чего ждём.
  if (op) return op.action === "stop" ? "Останавливается…"
    : op.action === "resume" ? "Запускается…" : "Перезапускается — ждёт границы текущего хода";
  // Снятый агент (1.4.1, живой случай 06.10): флаг enabled=false в конфиге, а
  // процессы при служебной установке держит служба до своего перезапуска.
  // Прежде окно молчало — кнопка «Остановить движок» выглядела сломанной, хотя
  // движок честно жив. Говорим состояние словами, и объясняем, кто исполнит.
  if (owner && owner.enabled === false && !owner.stopped) {
    return owner.service
      ? "Агент снят в настройках; его держит служба — отпустит перезапуск службы («Система» → «Перезапустить»)"
      : "Агент снят в настройках — движок не поднимется, пока не вернёшь галочку";
  }
  if (owner?.stopped) return owner.runner_alive === false ? "Движок остановлен"
    : owner.runner_alive === true ? "Останавливается…" : "Остановка включена";
  return "";
}

export interface CurrentActivity {
  run_id: string;
  chat_id: string;
  kind: string;
  phase: string;
  tool: string;
}

export function activityWords(activity: CurrentActivity | null | undefined): string {
  if (!activity) return "";
  return activity.phase === "model" ? "Думает..." : "Работает...";
}

export function sameRoom(a: string, b: string, defaultRoom: string, legacyRoom: string): boolean {
  return a === b || ((a === defaultRoom || a === legacyRoom) && (b === defaultRoom || b === legacyRoom));
}

export function interruptReceiptMatches(id: string, receipt: Record<string, unknown> | null | undefined): boolean {
  return !!id && receipt?.id === id;
}
