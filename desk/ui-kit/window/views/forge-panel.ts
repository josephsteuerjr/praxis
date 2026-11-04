// Owner Forge viewer. Persisted observations are not process-liveness probes.
import { ApiError, channelURL, cfg } from "../api";
import { ACTIONS } from "../../steps";
import {workActivity,type WorkActivity,type Reviewer} from '../work-activity';

export interface ForgeRef { kind: "payload" | "result" | "request"; sha256: string; size_bytes?: number }
export interface ForgeUnit {
  id: string; parent_unit_id?: string; depth?: number; ancestry_unknown?: boolean;
  role?: string; model?: string; subtask?: string; status?: string; request_status?: string;
  custody?: string; current_disposition?: string; stale_dispositions?: number;
  started?: number; created?: string; finished?: string;
  observation?: { generation?: string; phase?: string; tool?: string; observed_at?: string; iteration?: number };
  result_ref?: ForgeRef; request_ref?: ForgeRef; payload_refs?: Record<string, ForgeRef>;
  liveness?: {state: string; observed_at?: string; basis?: string}; process_liveness?: string;
}
export interface ForgeTask { id: string; kind?: string; goal?: string; status?: string; agents: ForgeUnit[]; max_active_workers?: number; control?: {action?: string} }
interface EventRow { seq: number; kind: string; at?: string; tool?: string; generation?: string; iteration?: number; call_id?: string; input_ref?: ForgeRef; output_ref?: ForgeRef; receipt?: Record<string, unknown>; model?: string; model_requested?: string; summary?: boolean; reason?: string; error?: string; attempt?: number; retry?: boolean; partial_not_dispatched?: boolean }
interface EventPage { events: EventRow[]; next_offset: number; next_after_seq: number; has_more: boolean; pending_tail?: boolean; generation: string }
interface PayloadPage { sha256: string; text: string; offset: number; total_chars: number; next_offset: number | null }
interface ActivityPage { active: ForgeTask[]; recent: ForgeTask[]; next_cursor: string | null }
interface PanelOptions { compact?: boolean; readOnly?: boolean; controls?: boolean; scope?: () => string; onPlans?: () => void }
interface Command { command_id: string; state: string; task_id?: string; agent_id?: string; action?: string; reason?: string; result?: unknown }
type Read = <T>(path: string, signal: AbortSignal, body?: unknown) => Promise<T>;

const STATUS: Record<string, string> = { active: "в работе", running: "выполняется", queued: "в очереди", pending: "в очереди", done: "завершено", completed: "завершено", paused: "на паузе", waiting: "ожидает", stop_pending: "остановка не подтверждена", lost: "связь потеряна", cancelled: "отменено", stopped: "остановлено", failed: "ошибка", error: "ошибка", unknown: "состояние не подтверждено", abandoned: "оставлено", awaiting_acceptance: "ждёт приёмки" };
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
const shortGoal = (text: string, limit = 130) => { const chars = Array.from(text.split('\n')[0]); return chars.slice(0, limit).join('') + (chars.length > limit ? '…' : ''); };
function unitDuration(unit: ForgeUnit): string {
  if (!unit.started || !Number.isFinite(unit.started)) return '';
  const observed=Date.parse(unit.liveness?.observed_at || unit.observation?.observed_at || '');
  const live=unit.liveness?.state==='alive' && Number.isFinite(observed) && Date.now()-observed>=-5000 && Date.now()-observed<15000;
  const end=unit.finished ? Date.parse(unit.finished) : live ? Date.now() : observed;
  if (!Number.isFinite(end)) return '';
  const seconds=Math.max(0,Math.floor(end/1000-unit.started));
  return seconds<60 ? `${seconds} с` : `${Math.floor(seconds/60)} мин ${seconds%60} с`;
}

