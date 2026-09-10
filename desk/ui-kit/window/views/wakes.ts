// «Вейки» — пробуждения по расписанию (kind=wake): что агент делал, когда его
// никто не звал. Отдельно от чатов и задач (слово владельца 07.09).
import { api } from "../api";
import { esc, fmtTime } from "../lib";
import { S, type Run } from "../state";
import { bindRuns, runRowHTML } from "../runlist";

export async function render(container: HTMLElement): Promise<void> {
  const rows = await api<Run[]>("/api/runs?kind=wake&limit=60");
  const next = S.agentState?.next_wake;
  container.innerHTML = `<div class="center">
    ${next ? `<div class="now-idle"><span class="dot"></span><span>Следующее пробуждение: ${esc(fmtTime(next) || next)}</span></div>` : ""}
    <h3 class="section-title">Пробуждения <span class="muted">${rows.length}</span></h3>
    <p class="muted" style="margin:-4px 0 12px">Ход по будильнику или по расписанию: без сообщения снаружи. Раскрой строку — шаги хода.</p>
    ${rows.map((r) => runRowHTML(r)).join("") || '<div class="empty"><b>Пробуждений ещё не было</b>Появятся, когда сработает будильник агента.</div>'}
  </div>`;
  bindRuns(container, () => void render(container), () => undefined);
}
