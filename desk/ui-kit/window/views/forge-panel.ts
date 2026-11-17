import { t as uiText, locale } from "../../../lang/lang";
// Owner Forge viewer. Persisted observations are not process-liveness probes.
import { ApiError, channelURL, cfg } from "../api";
import { ACTIONS, toolTraceHTML, toolSummary, type ToolDetail, type ExecutionReceipt } from "../../steps";
import {workActivity,rememberDisclosure,type WorkActivity,type Reviewer} from '../work-activity';

export interface ForgeRef { kind: "payload" | "result" | "request"; sha256: string; size_bytes?: number }
export interface ForgeUnit {
  id: string; parent_unit_id?: string; depth?: number; ancestry_unknown?: boolean;
  role?: string; model?: string; subtask?: string; status?: string; request_status?: string;
  custody?: string; current_disposition?: string; stale_dispositions?: number;
  recovery?: {available: boolean; witness_recorded: boolean};
  started?: number; created?: string; finished?: string;
  observation?: { generation?: string; phase?: string; tool?: string; observed_at?: string; iteration?: number };
  result_ref?: ForgeRef; request_ref?: ForgeRef; payload_refs?: Record<string, ForgeRef>;
  liveness?: {state: string; observed_at?: string; basis?: string; error?:string}; process_liveness?: string;
}
export interface ForgeTask { id: string; kind?: string; goal?: string; status?: string; agents: ForgeUnit[]; max_active_workers?: number; control?: {action?: string} }
interface EventRow { seq: number; kind: string; at?: string; tool?: string; generation?: string; iteration?: number; call_id?: string; input_ref?: ForgeRef; output_ref?: ForgeRef; receipt?: ExecutionReceipt; outcome?: string; target?: string; model?: string; model_requested?: string; summary?: boolean; reason?: string; error?: string; attempt?: number; retry?: boolean; partial_not_dispatched?: boolean }
interface EventPage { events: EventRow[]; next_offset: number; next_after_seq: number; has_more: boolean; pending_tail?: boolean; generation: string }
interface PayloadPage { sha256: string; text: string; offset: number; total_chars: number; next_offset: number | null }
interface ActivityPage { active: ForgeTask[]; recent: ForgeTask[]; next_cursor: string | null }
interface PanelOptions { compact?: boolean; readOnly?: boolean; controls?: boolean; scope?: () => string; onPlans?: () => void }
interface Command { command_id: string; state: string; task_id?: string; agent_id?: string; action?: string; reason?: string; result?: unknown }
type Read = <T>(path: string, signal: AbortSignal, body?: unknown) => Promise<T>;

const STATUS: Record<string, string> = { get active() { return uiText("ui.ui.kit.window.views.forge.panel.cdd22f9fd0"); }, get running() { return uiText("ui.ui.kit.window.views.forge.panel.bd280ebe5b"); }, get queued() { return uiText("ui.ui.kit.window.views.forge.panel.f8ba02ed06"); }, get pending() { return uiText("ui.ui.kit.window.views.forge.panel.f8ba02ed06"); }, get done() { return uiText("ui.ui.kit.window.views.forge.panel.5f9b6607de"); }, get completed() { return uiText("ui.ui.kit.window.views.forge.panel.5f9b6607de"); }, get paused() { return uiText("ui.ui.kit.window.views.forge.panel.0bb2232e2e"); }, get waiting() { return uiText("ui.ui.kit.window.views.forge.panel.1c0ab75146"); }, get stop_pending() { return uiText("ui.ui.kit.window.views.forge.panel.8e5e46e4bb"); }, get lost() { return uiText("ui.ui.kit.window.views.forge.panel.8c77eb5f7f"); }, get cancelled() { return uiText("ui.ui.kit.window.views.forge.panel.8a09723017"); }, get stopped() { return uiText("ui.ui.kit.window.views.forge.panel.9ab567d8cc"); }, get failed() { return uiText("ui.ui.kit.window.views.forge.panel.44d4090ae9"); }, get error() { return uiText("ui.ui.kit.window.views.forge.panel.44d4090ae9"); }, get unknown() { return uiText("ui.ui.kit.window.views.forge.panel.6f86cfc2f7"); }, get abandoned() { return uiText("ui.ui.kit.window.views.forge.panel.239c0751fb"); }, get awaiting_acceptance() { return uiText("ui.ui.kit.window.views.forge.panel.569c0c18c7"); } };
const PHASE: Record<string, string> = { get starting() { return uiText("ui.ui.kit.window.views.forge.panel.855c61fbdf"); }, get model() { return uiText("ui.ui.kit.window.views.forge.panel.1e11198377"); }, get tool() { return uiText("ui.ui.kit.window.views.forge.panel.ea299cc892"); }, get waiting() { return uiText("ui.ui.kit.window.views.forge.panel.865fcaf5f9"); }, get finishing() { return uiText("ui.ui.kit.window.views.forge.panel.c3ce5db258"); }, get finished() { return uiText("ui.ui.kit.window.views.forge.panel.1e3aa39ba8"); }, get ready() { return uiText("ui.ui.kit.window.views.forge.panel.7e6bbe74f5"); }, get stopped() { return uiText("ui.ui.kit.window.views.forge.panel.2536d5194e"); }, get error() { return uiText("ui.ui.kit.window.views.forge.panel.afaca73319"); }, get checkpoint() { return uiText("ui.ui.kit.window.views.forge.panel.c23c209627"); }, get queued() { return uiText("ui.ui.kit.window.views.forge.panel.ab16de0728"); } };
const ACCEPTED: Record<string, string> = { get integrated() { return uiText("ui.ui.kit.window.views.forge.panel.9e114367b5"); }, get irrelevant() { return uiText("ui.ui.kit.window.views.forge.panel.fee772b3ed"); }, get deferred() { return uiText("ui.ui.kit.window.views.forge.panel.923886a847"); } };
const label = (v = "") => STATUS[v] || v || uiText("ui.ui.kit.window.views.forge.panel.1406688d87");
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
  return seconds<60 ? uiText("ui.ui.kit.window.views.forge.panel.baf1ec7874", {p0: String(seconds)}) : uiText("ui.ui.kit.window.views.forge.panel.aef6c8dfde", {p0: String(Math.floor(seconds/60)), p1: String(seconds%60)});
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
  refsKey: string; lastSeq: number; actions: Map<string, ActionView>; modelActions: Map<string, ModelActionView>; payloads: Set<PayloadView>;
}
interface ModelActionView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; time: HTMLElement; refs: HTMLElement; harness: HTMLElement; identity: HTMLElement; payload: PayloadView; inputSha?: string; outputSha?: string }
interface ActionView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; time: HTMLElement; mark:HTMLElement; body:HTMLElement; data:ToolDetail; key:string;
  input?:ForgeRef; output?:ForgeRef; modelOutput?:ForgeRef; payloads:Map<string,{text:string;offset:number;total:number;more:boolean;busy:boolean;error?:string}> }

