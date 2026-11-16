import { t as uiText, locale } from "../lang/lang";
// Шаги одного прогона агента — одна разметка для окна (панель хода, урок на
// экране «Система») и для телефона. Данные — `GET /api/run/{id}`.
import { esc, fmtK, md, runOriginText } from "./text";
import { copyText } from "./dom";
import { lensHTML, lensState, toolTarget, type ToolLens } from "./tool-lens";

const decimal = (value: number) => value.toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1, useGrouping: false});

/**
 * Старый ключ комнаты окна (до 10.09.2026) — копия `contract.rooms.legacy`.
 *
 * ⚠ Почему КОПИЯ, а не импорт JSON. Этот файл грузит стенд `app/test/actions.test.mjs`:
 * он читает исходник, снимает типы и подключает его как data:-модуль, подменяя один
 * относительный импорт. Из data:-адреса `./contract.json` не резолвится никогда.
 * Копию сверяет `app/test/contract.test.mjs` — тем же способом, каким сверяются копии
 * в Python и Rust.
 */
const LEGACY_WINDOW_KEY = "pult";

export interface RunDetail {
  id?: string;
  origin?: { text: string; source: string; display_title?: unknown };
  outcome?: { text: string; note: string };
  manifest?: {
    status?: string;
    created_at?: string;
    goal?: string;
    chat_id?: string | number | null;
    terminal?: { status?: string; reason?: string };
    context?: { chat_id?: string | number; delivery_chat_id?: string | number; origin_chat_id?: string | number; kind?: string };
  };
  iterations?: Array<{
    call_id?: string;
    seq?: number;
    status?: string;
    error?: string;
    at?: string;
    model?: string;
    ms?: number;
    progress?: { argument_bytes?: number; argument_deltas?: number; attempt_ms?: number; outcome?: string };
    progress_at?: string;
    /** stop_reason модели: max_tokens — ответ оборван потолком, а не получен. */
    stop?: string;
    text_chars?: number;
    text?: string;
    /** Длинное слово сохранено файлом результата: дочитать через /api/run/{id}/result/{ref}. */
    text_ref?: string;
    text_truncated?: boolean;
    usage?: { in?: number; cache_read?: number; out?: number };
    /** Words of the still-running call (core model_preview): thinking, plain text, reply being written. */
    preview?: { text?: string; thinking?: string; reply?: string; text_chars?: number; thinking_chars?: number; reply_chars?: number; at?: string };
    tools?: Array<{
      call_id?: string; seq?: number; at?: string; finished_at?: string; status?: string; error?: string;
      outcome?: string; target?: string; nested?: unknown[]; nested_cut?: boolean;
      tool?: string; args?: unknown;
      definition?: { description?: string; input_schema?: unknown };
      model_request?: { tool?: string; args?: unknown };
      execution?: ExecutionReceipt;
      operations?: ExecutionReceipt[];
      model_observation?: string;
      model_response_image_count?: number;
      result?: { head?: string; tail?: string; truncated?: boolean; result_id?: string; size?: number | null } | null;
      /** Сводка ответа руки (readers.py), когда сырой ответ велик для превью. */
      lens?: ToolLens;
      /** "result": исход взят из собственного отчёта руки — журнал его не записал. */
      outcome_source?: string;
    }>;
  }>;
}

export interface ExecutionReceipt {
  handler?: string; kind?: string; command?: string; argv?: string[]; requested_argv?: string[];
  cwd?: string; pid?: number; status?: string; exit_code?: number; error?: string;
}

export interface StepsOptions {
  /** Показать только последние N шагов (живой ход). */
  limit?: number;
  /** Обзор: повод и итог рядом, действия раскрываются отдельным блоком. */
  overview?: boolean;
  /** Пояснения под шагами (урок на экране «Система»): ключи think_first, think, hand, reply, end_turn, terminal. */
  lesson?: Record<string, string>;
}