// HTTP deliberately: AbortController owns each mounted read (the shared tunnel
// has no cancellation API). Same authenticated URL and route authorization.
export const readForge: Read = async <T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> => {
  const response = await fetch(channelURL(path), { signal, cache: "no-store", method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
  return response.json();
};

interface PayloadView {
  content: HTMLElement; contentMeta: HTMLElement; contentMore: HTMLButtonElement;
  contentRef?: ForgeRef; contentOffset: number; contentBusy: boolean; contentEpoch: number;
}
interface UnitView extends PayloadView {
  details: HTMLDetailsElement; title: HTMLElement; meta: HTMLElement; brief: HTMLElement; goalSummary: HTMLElement; modelSummary: HTMLElement; stateSummary: HTMLElement; elapsed: HTMLElement;
  controls: HTMLElement; refs: HTMLElement; log: HTMLElement; logStatus: HTMLElement; more: HTMLButtonElement;
  unit: ForgeUnit; task: ForgeTask; generation: string; eventsLoaded: boolean; eventOffset: number;
  hasMore: boolean; eventBusy: boolean; eventSeq: Set<number>;
  refsKey: string; lastSeq: number; actions: Map<string, ActionView>; payloads: Set<PayloadView>;
}
interface ActionView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; time: HTMLElement; refs: HTMLElement; harness: HTMLElement; identity: HTMLElement; payload: PayloadView; inputSha?: string; outputSha?: string }
interface TaskView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; goal: HTMLElement; controls: HTMLElement; commands: HTMLElement; units: HTMLElement; views: Map<string, UnitView>; task: ForgeTask }