interface TaskView { details: HTMLDetailsElement; title: HTMLElement; state: HTMLElement; goal: HTMLElement; controls: HTMLElement; commands: HTMLElement; units: HTMLElement; views: Map<string, UnitView>; task: ForgeTask }

/** Exported controller is also the production-fragment test seam. */
export function forgePanel(root: HTMLElement, read: Read = readForge, pollMs = 2500, options: PanelOptions = {}) {
  // The browser origin already isolates installations. Keep tokens out of
  // preference storage; task/unit identities are scoped by selected agent.
  const disclosureScope=()=>JSON.stringify([cfg.base,cfg.agent,(cfg as {agent_id?:string}).agent_id,!!options.compact]);
  const message = el("p", "muted", uiText("ui.ui.kit.window.views.forge.panel.c0a3d42991")); message.setAttribute("role", "status");
  const teams = el("div", "forge-teams");
  const recentTeams = el("div", "forge-teams forge-recent-teams");
  const activeReviews=el('div','forge-reviewers'),recentReviews=el('div','forge-reviewers');
  const renderActiveReviews=workActivity(activeReviews,{heading:false}),renderRecentReviews=workActivity(recentReviews,{heading:false});
  const active=document.createElement('details');active.className='forge-active';active.hidden=true;
  const activeSummary=el('summary');active.append(activeSummary,teams,activeReviews);
  const restoreActive=rememberDisclosure(active,'forge-active',disclosureScope);
  const recent = document.createElement('details'); recent.className = 'forge-recent';
  const recentSummary=el('summary');recent.hidden=true;
  recent.append(recentSummary, recentTeams,recentReviews);
  const restoreRecent=rememberDisclosure(recent,'forge-recent',disclosureScope);
  const next = button(uiText("ui.ui.kit.window.views.forge.panel.fa80efdd96"), () => void loadRecent()); next.hidden = true; recent.append(next);
  const reviewNext=button(uiText("ui.ui.kit.window.views.forge.panel.899d44eaf2"),()=>void loadReviewRecent());reviewNext.hidden=true;recent.append(reviewNext);
  const refreshButton = button(uiText("ui.ui.kit.window.views.forge.panel.603e460bf5"), () => void refresh());
  const heading = el("div", "forge-heading"); heading.append(el("h3", "section-title", options.compact ? uiText("ui.ui.kit.window.views.forge.panel.cf5873b9b1") : uiText("ui.ui.kit.window.views.forge.panel.2c4a9f46e4")), refreshButton);
  if (options.onPlans) heading.append(button(uiText("ui.ui.kit.window.views.forge.panel.6292c5de4a"), options.onPlans));
  root.className += options.compact ? ' forge-compact' : '';
  root.replaceChildren(heading, message, active, recent);
  const tasks = new Map<string, TaskView>();
  const controllers = new Set<AbortController>();
  let running = false, epoch = 0, timer: ReturnType<typeof setTimeout> | undefined, busy = false;
  const getScope = options.scope || (() => JSON.stringify([cfg.base, cfg.key, cfg.agent]));
  let scope = getScope(), activeRows: ForgeTask[] = [], recentRows: ForgeTask[] = [], nextCursor: string | null = null, recentBusy = false;
  let reviewers:Reviewer[]=[],recentReviewers:Reviewer[]=[],reviewCursor:string|null=null,reviewHead:string|undefined,reviewBusy=false;
  let forgeError='',recentError='',reviewError='',forgeLoaded=false;
  const pending = new Map<string, { command: Command; feedback: HTMLElement }>();
  const valid = (captured: number) => running && captured === epoch && scope === getScope() && root.isConnected;
  function resetScope() {
    epoch++; for (const controller of controllers) controller.abort(); controllers.clear();
    scope = getScope(); tasks.clear(); pending.clear(); teams.replaceChildren(); recentTeams.replaceChildren();
    activeRows = []; recentRows = []; nextCursor = null; next.hidden = true; recentBusy = false; busy = false;
    reviewers=[];recentReviewers=[];reviewCursor=null;reviewHead=undefined;reviewBusy=false;reviewNext.hidden=true;renderActiveReviews({});renderRecentReviews({});
    forgeError='';recentError='';reviewError='';forgeLoaded=false;recent.hidden=true;restoreRecent();
    active.hidden=true;restoreActive();
    message.textContent = uiText("ui.ui.kit.window.views.forge.panel.8f4c66cd60");
    message.hidden=false;
  }
  async function request<T>(path: string, body?: unknown): Promise<T> {
    const controller = new AbortController(); controllers.add(controller);
    try { return await read<T>(path, controller.signal, body); }
    finally { controllers.delete(controller); }
  }
  function commandText(c: Command): string {
    if (c.state === "queued") return uiText("ui.ui.kit.window.views.forge.panel.758a276176");
    if (c.state === "processing") return uiText("ui.ui.kit.window.views.forge.panel.d693eebb8c");
    if (c.state === "unknown") return uiText("ui.ui.kit.window.views.forge.panel.6c024486c5") + (c.reason || uiText("ui.ui.kit.window.views.forge.panel.a2d76e8c24"));
    if (c.state === "rejected") return uiText("ui.ui.kit.window.views.forge.panel.ee11bf92cc") + (c.reason || uiText("ui.ui.kit.window.views.forge.panel.321069cccf"));
    if (c.state === 'observed' && (c.result as {status?: string})?.status === 'prepared')
      return uiText('forge.recovery.prepared');
    return uiText("ui.ui.kit.window.views.forge.panel.5e54270023") + JSON.stringify(c.result ?? c.state);
  }
  async function control(task: ForgeTask, unit: ForgeUnit | undefined, action: string, target: HTMLElement,
                         recovery: {host_binding?: string; reason?: string} = {}) {
    const captured = epoch;
    const key = task.id + "/" + (unit?.id || "");
    if (pending.has(key)) return;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const command: Command = { command_id: Array.from(bytes, value => value.toString(16).padStart(2, "0")).join(""), state: "queued", task_id: task.id, agent_id: unit?.id || "", action };
    pending.set(key, { command, feedback: target });
    // A new owner action immediately leaves the slower idle-discovery cadence.
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(() => void refresh(), pollMs);
    target.textContent = uiText("ui.ui.kit.window.views.forge.panel.cd23e7d4b3");
    try {
      const response = await request<Command>("/api/forge/control", { ...command, ...recovery, expected_generation: unit?.observation?.generation || "" });
      if (!valid(captured)) return;
      if (response.command_id !== command.command_id) throw Error(uiText("ui.ui.kit.window.views.forge.panel.2857cdd48d"));
      if (['observed', 'rejected'].includes(response.state)) pending.delete(key);
      else pending.set(key, { command: response, feedback: target });
      target.textContent = commandText(response);
    } catch (e) {
      if (!valid(captured)) return;
      // Keep exact ID: uncertain delivery must not silently become another stop.
      command.state = "unknown";
      target.textContent = uiText("ui.ui.kit.window.views.forge.panel.5c7d157c89") + errorText(e) + uiText("ui.ui.kit.window.views.forge.panel.e2dc9153ea");
    }
  }
  function controls(current: () => [ForgeTask, ForgeUnit | undefined], host: HTMLElement) {
    host.replaceChildren();
    if(options.readOnly)return;
    const feedback = el("p", "muted"); feedback.setAttribute("role", "status");
    const [task, unit] = current();
    const standard = !options.compact || options.controls;
    if (!standard && !(unit ? [unit] : task.agents).some(row => row.recovery?.available)) return;
    const terminal = ["done", "completed", "cancelled", "abandoned"].includes(task.status || "");
    const row = el("div", "forge-actions");
    for (const [action, title] of (standard ? [["pause", uiText("ui.ui.kit.window.views.forge.panel.6aab9e2cb4")], ["stop", unit ? uiText("ui.ui.kit.window.views.forge.panel.f09a4f6adc") : uiText("ui.ui.kit.window.views.forge.panel.47d3bf45cf")], ["resume", uiText("ui.ui.kit.window.views.forge.panel.07c7b8b814")]] : [])) {
      const b = button(title, () => { const [task, unit] = current(); void control(task, unit, action, feedback); });
      // Core remains the authority for safe continuation/old generation fences.
      b.disabled = terminal && action === "resume";
      row.append(b);
    }
    if (standard) host.append(row);
    host.append(feedback);
    const details = document.createElement('details'); details.className = 'forge-recovery';
    details.append(el('summary', '', uiText('forge.recovery.title')));
    details.append(el('p', 'muted', uiText('forge.recovery.help')));
    const confirmation = document.createElement('label');
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
    confirmation.append(checkbox, document.createTextNode(uiText('forge.recovery.confirm_host')));
    const prepare = button(uiText('forge.recovery.prepare'), () => {
      if (!checkbox.checked) return;
      const [task, unit] = current();
      void control(task, unit, 'prepare_recovery', feedback, {
        host_binding: 'verified-local-execution-host', reason: 'Owner confirmed original Windows execution host after unresolved Stop',
      });
    });
    prepare.disabled = true;
    checkbox.addEventListener('change', () => { prepare.disabled = !checkbox.checked; });
    const verify = button(uiText('forge.recovery.verify'), () => {
      const [task, unit] = current(); void control(task, unit, 'recover', feedback);
    });
    details.append(confirmation, el('p', 'muted', uiText('forge.recovery.preserve')), prepare, verify);
    host.append(details);
    updateRecoveryControls(task, unit, host);
  }
  function updateRecoveryControls(task: ForgeTask, unit: ForgeUnit | undefined, host: HTMLElement) {
    const details = host.querySelector<HTMLElement>('.forge-recovery');
    if (!details) return;
    const rows = unit ? [unit] : task.agents;
    details.hidden = !rows.some(row => row.recovery?.available &&
      (row.recovery.witness_recorded || ['unknown', 'lost', 'stop_pending'].includes(row.status || '')));
  }
  function refButton(view: UnitView, title: string, ref: ForgeRef, target: PayloadView = view) {
    return button(title, () => {
      target.contentEpoch++; target.contentRef = { ...ref, kind: ref.kind || "payload" }; target.contentOffset = 0;
      target.content.textContent = ""; target.contentBusy = false; target.contentMeta.textContent = uiText("ui.ui.kit.window.views.forge.panel.53c9106d00");
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
      if (page.sha256 !== ref.sha256 || page.offset !== offset) throw Error(uiText("ui.ui.kit.window.views.forge.panel.cf1d95693a"));
      const end = offset + Array.from(page.text).length;
      if (end > page.total_chars || (page.next_offset !== null ? page.next_offset !== end || end <= offset : end !== page.total_chars)) throw Error(uiText("ui.ui.kit.window.views.forge.panel.43bcff5bde"));
      target.content.textContent += page.text;
      target.contentOffset = page.next_offset ?? page.total_chars;
      target.contentMeta.textContent = uiText("ui.ui.kit.window.views.forge.panel.78493ca94a", {p0: String(target.contentOffset), p1: String(page.total_chars)});
      target.contentMeta.title = `SHA-256 ${page.sha256}`;
      target.contentMore.hidden = page.next_offset === null;
    } catch (e) {
      if (valid(captured) && contentEpoch === target.contentEpoch && generation === view.generation && view.details.isConnected) {
        target.contentMeta.textContent = uiText("ui.ui.kit.window.views.forge.panel.f750546f2d") + errorText(e); target.contentMore.hidden = false;
      }
    } finally {
      if (contentEpoch === target.contentEpoch) { target.contentBusy = false; target.contentMore.disabled = false; }
    }
  }
  function payloadView(view: UnitView): PayloadView {
    const payload: PayloadView = {content:el('pre','forge-payload'),contentMeta:el('p','muted forge-hash'),
      contentMore:button(uiText("ui.ui.kit.window.views.forge.panel.f89882c627"),()=>void loadContent(view,payload)),contentOffset:0,contentBusy:false,contentEpoch:0};
    payload.contentMore.hidden=true;view.payloads.add(payload);return payload;
  }
  function actionView(view: UnitView, model: boolean): ModelActionView {
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
      // Сырое имя события («generation_started») — не подпись для человека.
      const title=({completion_deferred:uiText("ui.ui.kit.window.views.forge.panel.ce05239cab"),model_transport_error:uiText("ui.ui.kit.window.views.forge.panel.851c65d768"),
        generation_started:uiText("ui.ui.kit.window.views.forge.panel.74132656d5"),control_stopped:uiText("ui.ui.kit.window.views.forge.panel.a6c7bf454b"),result_published:uiText("ui.ui.kit.window.views.forge.panel.0b2f9ddeff")} as Record<string,string>)[row.kind] || row.kind;
      item.append(el(hasRefs ? 'summary' : 'div','st-line',title));
      if(row.reason)item.append(el('p','forge-brief',row.reason));
      if(row.error)item.append(el('p','forge-brief',row.error));
      if(row.attempt!==undefined)item.append(el('p','muted',uiText("ui.ui.kit.window.views.forge.panel.26d95c0381", {p0: String(row.attempt+1), p1: String(row.retry===undefined ? '' : row.retry ? uiText("ui.ui.kit.window.views.forge.panel.303c055a2d") : uiText("ui.ui.kit.window.views.forge.panel.a94d01e07a"))})));
      if(row.partial_not_dispatched)item.append(el('p','muted',uiText("ui.ui.kit.window.views.forge.panel.3c06e23025")));
      if(hasRefs){
        const payload=payloadView(view),refs=el('div','forge-actions');
        if(row.input_ref?.sha256)refs.append(refButton(view,uiText("ui.ui.kit.window.views.forge.panel.751a142ab0"),{...row.input_ref,kind:'payload'},payload));
        if(row.output_ref?.sha256)refs.append(refButton(view,uiText("ui.ui.kit.window.views.forge.panel.bc3aa967b0"),{...row.output_ref,kind:'payload'},payload));
        item.append(refs,payload.contentMeta,payload.content,payload.contentMore);
      }
      view.log.append(item);return;
    }
    // A new start is a new physical call, even if a provider reuses its ID.
    // Orphan receipts remain readable; absence of a start is not reconstructed.
    const key=model ? `model:${row.generation || generation}:${row.iteration ?? 'legacy'}:${!!row.summary}`
      : row.call_id ? `tool:${row.generation || generation}:${row.iteration ?? 'legacy'}:${row.call_id}` : `event:${row.seq}`;
    let action=view.modelActions.get(key);
    if(!action || row.kind==='tool_start' || row.kind==='model_start') {
      action=actionView(view,model);view.modelActions.set(key,action);
    }
    const names: Record<string,string>={run:uiText("ui.ui.kit.window.views.forge.panel.8b372c12c2"),edit:uiText("ui.ui.kit.window.views.forge.panel.a97b464eb8"),inspect:uiText("ui.ui.kit.window.views.forge.panel.f1163738de"),verify:uiText("ui.ui.kit.window.views.forge.panel.e56a412d12"),process:uiText("ui.ui.kit.window.views.forge.panel.781e34e36d"),delegate:uiText("ui.ui.kit.window.views.forge.panel.3d3af52b26"),signal:uiText("ui.ui.kit.window.views.forge.panel.c9e6479c7e"),checkpoint:uiText("ui.ui.kit.window.views.forge.panel.9946e198ca")};
    action.title.textContent=model ? (row.summary ? uiText("ui.ui.kit.window.views.forge.panel.1157338e28") : uiText("ui.ui.kit.window.views.forge.panel.de4efc6028", {p0: String(row.iteration ?? '—')}))
      : ACTIONS[row.tool || ''] || names[row.tool || ''] || row.tool || uiText("ui.ui.kit.window.views.forge.panel.6b53aafe28");
    action.state.textContent=row.kind==='model_result' ? uiText("ui.ui.kit.window.views.forge.panel.f669b47625") : row.kind==='model_start' ? uiText("ui.ui.kit.window.views.forge.panel.97fbc25ff0")
      : row.kind==='tool_result' ? uiText("ui.ui.kit.window.views.forge.panel.6c455b8924") : row.kind==='tool_dispatch' ? uiText("ui.ui.kit.window.views.forge.panel.6fce85e3b3")
      : row.kind==='tool_execution' ? uiText("ui.ui.kit.window.views.forge.panel.68dc6c7c5d") : row.kind==='tool_model_response' ? uiText("ui.ui.kit.window.views.forge.panel.bdde88b484") : uiText("ui.ui.kit.window.views.forge.panel.97fbc25ff0");
    action.time.textContent=row.at ? new Date(row.at).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}) : '';
    if(model && row.model_requested)action.identity.textContent=uiText("ui.ui.kit.window.views.forge.panel.086243fb86")+row.model_requested;
    if(model && row.model)action.identity.textContent += (action.identity.textContent ? '\n' : '')+uiText("ui.ui.kit.window.views.forge.panel.cc986a52b8")+row.model;
    if(!model)action.identity.textContent=[row.tool,row.call_id ? uiText("ui.ui.kit.window.views.forge.panel.9c843b5a75")+row.call_id : uiText("ui.ui.kit.window.views.forge.panel.4119a228c0")].filter(Boolean).join(' · ');
    if(row.receipt) {
      const receipt=document.createElement('details');receipt.className='forge-receipt';
      receipt.append(el('summary','',row.kind==='tool_execution' ? uiText("ui.ui.kit.window.views.forge.panel.ef60903168") : uiText("ui.ui.kit.window.views.forge.panel.d0d47b7549")),
        ...(row.receipt.handler ? [el('div','forge-receipt-label',uiText("ui.ui.kit.window.views.forge.panel.2aacb871e6")+String(row.receipt.handler))] : []),
        el('pre','forge-payload',JSON.stringify(row.receipt,null,2)));
      action.harness.append(receipt);
    }
    if(row.input_ref?.sha256 && row.input_ref.sha256!==action.inputSha) {
      action.inputSha=row.input_ref.sha256;action.refs.append(refButton(view,model ? uiText("ui.ui.kit.window.views.forge.panel.75f0c4e802") : uiText("ui.ui.kit.window.views.forge.panel.751a142ab0"),{...row.input_ref,kind:'payload'},action.payload));
    }
    if(row.output_ref?.sha256 && row.output_ref.sha256!==action.outputSha) {
      action.outputSha=row.output_ref.sha256;action.refs.append(refButton(view,model ? uiText("ui.ui.kit.window.views.forge.panel.28b4e08b49") : uiText("ui.ui.kit.window.views.forge.panel.3ffc2ccd8c"),{...row.output_ref,kind:'payload'},action.payload));
    }
  }
  function paintAction(view:UnitView,action:ActionView) {
    const focused=action.body.contains(document.activeElement) ? (document.activeElement as HTMLElement)?.dataset.traceTab : undefined;
    const summary=toolSummary(action.data,view.unit.liveness?.state==='alive');
    action.title.textContent=action.data.tool || uiText("ui.ui.kit.window.views.forge.panel.ea299cc892");
    action.state.textContent=summary.state;action.mark.textContent=summary.icon;action.mark.title=summary.label;
    action.time.textContent=summary.duration;
    action.details.classList.toggle('is-failed',summary.failed);action.details.classList.toggle('is-attention',summary.attention);
    action.body.innerHTML=toolTraceHTML(action.data,action.key,view.task.id+'/'+view.unit.id);
    const cleared=new Set<string>();
    for(const [tab,ref,caption] of [['args',action.input,uiText("ui.ui.kit.window.views.forge.panel.751a142ab0")],['response',action.output,uiText("ui.ui.kit.window.views.forge.panel.7391ee5e19")],['response',action.modelOutput,uiText("ui.ui.kit.window.views.forge.panel.cf5b7d94b4")]] as const) {
      if(!ref)continue;
      const panel=action.body.querySelector<HTMLElement>(`[data-trace-panel="${tab}"]`)!;
      const state=action.payloads.get(ref.sha256);
      if(!cleared.has(tab)){panel.replaceChildren();cleared.add(tab)}
      const content=el('pre','forge-action-payload');content.textContent=state?.text || '';
      const status=el('p','muted',state?.error || (state ? uiText("ui.ui.kit.window.views.forge.panel.78493ca94a", {p0: String(state.offset), p1: String(state.total)}) : uiText("ui.ui.kit.window.views.forge.panel.bb6cb3ec0f")));
      const more=button(state?.busy?uiText("ui.ui.kit.window.views.forge.panel.53c9106d00"):state?.text?uiText("ui.ui.kit.window.views.forge.panel.aa92bb78cc"):uiText("ui.ui.kit.window.views.forge.panel.e792119764")+(tab==='args'?uiText("ui.ui.kit.window.views.forge.panel.e7b56cea16"):uiText("ui.ui.kit.window.views.forge.panel.f1cfb8171f")),()=>void loadActionPayload(view,action,ref));
      more.disabled=!!state?.busy;more.hidden=!!state&&!state.more&&!state.error;
      panel.append(el('h4','',caption),content,status,more);
    }
    if(!action.modelOutput)action.body.querySelector('[data-trace-panel="response"]')?.append(el('p','tool-trace-unrecorded',uiText("ui.ui.kit.window.views.forge.panel.5806a655e6")));
    if(focused)action.body.querySelector<HTMLElement>(`[data-trace-tab="${focused}"]`)?.focus({preventScroll:true});
  }
  async function loadActionPayload(view:UnitView,action:ActionView,ref:ForgeRef) {
    let state=action.payloads.get(ref.sha256);
    if(state?.busy || (state&&!state.more&&!state.error))return;
    if(!state){state={text:'',offset:0,total:0,more:true,busy:false};action.payloads.set(ref.sha256,state)}
    const captured=epoch,generation=view.generation,offset=state.offset;state.busy=true;state.error='';paintAction(view,action);
    try {
      const page=await request<PayloadPage>(query('/api/forge/payload',{task_id:view.task.id,unit_id:view.unit.id,sha256:ref.sha256,kind:ref.kind||'payload',offset,limit:8000}));
      if(!valid(captured)||generation!==view.generation||!action.details.isConnected)return;
      const end=offset+Array.from(page.text).length;
      if(page.sha256!==ref.sha256||page.offset!==offset||end>page.total_chars||(page.next_offset===null?end!==page.total_chars:page.next_offset!==end||end<=offset))throw Error(uiText("ui.ui.kit.window.views.forge.panel.5cd184a112"));
      state.text+=page.text;state.offset=end;state.total=page.total_chars;state.more=page.next_offset!==null;
    }catch(e){if(valid(captured)&&generation===view.generation)state.error=uiText("ui.ui.kit.window.views.forge.panel.f750546f2d")+errorText(e)}
    finally{state.busy=false;if(valid(captured)&&generation===view.generation&&action.details.isConnected)paintAction(view,action)}
  }
  async function loadEvents(view: UnitView) {
    if (view.eventBusy) return;
    const captured = epoch, generation = view.generation;
    view.eventBusy = true; view.more.disabled = true;
    try {
      const page = await request<EventPage>(query("/api/forge/events", { task_id: view.task.id, unit_id: view.unit.id, offset: view.eventOffset, after_seq: view.lastSeq, limit: 50, generation }));
      if (!valid(captured) || generation !== view.generation) return;
      if (generation && page.generation !== generation) throw Error(uiText("ui.ui.kit.window.views.forge.panel.74c077c38f"));
      let seq = view.lastSeq;
      for (const row of page.events) if (row.seq !== ++seq) throw Error(uiText("ui.ui.kit.window.views.forge.panel.3a003c3690"));
      for (const row of page.events) {
        if (view.eventSeq.has(row.seq)) continue;
        view.eventSeq.add(row.seq);
        if (['tool_start','tool_dispatch','tool_execution','tool_model_response','tool_result','tool_failed'].includes(row.kind)) {
          const key=row.call_id ? `${row.generation||generation}:${row.iteration??'legacy'}:${row.call_id}` : `event:${row.seq}`;
          let action=view.actions.get(key);
          if(!action || row.kind==='tool_start') {
            const details=document.createElement('details');details.className='forge-event forge-action st st-hand tool-row';
            const summary=document.createElement('summary');summary.className='st-line';
            const title=el('code','st-label'),state=el('span','st-state'),time=el('span','st-duration'),mark=el('span','tool-outcome');
            const body=el('div','forge-action-body');summary.append(mark,title,state,time);details.append(summary,body);view.log.append(details);
            action={details,title,state,time,mark,body,key,data:{at:row.at,tool:row.tool},payloads:new Map()};view.actions.set(key,action);
            const own=action;
            details.addEventListener('toggle',()=>{if(details.open&&own.input)void loadActionPayload(view,own,own.input)});
            body.addEventListener('trace-tab-selected',event=>{
              const tab=(event as CustomEvent<string>).detail;
              const ref=tab==='args'?own.input:tab==='response'?own.output:undefined;
              if(ref)void loadActionPayload(view,own,ref);
              if(tab==='response'&&own.modelOutput)void loadActionPayload(view,own,own.modelOutput);
            });
          }
          if(row.tool)action.data.tool=row.tool;
          if(row.kind==='tool_start'){action.data.at=row.at;action.data.status='running'}
          if(row.kind==='tool_dispatch'&&row.receipt)action.data.execution=row.receipt;
          if(row.kind==='tool_execution'&&row.receipt)(action.data.operations??=[]).push(row.receipt);
          if(['tool_result','tool_failed'].includes(row.kind)){
            action.data.finished_at=row.at;action.data.status=row.kind==='tool_failed'?'failed':'received';
            action.data.outcome=row.outcome||'unknown';action.data.target=row.target;action.data.error=row.error;
          }
          if(row.input_ref?.sha256)action.input={...row.input_ref,kind:'payload'};
          if(row.output_ref?.sha256){
            if(row.kind==='tool_model_response')action.modelOutput={...row.output_ref,kind:'payload'};
            else action.output={...row.output_ref,kind:'payload'};
          }
          paintAction(view,action);
          continue;
        }
        paintEvent(view,row,generation);
      }
      view.lastSeq=seq;view.eventOffset = page.next_offset; view.hasMore = page.has_more; view.eventsLoaded = true;
      view.logStatus.textContent = view.eventSeq.size ? uiText("ui.ui.kit.window.views.forge.panel.df89c7d1c2", {p0: String(view.eventSeq.size), p1: String(page.has_more ? uiText("ui.ui.kit.window.views.forge.panel.52ee143598") : "")}) : uiText("ui.ui.kit.window.views.forge.panel.0760361158");
      if (page.pending_tail) view.logStatus.textContent += uiText("ui.ui.kit.window.views.forge.panel.d3a5b0c82c");
      view.more.textContent = page.has_more ? uiText("ui.ui.kit.window.views.forge.panel.0be62c7b2f") : uiText("ui.ui.kit.window.views.forge.panel.a52ce67987");
    } catch (e) {
      if (valid(captured) && generation === view.generation) view.logStatus.textContent = uiText("ui.ui.kit.window.views.forge.panel.e90b80f3a5") + errorText(e);
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
      more: button(uiText("ui.ui.kit.window.views.forge.panel.89202e356b"), () => void loadEvents(view)), content, contentMeta,
      contentMore: button(uiText("ui.ui.kit.window.views.forge.panel.f89882c627"), () => void loadContent(view)),
      task, unit, generation: unit.observation?.generation || "", eventsLoaded: false, eventOffset: 0, hasMore: false,
      eventBusy: false, eventSeq: new Set(), contentOffset: 0, contentBusy: false, contentEpoch: 0, refsKey: '', lastSeq: 0, actions: new Map(), modelActions: new Map(), payloads: new Set() };
    view.payloads.add(view);
    view.contentMore.hidden = true;
    if (options.compact) {
      const observation=document.createElement('details');observation.className='forge-observation';
      observation.append(el('summary','',uiText("ui.ui.kit.window.views.forge.panel.f791767118")),title,meta,brief);
      // Opening an agent reveals its actions first. Identity/ancestry and raw
      // producer observations remain available without filling the roster.
      body.append(logStatus,log,view.more,actions,refs,contentMeta,content,view.contentMore,observation);
    } else body.append(brief, actions, refs, logStatus, log, view.more, contentMeta, content, view.contentMore);
    details.append(summary, body);
    rememberDisclosure(details,'forge-unit:'+task.id+'/'+unit.id,disclosureScope());
    details.addEventListener("toggle", () => { if (details.open && running && !view.eventsLoaded) void loadEvents(view); });
    return view;
  }
  function updateUnit(view: UnitView, task: ForgeTask, unit: ForgeUnit) {
    const generation = unit.observation?.generation || "";
    if (view.generation !== generation) {
      for(const target of view.payloads){target.contentEpoch++;target.contentRef=undefined;target.contentBusy=false;target.content.textContent='';target.contentMeta.textContent='';target.contentMore.hidden=true;target.contentMore.disabled=false;}
      view.payloads.clear();view.payloads.add(view);
      view.log.replaceChildren(); view.eventSeq.clear(); view.actions.clear(); view.modelActions.clear(); view.lastSeq=0;view.eventOffset = 0; view.eventsLoaded = false; view.hasMore = false;
      // Event journal remains canonical across generations; reset its cursor.
      view.generation = generation;
    }
    view.unit = unit; view.task = task;
    view.details.style.marginLeft = Math.min(unit.depth || 0, 4) * (options.compact ? 8 : 14) + "px";
    view.title.textContent = `${unit.role || uiText("ui.ui.kit.window.views.forge.panel.3728896b36")} · ${unit.model || uiText("ui.ui.kit.window.views.forge.panel.b70c30266d")}` +
      (options.compact ? '' : ' · ' + label(unit.status));
    view.goalSummary.textContent = unit.role==='reviewer' ? uiText("ui.ui.kit.window.views.forge.panel.4140862946", {p0: String(unit.model || unit.id)}) : shortGoal(unit.subtask || uiText("ui.ui.kit.window.views.forge.panel.0d6bd2b100"), 85);
    view.goalSummary.title = unit.subtask || uiText("ui.ui.kit.window.views.forge.panel.0d6bd2b100");
    view.modelSummary.textContent=[unit.role || uiText("ui.ui.kit.window.views.forge.panel.3728896b36"),unit.model || uiText("ui.ui.kit.window.views.forge.panel.b70c30266d")].join(' · ');
    const acceptance=(task.kind || 'work')==='work' && unit.status==='done' && unit.result_ref
      ? unit.current_disposition ? ACCEPTED[unit.current_disposition] : uiText("ui.ui.kit.window.views.forge.panel.5e479184d8") : '';
    view.stateSummary.textContent = unit.liveness?.error ? uiText('forge.liveness.unreadable') : acceptance || label(unit.status);
    view.stateSummary.title = view.stateSummary.textContent;
    view.elapsed.textContent=unitDuration(unit);view.elapsed.title=uiText("ui.ui.kit.window.views.forge.panel.fae2ac42fb");
    view.stateSummary.setAttribute('data-state', unit.status || 'unknown');
    const o = unit.observation || {};
    const live = unit.liveness;
    const liveText = live?.error ? uiText('forge.liveness.unreadable') : live?.state === 'alive' ? uiText("ui.ui.kit.window.views.forge.panel.c5e4f530d6") : live?.state === 'exited' ? uiText("ui.ui.kit.window.views.forge.panel.2e1b268314") : live?.state === 'not_started' ? uiText("ui.ui.kit.window.views.forge.panel.6f13437958") : uiText("ui.ui.kit.window.views.forge.panel.0e4b4d2118");
    view.meta.textContent = uiText("ui.ui.kit.window.views.forge.panel.f680f19502", {p0: String(unit.id), p1: String(unit.parent_unit_id || uiText("ui.ui.kit.window.views.forge.panel.9247d77f72")), p2: String(unit.ancestry_unknown ? uiText("ui.ui.kit.window.views.forge.panel.78eb590e45") : "")}) +
      uiText("ui.ui.kit.window.views.forge.panel.c7c8925c7a", {p0: String(o.phase ? (PHASE[o.phase] || o.phase) + (o.tool ? ": " + o.tool : "") : uiText("ui.ui.kit.window.views.forge.panel.f7607ec31a")), p1: String(label(unit.request_status)), p2: String(o.observed_at || uiText("ui.ui.kit.window.views.forge.panel.dc9337ff5b")), p3: String(liveText), p4: String(live?.observed_at ? ': ' + live.observed_at : '')}) +
      (unit.current_disposition ? uiText("ui.ui.kit.window.views.forge.panel.69517383a8") + (ACCEPTED[unit.current_disposition] || unit.current_disposition) : unit.result_ref ? uiText("ui.ui.kit.window.views.forge.panel.06811cd0d8") : "") +
      (unit.custody === "unresolved_stop" ? uiText("ui.ui.kit.window.views.forge.panel.e49b1be6cb") : "") +
      (unit.custody === 'recovered_boundary' ? uiText('forge.recovery.recovered') : '') +
      (unit.stale_dispositions ? uiText("ui.ui.kit.window.views.forge.panel.bba46ad410", {p0: String(unit.stale_dispositions)}) : "");
    view.brief.textContent = unit.subtask || uiText("ui.ui.kit.window.views.forge.panel.0d6bd2b100");
    // Controls keep their in-flight feedback while the next observation arrives.
    if (!view.controls.firstChild) controls(() => [view.task, view.unit], view.controls);
    updateRecoveryControls(view.task, view.unit, view.controls);
    const refsKey = JSON.stringify([unit.result_ref, unit.request_ref, unit.payload_refs]);
    if (refsKey !== view.refsKey) {
      view.refsKey = refsKey; view.refs.replaceChildren();
      if (unit.result_ref) view.refs.append(refButton(view, uiText("ui.ui.kit.window.views.forge.panel.0511bf6962"), unit.result_ref));
      if (unit.request_ref) view.refs.append(refButton(view, uiText("ui.ui.kit.window.views.forge.panel.564949c139"), unit.request_ref));
      const names: Record<string, string> = { diff_ref: uiText("ui.ui.kit.window.views.forge.panel.3dce20b229"), files_ref: uiText("ui.ui.kit.window.views.forge.panel.9088ca9c0d"), checks_ref: uiText("ui.ui.kit.window.views.forge.panel.bb1b79b14c"), review_ref: uiText("ui.ui.kit.window.views.forge.panel.2499f9129f") };
      for (const [name, ref] of Object.entries(unit.payload_refs || {})) view.refs.append(refButton(view, names[name] || name, ref));
    }
  }
  function updateTask(task: ForgeTask, host: HTMLElement) {
    let view = tasks.get(task.id);
    if (!view) {
      const details = document.createElement("details"); details.className = "fold forge-task";
      rememberDisclosure(details,'forge-task:'+task.id,disclosureScope());
      const summary = document.createElement("summary"), title = el("b"), state = el("span", "muted"), goal = el("p", "forge-brief");
      const body = el("div", "fold-body"), actions = el("div"), commands = el("div", "muted forge-command"), units = el("div");
      summary.append(title, state);
      if (options.compact) {
        const assignment = document.createElement('details'); assignment.className = 'forge-assignment';
        assignment.append(el('summary', '', uiText("ui.ui.kit.window.views.forge.panel.630837fadc")), goal); body.append(assignment);
      } else body.append(goal);
      body.append(actions, commands, units); details.append(summary, body); host.append(details);
      view = { details, title, state, goal, controls: actions, commands, units, views: new Map(), task }; tasks.set(task.id, view);
    }
    if (view.details.parentNode !== host) host.append(view.details);
    view.task = task;
    view.title.textContent = options.compact ? shortGoal(task.goal || task.id) : (task.goal || task.id).split("\n")[0];
    view.state.textContent = uiText("ui.ui.kit.window.views.forge.panel.819b636e71", {p0: String(label(task.status)), p1: String(task.control?.action === "pause" ? uiText("ui.ui.kit.window.views.forge.panel.600d141940") : ""), p2: String(task.agents.length)});
    view.goal.textContent = task.goal || task.id;
    if (!view.controls.firstChild) controls(() => [view!.task, undefined], view.controls);
    updateRecoveryControls(view.task, undefined, view.controls);
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
    const current=external(reviewers),finishedReviews=external(recentReviewers);renderActiveReviews({reviewers:current});renderRecentReviews({reviewers:finishedReviews});
    reviewNext.hidden=!reviewCursor;
    const participants=activeRows.reduce((n,task)=>n+task.agents.length,0)+current.length;
    active.hidden=activeRows.length===0&&current.length===0;
    activeSummary.textContent=uiText("ui.ui.kit.window.views.forge.panel.322dd92ee1", {p0: String(participants)});
    if(active.hidden)active.open=false;
    const completed=recentRows.filter(task=>!activeRows.some(active=>active.id===task.id)).reduce((n,task)=>n+task.agents.length,0)+finishedReviews.length;
    recent.hidden=completed===0;recentSummary.textContent=uiText("ui.ui.kit.window.views.forge.panel.8726a1f078", {p0: String(completed), p1: String(nextCursor||reviewCursor?'+':'')});
    if(recent.hidden)recent.open=false;
    message.textContent=[forgeError,recentError,reviewError].filter(Boolean).join(' ') || (!forgeLoaded?uiText("ui.ui.kit.window.views.forge.panel.c0a3d42991"):participants?uiText("ui.ui.kit.window.views.forge.panel.f3e69dddd1", {p0: String(participants)}):completed?'':uiText("ui.ui.kit.window.views.forge.panel.0ccd3ed6d9"));
    message.hidden=!message.textContent;
  }
  async function loadReviewRecent() {
    if(!running||reviewBusy||!reviewCursor)return;
    const captured=epoch,cursor=reviewCursor;reviewBusy=true;reviewNext.disabled=true;
    try {
      const page=await request<WorkActivity>(query('/api/phone/activity',{review_cursor:cursor}));
      if(!valid(captured))return;
      recentReviewers.push(...(page.recent_reviewers||[]).filter(row=>!recentReviewers.some(old=>old.id===row.id)));
      reviewCursor=page.recent_reviewers_cursor||null;reviewError='';paintReviewers();
    }catch(e){if(valid(captured)){reviewError=uiText("ui.ui.kit.window.views.forge.panel.3137854067")+errorText(e);paintReviewers()}}
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
    } catch (e) { if (valid(captured)) {recentError = uiText("ui.ui.kit.window.views.forge.panel.3b77c073a5") + errorText(e);paintReviewers();} }
    finally { if (captured === epoch) { recentBusy = false; next.disabled = false; } }
  }
  async function refresh() {
    if (!running || !root.isConnected || busy) return;
    if (document.hidden) { clearTimeout(timer); return; }
    if (scope !== getScope()) resetScope();
    clearTimeout(timer);
    const captured = epoch; busy = true;
    try {
      const page = await request<ActivityPage>('/api/forge/tasks?view=active');
      if (!valid(captured)) return;
      if (!Array.isArray(page.active) || !Array.isArray(page.recent)) throw Error(uiText("ui.ui.kit.window.views.forge.panel.071e667516"));
      forgeError='';forgeLoaded=true;
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
          view.commands.textContent = commands.commands.map(c => `${c.agent_id || uiText("ui.ui.kit.window.views.forge.panel.907efbd4ce")}: ${commandText(c)}`).join("\n");
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
      if (valid(captured)) {forgeError = uiText("ui.ui.kit.window.views.forge.panel.e90b015e0f") + errorText(e) + (tasks.size ? uiText("ui.ui.kit.window.views.forge.panel.5468c3d753") : "");paintReviewers();}
    } finally {
      if (captured === epoch) {
        busy = false;
        if (running && root.isConnected && !document.hidden) {
          const interval = activeRows.length || pending.size || forgeError ? pollMs : Math.max(pollMs, 10000);
          timer = setTimeout(() => void refresh(), interval);
        }
      }
    }
  }
  const visibility = () => { clearTimeout(timer); if (!document.hidden) void refresh(); };
  return {
    start() { if (scope !== getScope()) resetScope(); if (running) { void refresh(); return; } running = true; epoch++; busy = false; document.addEventListener('visibilitychange', visibility); void refresh(); },
    stop() { running = false; epoch++; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); for (const controller of controllers) controller.abort(); controllers.clear(); busy = false;
      for(const task of tasks.values())for(const view of task.views.values()){
        view.eventBusy=false;view.more.disabled=false;
        for(const target of view.payloads)if(target.contentBusy){target.contentEpoch++;target.contentBusy=false;target.contentMore.disabled=false;target.contentMore.hidden=false;target.contentMeta.textContent=uiText("ui.ui.kit.window.views.forge.panel.4f0d6b6919");}
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