export const ACTIONS: Record<string, string> = {
  get shell() { return uiText("ui.ui.kit.steps.8b372c12c2"); }, get run_command() { return uiText("ui.ui.kit.steps.075f39ec5a"); }, get read_file() { return uiText("ui.ui.kit.steps.45fb6f573f"); }, get write_file() { return uiText("ui.ui.kit.steps.a4430b3a82"); },
  get edit_file() { return uiText("ui.ui.kit.steps.a4c851c825"); }, get search() { return uiText("ui.ui.kit.steps.180f58ab97"); }, get web_search() { return uiText("ui.ui.kit.steps.bc19a3f034"); },
  get fs_read() { return uiText("ui.ui.kit.steps.45fb6f573f"); }, get fs_ls() { return uiText("ui.ui.kit.steps.844f16c325"); }, get fs_search() { return uiText("ui.ui.kit.steps.40cd5d05c9"); }, get edit() { return uiText("ui.ui.kit.steps.a4c851c825"); },
  python: "Python", get code_query() { return uiText("ui.ui.kit.steps.1931982baf"); }, get task_control() { return uiText("ui.ui.kit.steps.b2f2be9925"); }, get coding_session() { return uiText("ui.ui.kit.steps.4be5fcdceb"); },
  get coding_run() { return uiText("ui.ui.kit.steps.d9eecb6b86"); }, get coding_inspect() { return uiText("ui.ui.kit.steps.80e25e5ed1"); }, get memory_compact() { return uiText("ui.ui.kit.steps.095dd3fe2c"); },
  get manage_notes() { return uiText("ui.ui.kit.steps.04a18b51cd"); }, get telegram_account() { return uiText("ui.ui.kit.steps.3230cfc033"); }, get "telegram.send_media"() { return uiText("ui.ui.kit.steps.924172163c"); },
  get my_agenda() { return uiText("ui.ui.kit.steps.170b1c8b9e"); }, get manage_loop() { return uiText("ui.ui.kit.steps.3a81a8fa85"); }, get recent_turns() { return uiText("ui.ui.kit.steps.d296125a18"); }, get stay_silent() { return uiText("ui.ui.kit.steps.4ef700539d"); },
  get react() { return uiText("ui.ui.kit.steps.c2622cc274"); }, git: "Git",
  get fetch_url() { return uiText("ui.ui.kit.steps.0ecb66478d"); }, get read_url() { return uiText("ui.ui.kit.steps.0ecb66478d"); },
  get reply() { return uiText("ui.ui.kit.steps.5cfcac6ad0"); }, get end_turn() { return uiText("ui.ui.kit.steps.324988af1d"); }, get remind_self() { return uiText("ui.ui.kit.steps.eea894b26c"); },
  get recall() { return uiText("ui.ui.kit.steps.bb91ccbb2f"); }, get remember() { return uiText("ui.ui.kit.steps.53bacfee4c"); }, get read_memory() { return uiText("ui.ui.kit.steps.ccee864b5a"); },
  get coding_task() { return uiText("ui.ui.kit.steps.4be5fcdceb"); }, get coding_agent() { return uiText("ui.ui.kit.steps.0cc6eef0cf"); },
  get coding_process() { return uiText("ui.ui.kit.steps.781e34e36d"); }, get coding_read() { return uiText("ui.ui.kit.steps.f0a663f477"); }, get coding_search() { return uiText("ui.ui.kit.steps.4cc341cef9"); },
  get coding_patch() { return uiText("ui.ui.kit.steps.76f978e79f"); }, get coding_verify() { return uiText("ui.ui.kit.steps.e56a412d12"); }, get coding_finish() { return uiText("ui.ui.kit.steps.5152fe706a"); },
  get write_skill() { return uiText("ui.ui.kit.steps.6d8788384a"); }, get consolidate_context() { return uiText("ui.ui.kit.steps.c993c62d33"); },
  get computer() { return uiText("ui.ui.kit.steps.9329c3a242"); }, get read_run_result() { return uiText("ui.ui.kit.steps.e1965c23d8"); }, get journal() { return uiText("ui.ui.kit.steps.77bde73edb"); },
  get wake() { return uiText("ui.ui.kit.steps.00ef5eb94e"); }, get set_wake() { return uiText("ui.ui.kit.steps.00ef5eb94e"); }, get sleep() { return uiText("ui.ui.kit.steps.1c947e774a"); }, get voice() { return uiText("ui.ui.kit.steps.24a0d703af"); }, get speak() { return uiText("ui.ui.kit.steps.24a0d703af"); },
  get browser() { return uiText("ui.ui.kit.steps.4a3b61fd81"); }, get screenshot() { return uiText("ui.ui.kit.steps.e878257234"); }, get send_file() { return uiText("ui.ui.kit.steps.d5b72f56e5"); },
  get "telegram.deliver"() { return uiText("ui.ui.kit.steps.9f545f8f36"); },
  get windows_mcp_Snapshot() { return uiText("ui.ui.kit.steps.d9063c292e"); }, get windows_mcp_Screenshot() { return uiText("ui.ui.kit.steps.e878257234"); },
  get windows_mcp_Click() { return uiText("ui.ui.kit.steps.f6cf996cea"); }, get windows_mcp_Type() { return uiText("ui.ui.kit.steps.d69397589d"); }, get windows_mcp_Scroll() { return uiText("ui.ui.kit.steps.64dbfe55dc"); },
  get windows_mcp_Move() { return uiText("ui.ui.kit.steps.757b2e98e3"); }, get windows_mcp_Shortcut() { return uiText("ui.ui.kit.steps.383ac4908f"); },
  get windows_mcp_App() { return uiText("ui.ui.kit.steps.448fabc413"); }, windows_mcp_PowerShell: "PowerShell",
  get windows_mcp_FileSystem() { return uiText("ui.ui.kit.steps.b3cd7719dc"); }, get windows_mcp_Registry() { return uiText("ui.ui.kit.steps.836f4c1824"); },
  get windows_mcp_Process() { return uiText("ui.ui.kit.steps.5650f4123d"); }, get windows_mcp_Clipboard() { return uiText("ui.ui.kit.steps.eb31594ab4"); },
  get windows_mcp_ControlStatus() { return uiText("ui.ui.kit.steps.25dc9559bc"); }, get windows_mcp_DisplayInventory() { return uiText("ui.ui.kit.steps.720042dadd"); },
  get windows_mcp_MultiSelect() { return uiText("ui.ui.kit.steps.6cf14b2064"); }, get windows_mcp_MultiEdit() { return uiText("ui.ui.kit.steps.a2c9033788"); },
  get windows_mcp_Notification() { return uiText("ui.ui.kit.steps.1a3cc6cdef"); }, get windows_mcp_Scrape() { return uiText("ui.ui.kit.steps.0ecb66478d"); },
  get windows_mcp_Wait() { return uiText("ui.ui.kit.steps.074415c7fe"); }, get windows_mcp_WaitFor() { return uiText("ui.ui.kit.steps.e6b55327fa"); },
};
const TERMINAL: Record<string, string> = {
  get done() { return uiText("ui.ui.kit.steps.edc4e7a138"); }, get completed() { return uiText("ui.ui.kit.steps.edc4e7a138"); }, get failed() { return uiText("ui.ui.kit.steps.042fb5f9ab"); },
  get cancelled() { return uiText("ui.ui.kit.steps.d33d9337ec"); }, get canceled() { return uiText("ui.ui.kit.steps.d33d9337ec"); }, get paused() { return uiText("ui.ui.kit.steps.6cf0b8fc13"); },
  get in_doubt() { return uiText("ui.ui.kit.steps.e16eb78176"); }, get blocked() { return uiText("ui.ui.kit.steps.5db21586e6"); },
};
/** Причина завершения человеческими словами. `done` бывает разным: оборвалась потолком —
 *  не то же, что «решила промолчать». Ход без руки reply НЕ помечается «без ответа»:
 *  в Hélène слово, написанное текстом, доставляет граница окна, а прогон ядра об этом
 *  не знает — такой ход остаётся просто «Работа завершена». */
export function terminalLabel(status: string, reason: string): { label: string; failed: boolean } {
  const r = (reason || "").toLowerCase();
  if (status === "done" || status === "completed") {
    if (r.includes("max_tokens")) return { label: uiText("ui.ui.kit.steps.1d3433e766"), failed: true };
    if (r === "silent decision") return { label: uiText("ui.ui.kit.steps.493e303d62"), failed: false };
  }
  return { label: TERMINAL[status] || status, failed: status === "failed" };
}
export const clip = (s: string, n: number) => s.length > n ? s.slice(0, n) + "…" : s;
export const pretty = (v: unknown) => typeof v === "string" ? v : JSON.stringify(v, null, 2) || "";

export function resultText(text: string): string {
  try {
    const data = JSON.parse(text);
    return pretty(data);
  } catch {
    return text;
  }
}

