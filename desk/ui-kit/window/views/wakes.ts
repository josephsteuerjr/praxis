import { t as uiText } from "../../../lang/lang";
// «Вейки» — пробуждения по расписанию (kind=wake): что агент делал, когда его
// никто не звал. Отдельно от чатов и задач (слово владельца 07.09).
import { api } from "../api";
import { esc, fmtTime } from "../lib";
import { S, type Run } from "../state";
import { bindRuns, runRowHTML } from "../runlist";

export async function render(container: HTMLElement): Promise<void> {
  const rows = await api<Run[]>("/api/runs?kind=wake&limit=60");
  const next = S.agentState?.next_wake;
  container.innerHTML = uiText("ui.ui.kit.window.views.wakes.c74a2e92dd", {p0: String(next ? uiText("ui.ui.kit.window.views.wakes.b432327026", {p0: String(esc(fmtTime(next) || next))}) : ""), p1: String(rows.length), p2: String(rows.map((r) => runRowHTML(r)).join("") || uiText("ui.ui.kit.window.views.wakes.d308c0f245"))});
  bindRuns(container, () => void render(container), () => undefined);
}
