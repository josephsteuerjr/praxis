// Owner Forge viewer. Persisted observations are not process-liveness probes.
import { ApiError, channelURL } from "../api";

export interface ForgeRef { kind: "payload" | "result" | "request"; sha256: string; size_bytes?: number }
export interface ForgeUnit {
  id: string; parent_unit_id?: string; depth?: number; ancestry_unknown?: boolean;
  role?: string; model?: string; subtask?: string; status?: string; request_status?: string;
  custody?: string; current_disposition?: string; stale_dispositions?: number;
  observation?: { generation?: string; phase?: string; tool?: string; observed_at?: string; iteration?: number };
  result_ref?: ForgeRef; request_ref?: ForgeRef; payload_refs?: Record<string, ForgeRef>;
}
export interface ForgeTask { id: string; goal?: string; status?: string; agents: ForgeUnit[]; max_active_workers?: number; control?: {action?: string} }
interface EventRow { seq: number; kind: string; at?: string; tool?: string; generation?: string; input_ref?: ForgeRef; output_ref?: ForgeRef }
interface EventPage { events: EventRow[]; next_offset: number; next_after_seq: number; has_more: boolean; pending_tail?: boolean; generation: string }
interface PayloadPage { sha256: string; text: string; offset: number; total_chars: number; next_offset: number | null }
interface Command { command_id: string; state: string; task_id?: string; agent_id?: string; action?: string; reason?: string; result?: unknown }
type Read = <T>(path: string, signal: AbortSignal, body?: unknown) => Promise<T>;

const STATUS: Record<string, string> = { active: "в работе", running: "выполняется", queued: "в очереди", pending: "в очереди", done: "завершено", completed: "завершено", paused: "на паузе", cancelled: "отменено", stopped: "остановлено", failed: "ошибка", error: "ошибка", unknown: "состояние не подтверждено", abandoned: "оставлено", awaiting_acceptance: "ждёт приёмки" };
const PHASE: Record<string, string> = { starting: "начало", model: "ожидание модели", tool: "инструмент", waiting: "ожидание", finishing: "сохранение результата", finished: "результат сохранён", ready: "граница продолжения", stopped: "остановка по записи", error: "ошибка по записи", checkpoint: "контрольная точка", queued: "очередь" };
const ACCEPTED: Record<string, string> = { integrated: "принят", irrelevant: "не нужен", deferred: "отложен" };
const label = (v = "") => STATUS[v] || v || "нет записи";
const el = (tag: string, cls = "", text = ""): HTMLElement => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const button = (title: string, action: () => void) => {
  const node = document.createElement("button"); node.type = "button"; node.className = "btn"; node.textContent = title;
  node.addEventListener("click", action); return node;
};
const query = (path: string, data: Record<string, string | number>) => path + "?" + new URLSearchParams(Object.entries(data).map(([k,v]) => [k, String(v)]));
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);