/** run_command's argv as one readable line; each item stays one argument (quoted like shlex). */
export function argvLine(args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const argv = (args as Record<string, unknown>).argv;
  return Array.isArray(argv) && argv.length && argv.every(x => typeof x === "string")
    ? (argv as string[]).map(shellQuote).join(" ") : "";
}

export function subject(args: unknown): string {
  if (!args || typeof args !== "object") return typeof args === "string" ? args : "";
  const a = args as Record<string, unknown>;
  if (typeof a.command !== "string" && typeof a.cmd !== "string" && argvLine(a)) return argvLine(a);
  for (const key of ["command", "cmd", "path", "file_path", "query", "url", "text", "goal", "note", "task", "action"]) {
    if (typeof a[key] === "string" && a[key]) return a[key] as string;
  }
  return "";
}

/** Source is the saved tool input, not a reconstructed OS argv or proof of execution. */
export function suppliedCommand(args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const fields = args as Record<string, unknown>;
  return typeof fields.command === "string" ? fields.command :
    typeof fields.cmd === "string" ? fields.cmd : argvLine(fields);
}

export type ToolDetail = NonNullable<NonNullable<RunDetail['iterations']>[number]['tools']>[number];

/** A readable projection; the original input remains available below it. */
export function toolParameters(args: unknown): Array<[string, unknown]> {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return [];
  const command = suppliedCommand(args);
  return Object.entries(args).filter(([key, value]) =>
    !((key === 'command' || key === 'cmd') && typeof value === 'string' && value === command) &&
    !(key === 'argv' && command !== '' && command === argvLine(args)));
}

function traceBlock(key: string, label: string, content: string, kind: string, opts: Parameters<typeof textBox>[2] = {}): string {
  return `<section class="tool-trace-block tool-trace-${kind}"><div class="tool-trace-heading"><span>${esc(label)}</span>` +
    uiText("ui.ui.kit.steps.c23eb9b8cd", {p0: String(esc(label)), p1: String(opts.truncated ? uiText("ui.ui.kit.steps.547e84dadd") : uiText("ui.ui.kit.steps.1ecddf40bf"))}) +
    textBox(key, content, opts) + `</section>`;
}

/** Outcome comes from the journal. A received payload alone never proves success. */
export function toolSummary(t: ToolDetail, live = false) {
  const known = ['ok','confirmed','failed','refused','in_doubt','partial','unknown'];
  const received = t.result != null || t.status === 'received' || !!t.finished_at;
  const active = !received && t.status !== 'failed' && live;
  const outcome = t.status === 'failed' ? 'failed' : known.includes(t.outcome || '') ? t.outcome! : 'unknown';
  const labels: Record<string,string> = {ok:uiText("ui.ui.kit.steps.7b3fee132f"),confirmed:uiText("ui.ui.kit.steps.3807e9938d"),failed:uiText("ui.ui.kit.steps.44d4090ae9"),refused:uiText("ui.ui.kit.steps.6d4b1454a0"),in_doubt:uiText("ui.ui.kit.steps.a518ad0ddc"),partial:uiText("ui.ui.kit.steps.27e1bd212b"),unknown:uiText("ui.ui.kit.steps.e1ffc81378")};
  const icons: Record<string,string> = {ok:'✓',confirmed:'✓',failed:'✗',refused:'⊘',in_doubt:'?',partial:'◐',unknown:'·'};
  const receipt = [t.execution, ...(t.operations || [])].filter(r => r?.exit_code != null).at(-1);
  const lens = active ? null : lensState(t.lens);
  const state = active ? uiText("ui.ui.kit.steps.bd280ebe5b") : lens ? lens.state : receipt?.exit_code != null ? `exit ${receipt.exit_code}${['ok','confirmed','failed'].includes(outcome) ? '' : ' · '+labels[outcome]}` : ['ok','confirmed'].includes(outcome) ? '' : labels[outcome];
  const elapsed = Date.parse(t.finished_at || '') - Date.parse(t.at || '');
  const seconds = Number.isFinite(elapsed) && elapsed >= 0 ? elapsed/1000 : null;
  const duration = seconds == null ? '' : seconds < 60 ? uiText("ui.ui.kit.steps.baf1ec7874", {p0: String(seconds < 10 ? decimal(seconds) : Math.round(seconds))}) : uiText("ui.ui.kit.steps.aef6c8dfde", {p0: String(Math.floor(seconds/60)), p1: String(Math.round(seconds%60))});
  return {outcome,active,state,icon:active ? '◌' : icons[outcome],label:active ? uiText("ui.ui.kit.steps.bd280ebe5b") : labels[outcome],duration,
    target:t.target || toolTarget(t.tool || '', t.args) || subject(t.args),
    failed:['failed','refused'].includes(outcome) || !!lens?.failed,attention:['partial','in_doubt'].includes(outcome) || !!lens?.attention};
}

const selectedTraceTabs = new Map<string,string>();
export function traceTabNames() {
  return [['args',uiText("ui.ui.kit.steps.751a142ab0")],['definition',uiText("ui.ui.kit.steps.8595381191")],['execution',uiText("ui.ui.kit.steps.b517109e50")],['request',uiText("ui.ui.kit.steps.e9e7fe7628")],['response',uiText("ui.ui.kit.steps.f1cfb8171f")]] as const;
}
export function traceTabsHTML(key:string, panels: Record<string,string>): string {
  // «Сводка» есть только у рук с линзой и тогда открывается первой.
  const names = traceTabNames();
  const tabs: ReadonlyArray<readonly [string,string]> = panels.summary ? [['summary',uiText("ui.ui.kit.steps.2acdbda48e")],...names] : names;
  const remembered = selectedTraceTabs.get(key);
  const selected = remembered && tabs.some(([tab])=>tab===remembered) ? remembered : panels.summary ? 'summary' : 'args';
  const id = 'trace-' + encodeURIComponent(key);
  return uiText("ui.ui.kit.steps.705883f694", {p0: String(esc(key))}) +
    tabs.map(([tab,label])=>`<button type="button" role="tab" id="${id}-${tab}" aria-controls="${id}-${tab}-panel" aria-selected="${tab===selected}" tabindex="${tab===selected?0:-1}" data-trace-tab="${tab}" aria-label="${label}">${tab==='request'||tab==='response'?`<span class="tab-long">${label}</span><span class="tab-short">${uiText('ui.steps.trace_'+tab+'_short')}</span>`:label}</button>`).join('') + '</div>' +
    tabs.map(([tab])=>`<section role="tabpanel" id="${id}-${tab}-panel" aria-labelledby="${id}-${tab}" data-trace-panel="${tab}" ${tab===selected?'':'hidden'}>${panels[tab] || uiText("ui.ui.kit.steps.470488b699")}</section>`).join('') + '</div>';
}
function selectTraceTab(button:HTMLElement):void {
  const trace=button.closest<HTMLElement>('[data-trace-key]');if(!trace)return;
  const tab=button.dataset.traceTab!,key=trace.dataset.traceKey!;
  selectedTraceTabs.delete(key);selectedTraceTabs.set(key,tab);
  if(selectedTraceTabs.size>1000)selectedTraceTabs.delete(selectedTraceTabs.keys().next().value!);
  for(const b of trace.querySelectorAll<HTMLElement>('[data-trace-tab]')) {const on=b===button;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;}
  for(const p of trace.querySelectorAll<HTMLElement>('[data-trace-panel]'))p.hidden=p.dataset.tracePanel!==tab;
  trace.dispatchEvent(new CustomEvent('trace-tab-selected',{bubbles:true,detail:tab}));
}