/** Exported controller is also the production-fragment test seam. */
export function forgePanel(root: HTMLElement, read: Read = readForge, pollMs = 2500, options: PanelOptions = {}) {
  const message = el("p", "muted", "Читаю записи Forge…"); message.setAttribute("role", "status");
  const teams = el("div", "forge-teams");
  const recentTeams = el("div", "forge-teams forge-recent-teams");
  const activeReviews=el('div','forge-reviewers'),recentReviews=el('div','forge-reviewers');
  const renderActiveReviews=workActivity(activeReviews,{heading:false}),renderRecentReviews=workActivity(recentReviews,{heading:false});
  const recent = document.createElement('details'); recent.className = 'forge-recent';
  recent.append(el('summary', '', 'Недавно завершены'), recentTeams,recentReviews);
  const next = button('Ещё завершённые', () => void loadRecent()); next.hidden = true; recent.append(next);
  const reviewNext=button('Ещё ревьюеры',()=>void loadReviewRecent());reviewNext.hidden=true;recent.append(reviewNext);
  const refreshButton = button("Обновить", () => void refresh());
  const heading = el("div", "forge-heading"); heading.append(el("h3", "section-title", options.compact ? 'Субагенты' : "Команды разработки"), refreshButton);
  if (options.onPlans) heading.append(button('Открыть обзор →', options.onPlans));
  root.className += options.compact ? ' forge-compact' : '';
  root.replaceChildren(heading, message, teams,activeReviews, recent);
  const tasks = new Map<string, TaskView>();
  const controllers = new Set<AbortController>();
  let running = false, epoch = 0, timer: ReturnType<typeof setTimeout> | undefined, busy = false;
  const getScope = options.scope || (() => JSON.stringify([cfg.base, cfg.key, cfg.agent]));
  let scope = getScope(), activeRows: ForgeTask[] = [], recentRows: ForgeTask[] = [], nextCursor: string | null = null, recentBusy = false;
  let reviewers:Reviewer[]=[],recentReviewers:Reviewer[]=[],reviewCursor:string|null=null,reviewHead:string|undefined,reviewBusy=false;
  let forgeError='',recentError='',reviewError='';
  const pending = new Map<string, { command: Command; feedback: HTMLElement }>();
  const valid = (captured: number) => running && captured === epoch && scope === getScope() && root.isConnected;
  function resetScope() {
    epoch++; for (const controller of controllers) controller.abort(); controllers.clear();
    scope = getScope(); tasks.clear(); pending.clear(); teams.replaceChildren(); recentTeams.replaceChildren();
    activeRows = []; recentRows = []; nextCursor = null; next.hidden = true; recentBusy = false; busy = false;
    reviewers=[];recentReviewers=[];reviewCursor=null;reviewHead=undefined;reviewBusy=false;reviewNext.hidden=true;renderActiveReviews({});renderRecentReviews({});
    forgeError='';recentError='';reviewError='';
    message.textContent = 'Читаю записи выбранного агента…';
  }
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
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const command: Command = { command_id: Array.from(bytes, value => value.toString(16).padStart(2, "0")).join(""), state: "queued", task_id: task.id, agent_id: unit?.id || "", action };
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
    if(options.readOnly)return;
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
  function refButton(view: UnitView, title: string, ref: ForgeRef, target: PayloadView = view) {
    return button(title, () => {
      target.contentEpoch++; target.contentRef = { ...ref, kind: ref.kind || "payload" }; target.contentOffset = 0;
      target.content.textContent = ""; target.contentBusy = false; target.contentMeta.textContent = 'Читаю…';
      void loadContent(view, target);
    });
  }
  async function loadContent(view: UnitView, target: PayloadView = view) {
    if (!target.contentRef || target.contentBusy || !running) return;
    const captured = epoch, contentEpoch = target.contentEpoch, generation = view.generation, ref = target.contentRef, offset = target.contentOffset;
    target.contentBusy = true; target.contentMore.disabled = true;
    try {
      const page = await request<PayloadPage>(query("/api/forge/payload", { task_id: view.task.id, unit_id: view.unit.id, sha256: ref.sha256, kind: ref.kind, offset, limit: 8000 }));
      if (!valid(captured) || contentEpoch !== target.contentEpoch || generation !== view.generation || !view.details.isConnected || !target.content.isConnected) return;
      if (page.sha256 !== ref.sha256 || page.offset !== offset) throw Error("Страница не совпала с выбранным результатом");
      const end = offset + Array.from(page.text).length;
      if (end > page.total_chars || (page.next_offset !== null ? page.next_offset !== end || end <= offset : end !== page.total_chars)) throw Error('Нарушен Unicode-курсор результата');
      target.content.textContent += page.text;
      target.contentOffset = page.next_offset ?? page.total_chars;
      target.contentMeta.textContent = `Прочитано ${target.contentOffset} из ${page.total_chars} знаков`;
      target.contentMeta.title = `SHA-256 ${page.sha256}`;
      target.contentMore.hidden = page.next_offset === null;
    } catch (e) {
      if (valid(captured) && contentEpoch === target.contentEpoch && generation === view.generation && view.details.isConnected) {
        target.contentMeta.textContent = "Не прочиталось: " + errorText(e); target.contentMore.hidden = false;
      }
    } finally {
      if (contentEpoch === target.contentEpoch) { target.contentBusy = false; target.contentMore.disabled = false; }
    }
  }
  function payloadView(view: UnitView): PayloadView {
    const payload: PayloadView = {content:el('pre','forge-payload'),contentMeta:el('p','muted forge-hash'),
      contentMore:button('Следующие 8000 знаков',()=>void loadContent(view,payload)),contentOffset:0,contentBusy:false,contentEpoch:0};
    payload.contentMore.hidden=true;view.payloads.add(payload);return payload;
  }
  function actionView(view: UnitView, model: boolean): ActionView {
    const details = document.createElement('details'); details.className = `forge-event forge-action st ${model ? 'st-think' : 'st-hand'}`;
    const summary = document.createElement('summary'); summary.className = 'st-line';
    const title = el('span','st-label'), state = el('span','st-state'), time = el('span','forge-event-time');
    const body = el('div','forge-action-body'), identity = el('div','forge-action-identity'), harness = el('div','st-harness'), refs = el('div','forge-actions');
    const payload=payloadView(view);
    summary.append(title,state,time);body.append(identity,harness,refs,payload.contentMeta,payload.content,payload.contentMore);
    details.append(summary,body);view.log.append(details);
    return {details,title,state,time,refs,harness,identity,payload};
  }
  function paintEvent(view: UnitView, row: EventRow, generation: string) {
    const model = ['model_start','model_result'].includes(row.kind);
    const tool = ['tool_start','tool_dispatch','tool_execution','tool_model_response','tool_result'].includes(row.kind);
    if (!model && !tool) {
      const hasRefs=!!(row.input_ref?.sha256 || row.output_ref?.sha256);
      const item=el(hasRefs ? 'details' : 'div','forge-event st');
      const title=row.kind==='completion_deferred' ? 'Продолжает работу' : row.kind==='model_transport_error' ? 'Ошибка связи с моделью' : row.kind;
      item.append(el(hasRefs ? 'summary' : 'div','st-line',title));
      if(row.reason)item.append(el('p','forge-brief',row.reason));
      if(row.error)item.append(el('p','forge-brief',row.error));
      if(row.attempt!==undefined)item.append(el('p','muted',`Попытка ${row.attempt+1}${row.retry===undefined ? '' : row.retry ? ' · будет повтор' : ' · без повтора'}`));
      if(row.partial_not_dispatched)item.append(el('p','muted','Частичный ответ сохранён; его действия не исполнялись.'));
      if(hasRefs){
        const payload=payloadView(view),refs=el('div','forge-actions');
        if(row.input_ref?.sha256)refs.append(refButton(view,'Аргументы',{...row.input_ref,kind:'payload'},payload));
        if(row.output_ref?.sha256)refs.append(refButton(view,'Результат вызова',{...row.output_ref,kind:'payload'},payload));
        item.append(refs,payload.contentMeta,payload.content,payload.contentMore);
      }
      view.log.append(item);return;
    }
    // A new start is a new physical call, even if a provider reuses its ID.
    // Orphan receipts remain readable; absence of a start is not reconstructed.
    const key=model ? `model:${row.generation || generation}:${row.iteration ?? 'legacy'}:${!!row.summary}`
      : row.call_id ? `tool:${row.generation || generation}:${row.iteration ?? 'legacy'}:${row.call_id}` : `event:${row.seq}`;
    let action=view.actions.get(key);
    if(!action || row.kind==='tool_start' || row.kind==='model_start') {
      action=actionView(view,model);view.actions.set(key,action);
    }
    const names: Record<string,string>={run:'Команда',edit:'Правка',inspect:'Просмотр',verify:'Проверка',process:'Процесс',delegate:'Субагент',signal:'Сообщение команде',checkpoint:'Контрольная точка'};
    action.title.textContent=model ? (row.summary ? 'Итог модели' : `Модель · шаг ${row.iteration ?? '—'}`)
      : ACTIONS[row.tool || ''] || names[row.tool || ''] || row.tool || 'Действие';
    action.state.textContent=row.kind==='model_result' ? 'ответ записан' : row.kind==='model_start' ? 'запрос записан'
      : row.kind==='tool_result' ? 'результат получен' : row.kind==='tool_dispatch' ? 'обработчик вызван'
      : row.kind==='tool_execution' ? 'исполнение записано' : row.kind==='tool_model_response' ? 'ответ передан модели' : 'запрос записан';
    action.time.textContent=row.at ? new Date(row.at).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}) : '';
    if(model && row.model_requested)action.identity.textContent='Запрошена модель: '+row.model_requested;
    if(model && row.model)action.identity.textContent += (action.identity.textContent ? '\n' : '')+'Ответила модель: '+row.model;
    if(!model)action.identity.textContent=[row.tool,row.call_id ? 'Вызов '+row.call_id : 'ID вызова не записан'].filter(Boolean).join(' · ');
    if(row.receipt) {
      const receipt=document.createElement('details');receipt.className='forge-receipt';
      receipt.append(el('summary','',row.kind==='tool_execution' ? 'Исполнение процесса' : 'Исполнение харнесса'),
        ...(row.receipt.handler ? [el('div','forge-receipt-label','Харнесс · '+String(row.receipt.handler))] : []),
        el('pre','forge-payload',JSON.stringify(row.receipt,null,2)));
      action.harness.append(receipt);
    }
    if(row.input_ref?.sha256 && row.input_ref.sha256!==action.inputSha) {
      action.inputSha=row.input_ref.sha256;action.refs.append(refButton(view,model ? 'Запрос модели' : 'Аргументы',{...row.input_ref,kind:'payload'},action.payload));
    }
    if(row.output_ref?.sha256 && row.output_ref.sha256!==action.outputSha) {
      action.outputSha=row.output_ref.sha256;action.refs.append(refButton(view,model ? 'Ответ модели' : 'Результат вызова',{...row.output_ref,kind:'payload'},action.payload));
    }
  }
  async function loadEvents(view: UnitView) {
    if (view.eventBusy) return;
    const captured = epoch, generation = view.generation;
    view.eventBusy = true; view.more.disabled = true;
    try {
      const page = await request<EventPage>(query("/api/forge/events", { task_id: view.task.id, unit_id: view.unit.id, offset: view.eventOffset, after_seq: view.lastSeq, limit: 50, generation }));
      if (!valid(captured) || generation !== view.generation) return;
      if (generation && page.generation !== generation) throw Error('История принадлежит другому поколению');
      let seq = view.lastSeq;
      for (const row of page.events) if (row.seq !== ++seq) throw Error('Нарушена последовательность истории');
      for (const row of page.events) {
        if (view.eventSeq.has(row.seq)) continue;
        view.eventSeq.add(row.seq); paintEvent(view,row,generation);
      }
      view.lastSeq=seq;view.eventOffset = page.next_offset; view.hasMore = page.has_more; view.eventsLoaded = true;
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
    const goalSummary = el('div', 'forge-unit-goal'), stateSummary = el('div', 'forge-unit-status');
    const modelSummary=el('div','forge-unit-model'),copy=el('div','forge-unit-copy');copy.append(goalSummary,modelSummary);
    const elapsed=el('span','forge-unit-time');
    if (options.compact) summary.append(copy, stateSummary,elapsed);
    else summary.append(title, meta,elapsed);
    const body = el("div", "fold-body"), brief = el("p", "forge-brief"), actions = el("div"), refs = el("div", "forge-actions");
    const log = el("div"), logStatus = el("p", "muted"), content = el("pre", "forge-payload"), contentMeta = el("p", "muted forge-hash");
    const view: UnitView = { details, title, meta, brief, goalSummary, modelSummary, stateSummary, elapsed, controls: actions, refs, log, logStatus,
      more: button("Прочитать шаги", () => void loadEvents(view)), content, contentMeta,
      contentMore: button("Следующие 8000 знаков", () => void loadContent(view)),
      task, unit, generation: unit.observation?.generation || "", eventsLoaded: false, eventOffset: 0, hasMore: false,
      eventBusy: false, eventSeq: new Set(), contentOffset: 0, contentBusy: false, contentEpoch: 0, refsKey: '', lastSeq: 0, actions: new Map(), payloads: new Set() };
    view.payloads.add(view);
    view.contentMore.hidden = true;
    if (options.compact) {
      const observation=document.createElement('details');observation.className='forge-observation';
      observation.append(el('summary','','Подробности участника'),title,meta,brief);
      // Opening an agent reveals its actions first. Identity/ancestry and raw
      // producer observations remain available without filling the roster.
      body.append(logStatus,log,view.more,actions,refs,contentMeta,content,view.contentMore,observation);
    } else body.append(brief, actions, refs, logStatus, log, view.more, contentMeta, content, view.contentMore);
    details.append(summary, body);
    details.addEventListener("toggle", () => { if (details.open && running && !view.eventsLoaded) void loadEvents(view); });
    return view;
  }
  function updateUnit(view: UnitView, task: ForgeTask, unit: ForgeUnit) {
    const generation = unit.observation?.generation || "";
    if (view.generation !== generation) {
      for(const target of view.payloads){target.contentEpoch++;target.contentRef=undefined;target.contentBusy=false;target.content.textContent='';target.contentMeta.textContent='';target.contentMore.hidden=true;target.contentMore.disabled=false;}
      view.payloads.clear();view.payloads.add(view);
      view.log.replaceChildren(); view.eventSeq.clear(); view.actions.clear(); view.lastSeq=0;view.eventOffset = 0; view.eventsLoaded = false; view.hasMore = false;
      // Event journal remains canonical across generations; reset its cursor.
      view.generation = generation;
    }
    view.unit = unit; view.task = task;
    view.details.style.marginLeft = Math.min(unit.depth || 0, 4) * (options.compact ? 8 : 14) + "px";
    view.title.textContent = `${unit.role || "участник"} · ${unit.model || "модель не записана"}` +
      (options.compact ? '' : ' · ' + label(unit.status));
    view.goalSummary.textContent = unit.role==='reviewer' ? `Ревью · ${unit.model || unit.id}` : shortGoal(unit.subtask || 'Подзадача не записана.', 85);
    view.goalSummary.title = unit.subtask || 'Подзадача не записана.';
    view.modelSummary.textContent=[unit.role || 'участник',unit.model || 'модель не записана'].join(' · ');
    const acceptance=(task.kind || 'work')==='work' && unit.status==='done' && unit.result_ref
      ? unit.current_disposition ? ACCEPTED[unit.current_disposition] : 'ждёт решения агента' : '';
    view.stateSummary.textContent = acceptance || label(unit.status);
    view.stateSummary.title = view.stateSummary.textContent;
    view.elapsed.textContent=unitDuration(unit);view.elapsed.title='Время от запуска процесса';
    view.stateSummary.setAttribute('data-state', unit.status || 'unknown');
    const o = unit.observation || {};
    const live = unit.liveness;
    const liveText = live?.state === 'alive' ? 'процесс наблюдался живым' : live?.state === 'exited' ? 'выход процесса наблюдался' : live?.state === 'not_started' ? 'процесс ещё не запускался' : 'живость процесса неизвестна';
    view.meta.textContent = `${unit.id} · родитель: ${unit.parent_unit_id || "основная задача"}${unit.ancestry_unknown ? " (связь не подтверждена)" : ""}\n` +
      `${o.phase ? (PHASE[o.phase] || o.phase) + (o.tool ? ": " + o.tool : "") : "фаза не записана"} · запрос: ${label(unit.request_status)} · последняя запись: ${o.observed_at || "нет"} · ${liveText}${live?.observed_at ? ': ' + live.observed_at : ''}` +
      (unit.current_disposition ? " · приёмка: " + (ACCEPTED[unit.current_disposition] || unit.current_disposition) : unit.result_ref ? " · ждёт приёмки" : "") +
      (unit.custody === "unresolved_stop" ? " · остановка не подтверждена" : "") +
      (unit.stale_dispositions ? ` · несовпавших расписок: ${unit.stale_dispositions}` : "");
    view.brief.textContent = unit.subtask || "Подзадача не записана.";
    // Controls keep their in-flight feedback while the next observation arrives.
    if ((!options.compact || options.controls) && !view.controls.firstChild) controls(() => [view.task, view.unit], view.controls);
    const refsKey = JSON.stringify([unit.result_ref, unit.request_ref, unit.payload_refs]);
    if (refsKey !== view.refsKey) {
      view.refsKey = refsKey; view.refs.replaceChildren();
      if (unit.result_ref) view.refs.append(refButton(view, "Итог участника", unit.result_ref));
      if (unit.request_ref) view.refs.append(refButton(view, "Полное задание", unit.request_ref));
      const names: Record<string, string> = { diff_ref: "Изменения и дифф", files_ref: "Файлы", checks_ref: "Проверки", review_ref: "Ревью" };
      for (const [name, ref] of Object.entries(unit.payload_refs || {})) view.refs.append(refButton(view, names[name] || name, ref));
    }
  }
  function updateTask(task: ForgeTask, host: HTMLElement) {
    let view = tasks.get(task.id);
    if (!view) {
      const details = document.createElement("details"); details.className = "fold forge-task";
      details.open = false;
      const summary = document.createElement("summary"), title = el("b"), state = el("span", "muted"), goal = el("p", "forge-brief");
      const body = el("div", "fold-body"), actions = el("div"), commands = el("div", "muted forge-command"), units = el("div");
      summary.append(title, state);
      if (options.compact) {
        const assignment = document.createElement('details'); assignment.className = 'forge-assignment';
        assignment.append(el('summary', '', 'Полное задание команды'), goal); body.append(assignment);
      } else body.append(goal);
      body.append(actions, commands, units); details.append(summary, body); host.append(details);
      view = { details, title, state, goal, controls: actions, commands, units, views: new Map(), task }; tasks.set(task.id, view);
    }
    if (view.details.parentNode !== host) host.append(view.details);
    view.task = task;
    view.title.textContent = options.compact ? shortGoal(task.goal || task.id) : (task.goal || task.id).split("\n")[0];
    view.state.textContent = ` · ${label(task.status)}${task.control?.action === "pause" ? " · допуск на паузе" : ""} · участников ${task.agents.length}`;
    view.goal.textContent = task.goal || task.id;
    if ((!options.compact || options.controls) && !view.controls.firstChild) controls(() => [view!.task, undefined], view.controls);
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
  function paintTasks() {
    const rows = [...activeRows, ...recentRows.filter(t => !activeRows.some(a => a.id === t.id))];
    const present = new Set(rows.map(t => t.id));
    for (const [id, view] of tasks) if (!present.has(id)) { view.details.remove(); tasks.delete(id); }
    for (const task of activeRows) updateTask(task, teams);
    for (const task of recentRows) if (!activeRows.some(a => a.id === task.id)) updateTask(task, recentTeams);
    next.hidden = !nextCursor;
    paintReviewers();
  }
  function paintReviewers() {
    const known=new Set([...activeRows,...recentRows].flatMap(task=>task.agents.map(unit=>task.id+'/'+unit.id)));
    const external=(rows:Reviewer[])=>rows.filter(row=>!row.task_id||!row.unit_id||!known.has(row.task_id+'/'+row.unit_id));
    const current=external(reviewers);renderActiveReviews({reviewers:current});renderRecentReviews({reviewers:external(recentReviewers)});
    reviewNext.hidden=!reviewCursor;
    const participants=activeRows.reduce((n,task)=>n+task.agents.length,0)+current.length;
    message.textContent=[forgeError,recentError,reviewError].filter(Boolean).join(' ') || (participants?`${participants} участников`:'Сейчас участников нет.');
  }
  async function loadReviewRecent() {
    if(!running||reviewBusy||!reviewCursor)return;
    const captured=epoch,cursor=reviewCursor;reviewBusy=true;reviewNext.disabled=true;
    try {
      const page=await request<WorkActivity>(query('/api/phone/activity',{review_cursor:cursor}));
      if(!valid(captured))return;
      recentReviewers.push(...(page.recent_reviewers||[]).filter(row=>!recentReviewers.some(old=>old.id===row.id)));
      reviewCursor=page.recent_reviewers_cursor||null;reviewError='';paintReviewers();
    }catch(e){if(valid(captured)){reviewError='Не удалось прочитать ревьюеров: '+errorText(e);paintReviewers()}}
    finally{if(captured===epoch){reviewBusy=false;reviewNext.disabled=false}}
  }
  async function loadRecent() {
    if (!running || recentBusy || !nextCursor) return;
    const captured = epoch, cursor = nextCursor; recentBusy = true; next.disabled = true;
    try {
      const page = await request<ActivityPage>(query('/api/forge/tasks', {view: 'active', cursor, limit: 20}));
      if (!valid(captured)) return;
      recentRows.push(...page.recent.filter(row => !recentRows.some(old => old.id === row.id)));
      nextCursor = page.next_cursor; recentError='';paintTasks();
    } catch (e) { if (valid(captured)) {recentError = 'Не удалось прочитать завершённые: ' + errorText(e);paintReviewers();} }
    finally { if (captured === epoch) { recentBusy = false; next.disabled = false; } }
  }
  async function refresh() {
    if (!running || !root.isConnected || busy) return;
    if (scope !== getScope()) resetScope();
    clearTimeout(timer);
    const captured = epoch; busy = true;
    try {
      const page = await request<ActivityPage>('/api/forge/tasks?view=active');
      if (!valid(captured)) return;
      if (!Array.isArray(page.active) || !Array.isArray(page.recent)) throw Error("Некорректный список Forge");
      forgeError='';
      activeRows = page.active;
      const current = new Map(page.recent.map(t => [t.id, t]));
      recentRows = [...page.recent, ...recentRows.filter(t => !current.has(t.id) && !activeRows.some(a => a.id === t.id))];
      if (!nextCursor || recentRows.length <= page.recent.length) nextCursor = page.next_cursor;
      paintTasks();
      for (const task of [...activeRows, ...recentRows]) {
        const view = tasks.get(task.id)!;
        if ((!options.compact || options.controls) && view.details.open) {
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
    } catch (e) {
      if (valid(captured)) {forgeError = "Не удалось обновить Forge: " + errorText(e) + (tasks.size ? ". Предыдущие записи сохранены." : "");paintReviewers();}
    } finally {
      if (captured === epoch) { busy = false; if (running && root.isConnected) timer = setTimeout(() => void refresh(), pollMs); }
    }
  }
  return {
    start() { if (scope !== getScope()) resetScope(); if (running) { void refresh(); return; } running = true; epoch++; busy = false; void refresh(); },
    stop() { running = false; epoch++; clearTimeout(timer); for (const controller of controllers) controller.abort(); controllers.clear(); busy = false;
      for(const task of tasks.values())for(const view of task.views.values()){
        view.eventBusy=false;view.more.disabled=false;
        for(const target of view.payloads)if(target.contentBusy){target.contentEpoch++;target.contentBusy=false;target.contentMore.disabled=false;target.contentMore.hidden=false;target.contentMeta.textContent='Чтение приостановлено. Продолжить с этой страницы.';}
      }
    },
    refresh,
    setReviewers(value:WorkActivity) {
      if(scope!==getScope())resetScope();
      reviewers=value.reviewers||[];
      const incoming=value.recent_reviewers||[],ids=new Set(incoming.map(row=>row.id)),activeIds=new Set(reviewers.map(row=>row.id));
      recentReviewers=[...incoming,...recentReviewers.filter(row=>!ids.has(row.id)&&!activeIds.has(row.id))];
      const head=incoming[0]?incoming[0].id+'@'+incoming[0].finished_at:'';
      if(reviewHead!==head){reviewHead=head;reviewCursor=value.recent_reviewers_cursor||null;}
      paintReviewers();
    },
  };
}