// HTTP deliberately: AbortController owns each mounted read (the shared tunnel
// has no cancellation API). Same authenticated URL and route authorization.
export const readForge: Read = async <T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> => {
  const response = await fetch(channelURL(path), { signal, cache: "no-store", method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
  return response.json();
};

interface UnitView {
  details: HTMLDetailsElement; title: HTMLElement; meta: HTMLElement; brief: HTMLElement;
  controls: HTMLElement; refs: HTMLElement; log: HTMLElement; logStatus: HTMLElement; more: HTMLButtonElement;
  content: HTMLElement; contentMeta: HTMLElement; contentMore: HTMLButtonElement;
  unit: ForgeUnit; task: ForgeTask; generation: string; eventsLoaded: boolean; eventOffset: number;
  hasMore: boolean; eventBusy: boolean; eventSeq: Set<number>; contentRef?: ForgeRef; contentOffset: number;
  contentBusy: boolean; contentEpoch: number;
}
interface TaskView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; goal: HTMLElement; controls: HTMLElement; commands: HTMLElement; units: HTMLElement; views: Map<string, UnitView>; task: ForgeTask }

/** Exported controller is also the production-fragment test seam. */
export function forgePanel(root: HTMLElement, read: Read = readForge, pollMs = 2500) {
  const message = el("p", "muted", "Читаю записи Forge…"); message.setAttribute("role", "status");
  const teams = el("div", "forge-teams");
  const refreshButton = button("Обновить", () => void refresh());
  const heading = el("div", "forge-heading"); heading.append(el("h3", "section-title", "Команды разработки"), refreshButton);
  root.replaceChildren(heading, message, teams);
  const tasks = new Map<string, TaskView>();
  const controllers = new Set<AbortController>();
  let running = false, epoch = 0, timer: ReturnType<typeof setTimeout> | undefined, busy = false;
  const pending = new Map<string, { command: Command; feedback: HTMLElement }>();
  const valid = (captured: number) => running && captured === epoch && root.isConnected;
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const controller = new AbortController(); controllers.add(controller);
    try { return await read<T>(path, controller.signal, body); }
    finally { controllers.delete(controller); }
  }
  function commandText(c: Command): string {
    if (c.state === "queued") return "Просьба в очереди; действие ещё не подтверждено.";
    if (c.state === "processing") return "Forge принял просьбу; результат ещё не записан.";
    if (c.state === "unknown") return "Результат управления неизвестен. " + (c.reason || "Автоматического повтора нет.");
    if (c.state === "rejected") return "Просьба отклонена: " + (c.reason || "см. запись");
    return "Получена расписка Forge: " + JSON.stringify(c.result ?? c.state);
  }
  async function control(task: ForgeTask, unit: ForgeUnit | undefined, action: string, target: HTMLElement) {
    const captured = epoch;
    const key = task.id + "/" + (unit?.id || "");
    if (pending.has(key)) return;
    const command: Command = { command_id: crypto.randomUUID().replaceAll("-", ""), state: "queued", task_id: task.id, agent_id: unit?.id || "", action };
    pending.set(key, { command, feedback: target });
    target.textContent = "Передаю просьбу…";
    try {
      const response = await request<Command>("/api/forge/control", { ...command, expected_generation: unit?.observation?.generation || "" });
      if (!valid(captured)) return;
      if (response.command_id !== command.command_id) throw Error("Расписка не совпала с просьбой управления");
      pending.set(key, { command: response, feedback: target }); target.textContent = commandText(response);
    } catch (e) {
      if (!valid(captured)) return;
      // Keep exact ID: uncertain delivery must not silently become another stop.
      command.state = "unknown";
      target.textContent = "Результат передачи не подтверждён: " + errorText(e) + ". Обновление прочитает эту же просьбу, не повторяя её.";
    }
  }
  function controls(current: () => [ForgeTask, ForgeUnit | undefined], host: HTMLElement) {
    host.replaceChildren();
    const feedback = el("p", "muted"); feedback.setAttribute("role", "status");
    const [task, unit] = current();
    const terminal = ["done", "completed", "cancelled", "abandoned"].includes(task.status || "");
    const row = el("div", "forge-actions");
    for (const [action, title] of [["pause", "Приостановить"], ["stop", unit ? "Остановить участника" : "Остановить группу"], ["resume", "Возобновить"]]) {
      const b = button(title, () => { const [task, unit] = current(); void control(task, unit, action, feedback); });
      // Core remains the authority for safe continuation/old generation fences.
      b.disabled = terminal && action === "resume";
      row.append(b);
    }
    host.append(row, feedback);
  }
  function refButton(view: UnitView, title: string, ref: ForgeRef) {
    return button(title, () => {
      view.contentEpoch++; view.contentRef = { ...ref, kind: ref.kind || "payload" }; view.contentOffset = 0;
      view.content.textContent = ""; view.contentBusy = false;
      void loadContent(view);
    });
  }
  async function loadContent(view: UnitView) {
    if (!view.contentRef || view.contentBusy) return;
    const captured = epoch, contentEpoch = view.contentEpoch, ref = view.contentRef, offset = view.contentOffset;
    view.contentBusy = true; view.contentMore.disabled = true;
    try {
      const page = await request<PayloadPage>(query("/api/forge/payload", { task_id: view.task.id, unit_id: view.unit.id, sha256: ref.sha256, kind: ref.kind, offset, limit: 8000 }));
      if (!valid(captured) || contentEpoch !== view.contentEpoch) return;
      if (page.sha256 !== ref.sha256 || page.offset !== offset) throw Error("Страница не совпала с выбранным результатом");
      view.content.textContent += page.text;
      view.contentOffset = page.next_offset ?? page.total_chars;
      view.contentMeta.textContent = `SHA-256 ${page.sha256} · прочитано ${view.contentOffset} из ${page.total_chars} знаков`;
      view.contentMore.hidden = page.next_offset === null;
    } catch (e) {
      if (valid(captured) && contentEpoch === view.contentEpoch) view.contentMeta.textContent = "Не прочиталось: " + errorText(e);
    } finally {
      if (contentEpoch === view.contentEpoch) { view.contentBusy = false; view.contentMore.disabled = false; }
    }
  }
  async function loadEvents(view: UnitView) {
    if (view.eventBusy) return;
    const captured = epoch, generation = view.generation;
    view.eventBusy = true; view.more.disabled = true;
    try {
      const page = await request<EventPage>(query("/api/forge/events", { task_id: view.task.id, unit_id: view.unit.id, offset: view.eventOffset, after_seq: Math.max(0, ...view.eventSeq), limit: 50, generation }));
      if (!valid(captured) || generation !== view.generation) return;
      for (const row of page.events) {
        if (view.eventSeq.has(row.seq)) continue;
        view.eventSeq.add(row.seq);
        const item = el("div", "row");
        item.append(el("div", "mono", `${row.seq}. ${row.tool || row.kind} · ${row.kind}`), el("div", "muted", row.at || "время не записано"));
        const links = el("div", "forge-actions");
        if (row.input_ref?.sha256) links.append(refButton(view, "Аргументы", { ...row.input_ref, kind: "payload" }));
        if (row.output_ref?.sha256) links.append(refButton(view, "Результат вызова", { ...row.output_ref, kind: "payload" }));
        item.append(links); view.log.append(item);
      }
      view.eventOffset = page.next_offset; view.hasMore = page.has_more; view.eventsLoaded = true;
      view.logStatus.textContent = view.eventSeq.size ? `Записанных шагов: ${view.eventSeq.size}${page.has_more ? " · есть продолжение" : ""}` : "Записанных шагов пока нет. Это не проверка процесса.";
      if (page.pending_tail) view.logStatus.textContent += " Последняя запись ещё не дописана; перечитаю её.";
      view.more.textContent = page.has_more ? "Следующие шаги" : "Обновить шаги";
    } catch (e) {
      if (valid(captured) && generation === view.generation) view.logStatus.textContent = "История не прочиталась: " + errorText(e);
    } finally { view.eventBusy = false; view.more.disabled = false; }
  }
  function unitView(task: ForgeTask, unit: ForgeUnit): UnitView {
    const details = document.createElement("details"); details.className = "fold forge-unit";
    const summary = document.createElement("summary"), title = el("b"), meta = el("div", "muted");
    summary.append(title, meta);
    const body = el("div", "fold-body"), brief = el("p", "forge-brief"), actions = el("div"), refs = el("div", "forge-actions");
    const log = el("div"), logStatus = el("p", "muted"), content = el("pre", "forge-payload"), contentMeta = el("p", "muted forge-hash");
    const view: UnitView = { details, title, meta, brief, controls: actions, refs, log, logStatus,
      more: button("Прочитать шаги", () => void loadEvents(view)), content, contentMeta,
      contentMore: button("Следующие 8000 знаков", () => void loadContent(view)),
      task, unit, generation: unit.observation?.generation || "", eventsLoaded: false, eventOffset: 0, hasMore: false,
      eventBusy: false, eventSeq: new Set(), contentOffset: 0, contentBusy: false, contentEpoch: 0 };
    view.contentMore.hidden = true;
    body.append(brief, actions, refs, logStatus, log, view.more, contentMeta, content, view.contentMore);
    details.append(summary, body);
    details.addEventListener("toggle", () => { if (details.open && running && !view.eventsLoaded) void loadEvents(view); });
    return view;
  }
  function updateUnit(view: UnitView, task: ForgeTask, unit: ForgeUnit) {
    const generation = unit.observation?.generation || "";
    if (view.generation !== generation) {
      view.log.replaceChildren(); view.eventSeq.clear(); view.eventOffset = 0; view.eventsLoaded = false; view.hasMore = false;
      // Event journal remains canonical across generations; reset its cursor.
      view.generation = generation;
    }
    view.unit = unit; view.task = task;
    view.details.style.marginLeft = Math.min(unit.depth || 0, 8) * 14 + "px";
    view.title.textContent = `${unit.role || "участник"} · ${unit.model || "модель не записана"} · ${label(unit.status)}`;
    const o = unit.observation || {};
    view.meta.textContent = `${unit.id} · родитель: ${unit.parent_unit_id || "основная задача"}${unit.ancestry_unknown ? " (связь не подтверждена)" : ""}\n` +
      `${o.phase ? (PHASE[o.phase] || o.phase) + (o.tool ? ": " + o.tool : "") : "фаза не записана"} · запрос: ${label(unit.request_status)} · последняя запись: ${o.observed_at || "нет"} · живость процесса не проверялась` +
      (unit.current_disposition ? " · приёмка: " + (ACCEPTED[unit.current_disposition] || unit.current_disposition) : unit.result_ref ? " · ждёт приёмки" : "") +
      (unit.custody === "unresolved_stop" ? " · остановка не подтверждена" : "") +
      (unit.stale_dispositions ? ` · несовпавших расписок: ${unit.stale_dispositions}` : "");
    view.brief.textContent = unit.subtask || "Подзадача не записана.";
    // Controls keep their in-flight feedback while the next observation arrives.
    if (!view.controls.firstChild) controls(() => [view.task, view.unit], view.controls);
    view.refs.replaceChildren();
    if (unit.result_ref) view.refs.append(refButton(view, "Итог участника", unit.result_ref));
    if (unit.request_ref) view.refs.append(refButton(view, "Полное задание", unit.request_ref));
    const names: Record<string, string> = { diff_ref: "Изменения и дифф", files_ref: "Файлы", checks_ref: "Проверки", review_ref: "Ревью" };
    for (const [name, ref] of Object.entries(unit.payload_refs || {})) view.refs.append(refButton(view, names[name] || name, ref));
  }
  function updateTask(task: ForgeTask) {
    let view = tasks.get(task.id);
    if (!view) {
      const details = document.createElement("details"); details.className = "fold forge-task";
      const summary = document.createElement("summary"), title = el("b"), state = el("span", "muted"), goal = el("p", "forge-brief");
      const body = el("div", "fold-body"), actions = el("div"), commands = el("div", "muted forge-command"), units = el("div");
      summary.append(title, state); body.append(goal, actions, commands, units); details.append(summary, body); teams.append(details);
      view = { details, title, state, goal, controls: actions, commands, units, views: new Map(), task }; tasks.set(task.id, view);
    }
    view.task = task;
    view.title.textContent = (task.goal || task.id).split("\n")[0];
    view.state.textContent = ` · ${label(task.status)}${task.control?.action === "pause" ? " · допуск на паузе" : ""} · участников ${task.agents.length}`;
    view.goal.textContent = task.goal || task.id;
    if (!view.controls.firstChild) controls(() => [view!.task, undefined], view.controls);
    const present = new Set(task.agents.map(a => a.id));
    for (const [id, unit] of view.views) if (!present.has(id)) { unit.details.remove(); view.views.delete(id); }
    for (const unit of task.agents) {
      let row = view.views.get(unit.id);
      if (!row) { row = unitView(task, unit); view.views.set(unit.id, row); view.units.append(row.details); }
      updateUnit(row, task, unit);
      if (view.details.open && row.details.open && (!row.eventsLoaded || !row.hasMore)) void loadEvents(row);
    }
    return view;
  }
  async function refresh() {
    if (!running || !root.isConnected || busy) return;
    clearTimeout(timer);
    const captured = epoch; busy = true;
    try {
      const rows = await request<ForgeTask[]>("/api/forge/tasks");
      if (!valid(captured)) return;
      if (!Array.isArray(rows)) throw Error("Некорректный список Forge");
      const present = new Set(rows.map(t => t.id));
      for (const [id, view] of tasks) if (!present.has(id)) { view.details.remove(); tasks.delete(id); }
      for (const task of rows) {
        const view = updateTask(task);
        if (view.details.open) {
          const commands = await request<{commands: Command[]}>(query("/api/forge/control", { task_id: task.id }));
          if (!valid(captured)) return;
          view.commands.textContent = commands.commands.map(c => `${c.agent_id || "Группа"}: ${commandText(c)}`).join("\n");
        }
      }
      for (const [key, entry] of pending) {
        try {
          const current = await request<Command>(query("/api/forge/control", { command_id: entry.command.command_id }));
          if (!valid(captured)) return;
          // Only this exact command may replace its own in-flight feedback.
          if (current.command_id !== entry.command.command_id || pending.get(key) !== entry) continue;
          entry.feedback.textContent = commandText(current);
          if (!["queued", "processing"].includes(current.state)) pending.delete(key);
        } catch { /* Unconfirmed transport is retained; never resubmit effects. */ }
      }
      message.textContent = rows.length ? "Последние задачи · записи обновляются, пока открыт раздел" : "Команд разработки пока нет.";
    } catch (e) {
      if (valid(captured)) message.textContent = "Не удалось обновить Forge: " + errorText(e) + (tasks.size ? ". Предыдущие записи сохранены." : "");
    } finally {
      if (captured === epoch) { busy = false; if (running && root.isConnected) timer = setTimeout(() => void refresh(), pollMs); }
    }
  }
  return {
    start() { if (running) { void refresh(); return; } running = true; epoch++; busy = false; void refresh(); },
    stop() { running = false; epoch++; clearTimeout(timer); for (const controller of controllers) controller.abort(); controllers.clear(); busy = false; },
    refresh,
  };
}