/** Python literal for a JSON value: the handler receives exactly these keyword arguments. */
export function pyLiteral(v: unknown, indent = ''): string {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'None';
  if (typeof v === 'string') {
    const quote = v.includes("'") && !v.includes('"') ? '"' : "'";
    return quote + v.replace(/\\/g, '\\\\').replace(new RegExp(quote, 'g'), '\\' + quote)
      .replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t') + quote;
  }
  const inner = indent + '    ';
  if (Array.isArray(v)) return v.length ? '[\n' + v.map(x => inner + pyLiteral(x, inner)).join(',\n') + ',\n' + indent + ']' : '[]';
  const entries = Object.entries(v as Record<string, unknown>);
  return entries.length ? '{\n' + entries.map(([k, x]) => inner + pyLiteral(k) + ': ' + pyLiteral(x, inner)).join(',\n') + ',\n' + indent + '}' : '{}';
}

/** `handler(**args)` — what the harness actually calls for a Python hand. */
export function pythonCall(handler: string, args: unknown): string {
  const kwargs = args && typeof args === 'object' && !Array.isArray(args) ? Object.entries(args as Record<string, unknown>) : [];
  const flat = kwargs.map(([k, v]) => `${k}=${pyLiteral(v)}`);
  const oneLine = `${handler}(${flat.join(', ')})`;
  if (oneLine.length <= 72 && !oneLine.includes('\n')) return oneLine;
  return `${handler}(\n` + kwargs.map(([k, v]) => `    ${k}=${pyLiteral(v, '    ')},`).join('\n') + '\n)';
}

/** POSIX shell quoting, as shlex.quote does. */
export function shellQuote(arg: string): string {
  return /^[\w@%+=:,./-]+$/.test(arg) ? arg : "'" + arg.replace(/'/g, `'"'"'`) + "'";
}

const RECEIPT_STATE: Record<string, string> = {
  get entered() { return uiText("ui.ui.kit.steps.6fce85e3b3"); }, get returned() { return uiText("ui.ui.kit.steps.3f7948a54d"); }, get timed_out() { return uiText("ui.ui.kit.steps.55829b7cce"); },
  get launch_failed() { return uiText("ui.ui.kit.steps.fa6213514d"); }, get running() { return uiText("ui.ui.kit.steps.bd280ebe5b"); }, get failed() { return uiText("ui.ui.kit.steps.44d4090ae9"); }, get cancelled() { return uiText("ui.ui.kit.steps.8a09723017"); }, get started() { return uiText("ui.ui.kit.steps.d847c0f696"); },
};

/** One receipt as the command it records: Python call, process line or bare harness note. */
export function executionText(r: ExecutionReceipt, args: unknown): { title: string; code: string; meta: string } {
  const meta = [r.exit_code != null ? '' : RECEIPT_STATE[r.status || ''] || r.status, r.exit_code != null ? `exit ${r.exit_code}` : '',
    r.pid != null ? `PID ${r.pid}` : '', r.error || ''].filter(Boolean).join(' · ');
  if (r.argv?.length || r.requested_argv?.length) {
    const argv = r.argv?.length ? r.argv : r.requested_argv!;
    const line = argv.map(shellQuote).join(' ');
    return { title: r.argv?.length ? uiText("ui.ui.kit.steps.781e34e36d") : uiText("ui.ui.kit.steps.a2889947cc"),
      code: (r.cwd ? `$ cd ${shellQuote(r.cwd)}\n` : '') + '$ ' + line, meta };
  }
  if (r.command) return { title: uiText("ui.ui.kit.steps.8b372c12c2"), code: (r.cwd ? `$ cd ${shellQuote(r.cwd)}\n` : '') + '$ ' + r.command, meta };
  if (r.handler && (r.kind || 'python') === 'python') return { title: uiText("ui.ui.kit.steps.c9e15d025f"), code: pythonCall(r.handler, args), meta };
  return { title: uiText("ui.ui.kit.steps.d0d47b7549"), code: pretty(r), meta };
}

/** metadata.operations rows: ["read", path], ["process", exe, argv], ["connect", addr]… */
export function nestedText(rows: Record<string, unknown>[] | unknown[]): string {
  return rows.map(row => {
    if (Array.isArray(row)) {
      const [op, ...rest] = row;
      if (op === 'process' && Array.isArray(rest[1])) return '$ ' + (rest[1] as unknown[]).map(a => shellQuote(String(a))).join(' ');
      return [String(op), ...rest.map(x => Array.isArray(x) ? x.join(' ') : String(x))].join('  ');
    }
    return typeof row === 'object' && row ? Object.entries(row).map(([k, v]) => `${k}: ${String(v)}`).join(' · ') : String(row);
  }).join('\n');
}

/** Five mutually exclusive views, with saved request and actual execution kept separate. */
export function toolTraceHTML(t: ToolDetail, key: string, runId: string): string {
  const missing=(text:string)=>`<p class="tool-trace-unrecorded">${esc(text)}</p>`;
  const panels:Record<string,string>={};
  const summary=lensHTML(t.lens,t.args);
  if(summary)panels.summary=summary+(t.outcome_source==='result'?uiText("ui.ui.kit.steps.9a428edea4"):'');
  panels.args=t.args!=null ? traceBlock(key+':args',uiText("ui.ui.kit.steps.751a142ab0"),pretty(t.args),'request') : missing(uiText("ui.ui.kit.steps.6625210452"));
  panels.definition=t.definition ? traceBlock(key+':definition',uiText("ui.ui.kit.steps.fb11f80f1c"),pretty(t.definition),'definition') : missing(uiText("ui.ui.kit.steps.8e4dd13b95"));
  panels.request=t.model_request ? traceBlock(key+':request',uiText("ui.ui.kit.steps.196ae6fa4e"),pretty(t.model_request),'request') : missing(uiText("ui.ui.kit.steps.7a2d979148"));
  const executions=[t.execution,...(t.operations||[])].filter((r):r is ExecutionReceipt=>!!r);
  panels.execution=executions.length ? executions.map((r,i)=>{
    const shown=executionText(r,t.args);
    return traceBlock(key+':execution:'+i,shown.title+(shown.meta?' · '+shown.meta:''),shown.code,'execution');
  }).join('') : missing(uiText("ui.ui.kit.steps.720e7fde0a"));
  if(t.nested?.length)panels.execution+=traceBlock(key+':nested',uiText("ui.ui.kit.steps.29070a58a5"),nestedText(t.nested),'execution');
  else if(executions.length && !executions.some(r=>r.argv||r.command||r.requested_argv))panels.execution+=missing(uiText("ui.ui.kit.steps.d581ee9cdf"));
  if(t.nested_cut)panels.execution+=missing(uiText("ui.ui.kit.steps.1b3df7b455"));
  if(executions.length)panels.execution+=details(key+':receipt',[[uiText("ui.ui.kit.steps.47466c9c39"),pretty(executions)]],uiText("ui.ui.kit.steps.bc17e81016"));
  const result=t.result?.truncated ? [t.result.head,uiText("ui.ui.kit.steps.e57971f1bd"),t.result.tail].filter(Boolean).join('\n') : String(t.result?.head ?? t.result?.tail ?? '');
  panels.response=t.result ? traceBlock(key+':result',uiText("ui.ui.kit.steps.7391ee5e19"),resultText(result),'response',{truncated:t.result.truncated,ref:t.result.result_id,run:runId,size:t.result.size,raw:true}) : missing(uiText("ui.ui.kit.steps.498d011d77"));
  if(t.model_observation!=null)panels.response+=!t.result?.truncated&&t.model_observation===result ? missing(uiText("ui.ui.kit.steps.7113c9969f")) : traceBlock(key+':model-response',uiText("ui.ui.kit.steps.cf5b7d94b4"),t.model_observation,'model-response');
  if(t.model_response_image_count)panels.response+=missing(uiText("ui.ui.kit.steps.49e3fc8f1e", {p0: String(t.model_response_image_count)}));
  if(t.error)panels.response+=traceBlock(key+':error',uiText("ui.ui.kit.steps.bf22dfeeba"),t.error,'error');
  return traceTabsHTML(runId+'/'+key,panels);
}

function details(key: string, parts: Array<[string, string]>, label = uiText("ui.ui.kit.steps.0fefbd67d8")): string {
  const body = parts.filter(([, value]) => value).map(([name, value]) =>
    `<div class="action-detail-label">${esc(name)}</div><pre>${esc(value)}</pre>`).join("");
  return body ? `<details class="action-details" data-detail="${esc(key)}"><summary>${esc(label)}</summary>${body}</details>` : "";
}

/** Повод человеческими словами: служебные префиксы продукта («Hélène: Сработал твой будильник.
 *  Намечено было вот что:», «[обещание] напоминание себе:») уходят в подпись, содержание остаётся. */
function humanOrigin(text: string, label: string): { text: string; label: string; kind: string } {
  let t = text.replace(/^(?:Hélène|Praxis|Праксис):\s*/u, "");
  let kind = "";
  const alarm = t.match(/^Сработал твой будильник[.:]\s*(?:Намечено было вот что:\s*|намечено\s+)?/iu);
  if (alarm) { t = t.slice(alarm[0].length); label = uiText("ui.ui.kit.steps.00ef5eb94e"); kind = "alarm"; }
  const promise = t.match(/^\[обещание\]\s*напоминание себе:\s*/iu);
  if (promise) { t = t.slice(promise[0].length); label = uiText("ui.ui.kit.steps.a9d789f09a"); kind = "promise"; }
  return { text: t.trim() || text, label, kind };
}

/** Ссылки под поводом: место в чате и, для напоминаний, просьба агенту словами (в композер, не отправка). */
function originLinks(d: RunDetail | undefined, kind: string, goal: string): string {
  if (!d) return "";
  const raw = String(d.manifest?.chat_id ?? "");
  const room = raw === LEGACY_WINDOW_KEY ? "window" : raw;
  const at = d.manifest?.created_at || "";
  const links: string[] = [];
  if (room) links.push(uiText("ui.ui.kit.steps.c657bfc303", {p0: String(esc(room)), p1: String(esc(at))}));
  if (room && (kind === "alarm" || kind === "promise")) {
    const g = clip(goal.replace(/\s+/g, " ").trim(), 200);
    links.push(uiText("ui.ui.kit.steps.7aaa8fc847", {p0: String(esc(room)), p1: String(esc(uiText("ui.ui.kit.steps.b7cf9f1af8", {p0: String(g)})))}));
    links.push(uiText("ui.ui.kit.steps.6ebe9225d6", {p0: String(esc(room)), p1: String(esc(uiText("ui.ui.kit.steps.a9c5ba482f", {p0: String(g)})))}));
  }
  return links.length ? `<div class="run-links">${links.join(" · ")}</div>` : "";
}

function readable(text: string, label: string, key: string, d?: RunDetail): string {
  if (!text) return "";
  let kind = "";
  if (key === "origin") ({ text, label, kind } = humanOrigin(text, label));
  const body = `<div class="run-reading md">${md(text)}</div>`;
  const links = key === "origin" ? originLinks(d, kind, text) : "";
  return `<section class="run-message"><div class="action-detail-label">${label}</div>${text.length > 320 ? uiText("ui.ui.kit.steps.3db6274b13", {p0: String(key), p1: String(md(clip(text, 240))), p2: String(body)}) : body}${links}</section>`;
}

// ---------------------------------------------------------------- длинные тексты
// Результат руки или её слово показываются целиком, а не обрубком в 280 знаков: длинный
// текст складывается до 12 строк с кнопкой «Показать целиком» (без прокрутки внутри
// коробки), а если сервер сохранил только голову и хвост (inline 2000 знаков), кнопка
// дочитывает файл результата через /api/run/{run}/result/{id}. Слово владельца 08.09:
// «не разворачивается текстовое окошко с ответом или выводом, если они длинные очень».
export type ResultFetcher = (runId: string, resultId: string) => Promise<{ text?: string; model_text?: string; complete?: boolean } | null>;
let fetchResult: ResultFetcher | null = null;
const fetched = new Map<string, string>();
const CLIP_LINES = 12;
const CLIP_CHARS = 900;

export function setResultFetcher(fn: ResultFetcher): void {
  fetchResult = fn;
}

const lineCount = (s: string) => s.split("\n").length;
const needsClip = (text: string) => text.length > CLIP_CHARS || lineCount(text) > CLIP_LINES;

/** Коробка текста: ключ сохраняет раскрытие при перерисовке; data-full — что дочитать. */
export function textBox(key: string, text: string, opts: { truncated?: boolean; ref?: string; run?: string; size?: number | null; raw?: boolean } = {}): string {
  const full = opts.ref && opts.run ? fetched.get(`${opts.run}/${opts.ref}/${opts.raw ? "raw" : "model"}`) : undefined;
  const body = full ?? text;
  const truncated = !!opts.truncated && full === undefined;
  const folded = needsClip(body) || truncated;
  const more = truncated
    ? uiText("ui.ui.kit.steps.0e01cda91f", {p0: String(opts.size ? uiText("ui.ui.kit.steps.34f5770325", {p0: String(fmtK(opts.size))}) : "")})
    : uiText("ui.ui.kit.steps.2247a80575", {p0: String(lineCount(body))});
  const fullAttr = truncated && opts.ref && opts.run ? ` data-full="${esc(opts.run)}/${esc(opts.ref)}/${opts.raw ? "raw" : "model"}"` : "";
  return `<div class="run-text" data-text="${esc(key)}">` +
    `<pre class="run-text-body ${folded ? "clipped" : ""}">${esc(body)}</pre>` +
    (folded ? `<button type="button" class="run-text-more" data-more="${esc(key)}"${fullAttr}>${more}</button>` : "") +
    `</div>`;
}

function textOf(payload: { text?: string; model_text?: string } | null, rawResult=false): string {
  if (!payload) return "";
  if(!rawResult && payload.model_text)return payload.model_text;
  const raw = payload.text ?? payload.model_text ?? "";
  try {
    return pretty(JSON.parse(raw));
  } catch {
    return raw;
  }
}

let bound = false;
function bindMore(): void {
  if (bound || typeof document === "undefined") return;
  bound = true;
  document.addEventListener('keydown',event=>{
    const button=(event.target as HTMLElement)?.closest<HTMLElement>('[data-trace-tab]');
    if(!button || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const buttons=[...button.parentElement!.querySelectorAll<HTMLElement>('[data-trace-tab]')];
    const i=buttons.indexOf(button), next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    event.preventDefault();buttons[next].focus();selectTraceTab(buttons[next]);
  });
  document.addEventListener("click", (e) => {
    const target = e.target as HTMLElement | null;
    const tab = target?.closest<HTMLElement>('[data-trace-tab]');
    if(tab){e.preventDefault();selectTraceTab(tab);return;}
    const copy = target?.closest<HTMLButtonElement>('button[data-copy-block]');
    if (copy) {
      e.preventDefault();
      const text = copy.closest('.tool-trace-block')?.querySelector('.run-text-body')?.textContent;
      if (text != null) {
        copyText(text).then((ok) => { copy.textContent = ok ? uiText("ui.ui.kit.steps.d2d08c715e") : uiText("ui.ui.kit.steps.b96e4f8504"); },
          () => { copy.textContent = uiText("ui.ui.kit.steps.b96e4f8504"); });
      } else copy.textContent = uiText("ui.ui.kit.steps.324df365aa");
      return;
    }
    // Ссылки с карточки: хозяин ленты (окно, телефон) решает, как открыть чат и композер.
    const open = target?.closest<HTMLAnchorElement>("a[data-open-room]");
    if (open) {
      e.preventDefault();
      window.dispatchEvent(new CustomEvent("steps-open", { detail: { room: open.dataset.openRoom || "", at: open.dataset.at || "" } }));
      return;
    }
    const compose = target?.closest<HTMLAnchorElement>("a[data-compose]");
    if (compose) {
      e.preventDefault();
      window.dispatchEvent(new CustomEvent("steps-compose", { detail: { room: compose.dataset.compose || "", text: compose.dataset.text || "" } }));
      return;
    }
    const btn = target?.closest<HTMLButtonElement>("button[data-more]");
    if (!btn) return;
    e.preventDefault();
    const box = btn.closest<HTMLElement>(".run-text");
    const pre = box?.querySelector<HTMLElement>(".run-text-body");
    if (!box || !pre) return;
    const full = btn.dataset.full;
    if (full && !fetched.has(full) && fetchResult) {
      const [run, rid, mode] = full.split("/");
      btn.disabled = true;
      btn.textContent = uiText("ui.ui.kit.steps.b7eefd36b7");
      fetchResult(run, rid).then((payload) => {
        const text = textOf(payload, mode === "raw");
        if (text) {
          fetched.set(full, text);
          pre.textContent = text;
          const copy = box.closest('.tool-trace-block')?.querySelector<HTMLButtonElement>('[data-copy-block]');
          if (copy) copy.textContent = uiText("ui.ui.kit.steps.1ecddf40bf");
        }
        pre.classList.remove("clipped");
        btn.disabled = false;
        btn.textContent = text ? uiText("ui.ui.kit.steps.36c25d9ffc") : uiText("ui.ui.kit.steps.ccdc285fe7");
      }, () => {
        btn.disabled = false;
        btn.textContent = uiText("ui.ui.kit.steps.1e9ec28943");
      });
      return;
    }
    const closed = pre.classList.toggle("clipped");
    btn.textContent = closed ? uiText("ui.ui.kit.steps.2247a80575", {p0: String(lineCount(pre.textContent || ""))}) : uiText("ui.ui.kit.steps.36c25d9ffc");
  });
}
bindMore();

// ---------------------------------------------------------------- лента шагов

/** Шаги хода: один блок на итерацию модели — номер, время, токены, затем руки и слово. */
export function modelProgressLabel(it: NonNullable<RunDetail["iterations"]>[number], now = Date.now()): string {
  const p = it.progress;
  if (p?.outcome !== "streaming" || !Number.isFinite(p.argument_bytes) || !Number.isFinite(p.attempt_ms)) return "";
  const age = it.progress_at ? now - Date.parse(it.progress_at) : Infinity;
  const label = age >= 0 && age < 15000 ? uiText("ui.ui.kit.steps.ae4b3e99e1") : uiText("ui.ui.kit.steps.7a8490e569");
  return uiText("ui.ui.kit.steps.389a300c57", {p0: String(label), p1: decimal((p.argument_bytes || 0) / 1024), p2: decimal((p.attempt_ms || 0) / 60000)});
}

/** Live words of a running model call (Tim: local models at a few tokens/s). Thinking folds;
 *  the reply being written and plain text show as they grow. Nothing here is sent yet. */
export function modelPreviewHTML(it: NonNullable<RunDetail["iterations"]>[number], key: string, live: boolean): string {
  const p = it.preview;
  if (!p || !live || it.text || it.status === "failed" || it.status === "completed") return "";
  const out: string[] = [];
  const count = (value: number | undefined, text: string) => String(Number.isFinite(value) ? value : text.length);
  if (p.thinking) out.push(`<details class="action-details model-preview-thinking" data-detail="${esc("thinking:" + key)}"><summary>${esc(uiText("ui.ui.kit.steps.preview_thinking", {p0: count(p.thinking_chars, p.thinking)}))}</summary><pre>${esc(p.thinking)}</pre></details>`);
  if (p.reply) out.push(`<div class="model-preview" data-model-preview="reply"><div class="step-meta">${esc(uiText("ui.ui.kit.steps.preview_reply", {p0: count(p.reply_chars, p.reply)}))}</div><pre class="model-preview-text">${esc(p.reply)}</pre></div>`);
  if (p.text) out.push(`<div class="model-preview" data-model-preview="text"><div class="step-meta">${esc(uiText("ui.ui.kit.steps.preview_text", {p0: count(p.text_chars, p.text)}))}</div><pre class="model-preview-text">${esc(p.text)}</pre></div>`);
  return out.join("");
}

export function stepsHTML(d: RunDetail, opts: StepsOptions = {}): string {
  const steps: string[] = [];
  const L = opts.lesson;
  const lesson = (key: string) => (L && L[key] ? `<div class="lesson">${esc(L[key])}</div>` : "");
  const live = d.manifest?.status === "running";
  const runId = d.id || "";
  let hands = 0;
  (d.iterations || []).forEach((it, i) => {
    const n = i + 1;
    const u = it.usage || {};
    const cached = u.cache_read || 0;
    const total = (u.in || 0) + cached;
    const share = total ? Math.round((100 * cached) / total) : 0;
    const key = it.call_id || String(it.seq ?? n);
    const complete = it.status === "completed" || (it.status !== "failed" && it.ms != null);
    const thinking = !complete && it.status !== "failed" && !it.tools?.length && live;
    const cut = complete && it.stop === "max_tokens";
    const status = it.status === "failed" ? uiText("ui.ui.kit.steps.286475bfb9") : cut ? (it.text_chars ? uiText("ui.ui.kit.steps.52dcc83822") : uiText("ui.ui.kit.steps.2682d29e0f")) : thinking ? uiText("ui.ui.kit.steps.8176f94e66") : "";
    const seconds = it.ms != null ? uiText("ui.ui.kit.steps.baf1ec7874", {p0: decimal(it.ms / 1000)}) : "";
    const tokens = u.in != null ? `${fmtK(total)}${cached ? uiText("ui.ui.kit.steps.bd22fee03c", {p0: String(share)}) : ""} → ${fmtK(u.out || 0)}` : "";
    const meta = [seconds, tokens].filter(Boolean).join(" · ");
    const parts: string[] = [];
    parts.push(uiText("ui.ui.kit.steps.21931b9dc9", {p0: String(n), p1: String(meta ? `<span class="step-meta">${esc(meta)}</span>` : ""), p2: String(status ? `<span class="action-status">${esc(status)}</span>` : "")}));
    const progress = thinking ? modelProgressLabel(it) : "";
    if (progress) parts.push(`<div class="step-meta" data-model-progress>${esc(progress)}</div>`);
    parts.push(modelPreviewHTML(it, key, live));
    parts.push(details("model:" + key, [[uiText("ui.ui.kit.steps.f3bdf7cce3"), it.model || ""], [uiText("ui.ui.kit.steps.6711f073ab"), seconds],
      [uiText("ui.ui.kit.steps.98b7dc4147"), u.in != null ? uiText("ui.ui.kit.steps.74ec630d1f", {p0: String(fmtK(total)), p1: String(cached ? uiText("ui.ui.kit.steps.bd22fee03c", {p0: String(share)}) : ""), p2: String(fmtK(u.out || 0))}) : ""],
      [uiText("ui.ui.kit.steps.065a18f569"), cut ? uiText("ui.ui.kit.steps.dcdb345a29") : ""], [uiText("ui.ui.kit.steps.bf22dfeeba"), it.error || ""]], uiText("ui.ui.kit.steps.3c17ab48d8")));
    parts.push(lesson(n === 1 ? "think_first" : "think"));
    for (const t of it.tools || []) {
      hands += 1;
      const received = t.result != null || t.status === "received";
      const failed = t.status === "failed";
      const active = !received && !failed && live;
      // Шаг доставки ядра с нулём знаков — не её действие и не «получила ноль»: на границе
      // прогона доставлять было нечего (слово ушло рукой reply или границей окна).
      const emptyDelivery = t.tool === "telegram.deliver" && (t.args as { text_chars?: number } | null)?.text_chars === 0 && !(t.args as { media_count?: number } | null)?.media_count;
      const tstatus = failed ? uiText("ui.ui.kit.steps.44d4090ae9") : emptyDelivery ? uiText("ui.ui.kit.steps.d30323a8b4") : received ? uiText("ui.ui.kit.steps.6c455b8924") : active ? uiText("ui.ui.kit.steps.bd280ebe5b") : uiText("ui.ui.kit.steps.c394012b6f");
      const title = emptyDelivery ? uiText("ui.ui.kit.steps.9b8f19a36a") : ACTIONS[t.tool || ""] || t.tool || uiText("ui.ui.kit.steps.6b53aafe28");
      const lessonKey = t.tool === "reply" ? "reply" : t.tool === "end_turn" ? "end_turn" : "hand";
      const tkey = "tool:" + (t.call_id || String(t.seq ?? key + ":" + hands));
      parts.push(
        `<div class="tool-action ${active ? "action-active" : ""} ${failed ? "action-failed" : ""}">` +
          `<div class="action-head"><b class="action-title">${esc(title)}</b><span class="action-status">${tstatus}</span></div>` +
          `<code class="tool-name">${esc(t.tool || '')}</code>` + toolTraceHTML(t, tkey, runId) +
          lesson(lessonKey) +
          `</div>`,
      );
    }
    if (it.text) {
      parts.push(uiText("ui.ui.kit.steps.c7ed4117dc", {p0: String(textBox("text:" + key, it.text, { truncated: !!it.text_truncated, ref: it.text_ref, run: runId }))}));
    } else if (it.text_ref) {
      parts.push(uiText("ui.ui.kit.steps.ebfe71b9ca", {p0: String(textBox("text:" + key, "", { truncated: true, ref: it.text_ref, run: runId }))}));
    }
    steps.push(`<div class="ev-step step ${thinking ? "action-active" : ""} ${cut || it.status === "failed" ? "action-failed" : ""}">${parts.join("")}</div>`);
  });
  const term = d.manifest?.terminal || {};
  const tail: string[] = [];
  if (term.status) {
    const t = terminalLabel(term.status, term.reason || "");
    tail.push(
      `<div class="ev-step ${t.failed ? "action-failed" : ""}"><b class="action-title">${esc(t.label)}</b>${details("terminal", [[uiText("ui.ui.kit.steps.442e7ad547"), term.reason || ""]])}${lesson("terminal")}</div>`,
    );
  }
  const origin = readable(runOriginText(d.origin), uiText("ui.ui.kit.steps.454699fd11"), "origin", d) || (d.origin?.source === "unknown" ? uiText("ui.ui.kit.steps.5b70d80713") : "");
  const outcome = readable(d.outcome?.text || d.outcome?.note || "", uiText("ui.ui.kit.steps.4706e4a4a3"), "outcome");
  let actions = steps.join("") || `<div class="muted">${live ? uiText("ui.ui.kit.steps.60af385604") : uiText("ui.ui.kit.steps.28e6d08b30")}</div>`;
  if (opts.limit && steps.length > opts.limit) {
    // Счёт один и тот же везде: шаги = итерации модели, не сумма рук и не строки ленты.
    const hidden = steps.length - opts.limit;
    actions = uiText("ui.ui.kit.steps.6949a0a079", {p0: String(hidden), p1: String(steps.length), p2: String(steps.slice(0, -opts.limit).join(""))}) + steps.slice(-opts.limit).join("");
  }
  actions += tail.join("");
  if (opts.overview) {
    const count = steps.length ? ` · ${steps.length}${hands ? uiText("ui.ui.kit.steps.8e103f4247", {p0: String(hands)}) : ""}` : "";
    return origin + outcome + uiText("ui.ui.kit.steps.59a5a4320b", {p0: String(live ? "open" : ""), p1: String(count), p2: String(actions)});
  }
  return origin + actions + outcome;
}

/** Обновлять содержимое только при изменении, сохраняя раскрытие, развёрнутые тексты и фокус. */
const rendered = new WeakMap<HTMLElement, string>();
export function renderSteps(box: HTMLElement, d: RunDetail, opts: StepsOptions = {}): void {
  const html = stepsHTML(d, opts);
  if (rendered.get(box) === html) return;
  const expanded = new Map([...box.querySelectorAll<HTMLDetailsElement>("details[data-detail]")].map((el) => [el.dataset.detail, el.open]));
  const unclipped = new Set([...box.querySelectorAll<HTMLElement>(".run-text")].filter((el) => !el.querySelector(".run-text-body.clipped")).map((el) => el.dataset.text));
  const focused = box.contains(document.activeElement) ? document.activeElement?.closest<HTMLElement>("[data-detail]")?.dataset.detail : undefined;
  box.innerHTML = html;
  rendered.set(box, html);
  for (const el of box.querySelectorAll<HTMLDetailsElement>("details[data-detail]")) {
    if (expanded.has(el.dataset.detail)) el.open = expanded.get(el.dataset.detail)!;
    if (focused === el.dataset.detail) el.querySelector("summary")?.focus({ preventScroll: true });
  }
  for (const el of box.querySelectorAll<HTMLElement>(".run-text")) {
    if (!unclipped.has(el.dataset.text)) continue;
    el.querySelector(".run-text-body")?.classList.remove("clipped");
    const btn = el.querySelector<HTMLButtonElement>(".run-text-more");
    if (btn && !btn.dataset.full) btn.textContent = uiText("ui.ui.kit.steps.36c25d9ffc");
  }
}

/** Последнее использование модели в прогоне: цена кадра. */
export function lastUsage(d: RunDetail | undefined): { in: number; cached: number; out: number; model: string } | null {
  const its = d?.iterations || [];
  for (let i = its.length - 1; i >= 0; i--) {
    const u = its[i].usage;
    if (u && (u.in != null || u.cache_read != null)) {
      return { in: u.in || 0, cached: u.cache_read || 0, out: u.out || 0, model: its[i].model || "" };
    }
  }
  return null;
}

/** Полоса кадра: из кэша / заново / ответ. */
export function frameStripHTML(d: RunDetail | undefined, title: string, link = ""): string {
  const u = lastUsage(d);
  if (!u) return "";
  const total = u.in + u.cached;
  const share = total ? Math.round((100 * u.cached) / total) : 0;
  const all = total + u.out || 1;
  const w = (n: number) => `${Math.max(0, (100 * n) / all).toFixed(2)}%`;
  return uiText("ui.ui.kit.steps.a215cbe837", {p0: String(esc(title)), p1: String(link), p2: String(w(u.cached)), p3: String(w(u.in)), p4: String(w(u.out)), p5: String(fmtK(u.cached)), p6: String(share), p7: String(fmtK(u.in)), p8: String(fmtK(u.out))});
}
