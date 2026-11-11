// Шаги одного прогона агента — одна разметка для окна (панель хода, урок на
// экране «Система») и для телефона. Данные — `GET /api/run/{id}`.
import { esc, fmtK, md } from "./text";
import { copyText } from "./dom";

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
  origin?: { text: string; source: string };
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
    /** stop_reason модели: max_tokens — ответ оборван потолком, а не получен. */
    stop?: string;
    text_chars?: number;
    text?: string;
    /** Длинное слово сохранено файлом результата: дочитать через /api/run/{id}/result/{ref}. */
    text_ref?: string;
    text_truncated?: boolean;
    usage?: { in?: number; cache_read?: number; out?: number };
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
  shell: "Команда", read_file: "Чтение файла", write_file: "Запись файла",
  edit_file: "Правка файла", search: "Поиск", web_search: "Поиск в интернете",
  fetch_url: "Чтение страницы", read_url: "Чтение страницы",
  reply: "Ответ", end_turn: "Завершение работы", remind_self: "Напоминание",
  recall: "Поиск в памяти", remember: "Запись в память", read_memory: "Чтение памяти",
  coding_task: "Задача разработки", coding_agent: "Помощник по коду",
  coding_process: "Процесс", coding_read: "Чтение кода", coding_search: "Поиск в коде",
  coding_patch: "Правка кода", coding_verify: "Проверка", coding_finish: "Завершение задачи",
  write_skill: "Запись навыка", consolidate_context: "Свёртка контекста",
  computer: "Компьютер", read_run_result: "Дочитать результат", journal: "Дневник",
  wake: "Будильник", set_wake: "Будильник", sleep: "Сон", voice: "Голос", speak: "Голос",
  browser: "Браузер", screenshot: "Снимок экрана", send_file: "Отправка файла",
  "telegram.deliver": "Доставка сообщения",
};
const TERMINAL: Record<string, string> = {
  done: "Работа завершена", completed: "Работа завершена", failed: "Работа остановилась с ошибкой",
  cancelled: "Работа отменена", canceled: "Работа отменена", paused: "Работа приостановлена",
  in_doubt: "Нужно проверить результат", blocked: "Есть препятствие",
};
/** Причина завершения человеческими словами. `done` бывает разным: оборвалась потолком —
 *  не то же, что «решила промолчать». Ход без руки reply НЕ помечается «без ответа»:
 *  в Hélène слово, написанное текстом, доставляет граница окна, а прогон ядра об этом
 *  не знает — такой ход остаётся просто «Работа завершена». */
export function terminalLabel(status: string, reason: string): { label: string; failed: boolean } {
  const r = (reason || "").toLowerCase();
  if (status === "done" || status === "completed") {
    if (r.includes("max_tokens")) return { label: "Ответ оборван потолком, работа не доведена", failed: true };
    if (r === "silent decision") return { label: "Завершено: решила промолчать", failed: false };
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

export function subject(args: unknown): string {
  if (!args || typeof args !== "object") return typeof args === "string" ? args : "";
  const a = args as Record<string, unknown>;
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
    typeof fields.cmd === "string" ? fields.cmd : "";
}

export type ToolDetail = NonNullable<NonNullable<RunDetail['iterations']>[number]['tools']>[number];

/** A readable projection; the original input remains available below it. */
export function toolParameters(args: unknown): Array<[string, unknown]> {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return [];
  const command = suppliedCommand(args);
  return Object.entries(args).filter(([key, value]) =>
    !((key === 'command' || key === 'cmd') && typeof value === 'string' && value === command));
}

function traceBlock(key: string, label: string, content: string, kind: string, opts: Parameters<typeof textBox>[2] = {}): string {
  return `<section class="tool-trace-block tool-trace-${kind}"><div class="tool-trace-heading"><span>${esc(label)}</span>` +
    `<button type="button" class="tool-copy" data-copy-block aria-label="Копировать: ${esc(label)}">${opts.truncated ? 'Копировать фрагмент' : 'Копировать'}</button></div>` +
    textBox(key, content, opts) + `</section>`;
}

/** Outcome comes from the journal. A received payload alone never proves success. */
export function toolSummary(t: ToolDetail, live = false) {
  const known = ['ok','confirmed','failed','refused','in_doubt','partial','unknown'];
  const received = t.result != null || t.status === 'received' || !!t.finished_at;
  const active = !received && t.status !== 'failed' && live;
  const outcome = t.status === 'failed' ? 'failed' : known.includes(t.outcome || '') ? t.outcome! : 'unknown';
  const labels: Record<string,string> = {ok:'успех',confirmed:'подтверждено',failed:'ошибка',refused:'отказ',in_doubt:'нужна проверка',partial:'частично',unknown:'исход неизвестен'};
  const icons: Record<string,string> = {ok:'✓',confirmed:'✓',failed:'✗',refused:'⊘',in_doubt:'?',partial:'◐',unknown:'·'};
  const receipt = [t.execution, ...(t.operations || [])].filter(r => r?.exit_code != null).at(-1);
  const state = active ? 'выполняется' : receipt?.exit_code != null ? `exit ${receipt.exit_code}${['ok','confirmed','failed'].includes(outcome) ? '' : ' · '+labels[outcome]}` : ['ok','confirmed'].includes(outcome) ? '' : labels[outcome];
  const elapsed = Date.parse(t.finished_at || '') - Date.parse(t.at || '');
  const seconds = Number.isFinite(elapsed) && elapsed >= 0 ? elapsed/1000 : null;
  const duration = seconds == null ? '' : seconds < 60 ? `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} с` : `${Math.floor(seconds/60)} мин ${Math.round(seconds%60)} с`;
  return {outcome,active,state,icon:active ? '◌' : icons[outcome],label:active ? 'выполняется' : labels[outcome],duration,
    target:t.target || subject(t.args),failed:['failed','refused'].includes(outcome),attention:['partial','in_doubt'].includes(outcome)};
}

const selectedTraceTabs = new Map<string,string>();
export const traceTabNames = [['args','Аргументы'],['definition','Функция'],['execution','Исполнение'],['request','JSON-запрос'],['response','JSON-ответ']] as const;
export function traceTabsHTML(key:string, panels: Record<string,string>): string {
  const selected = selectedTraceTabs.get(key) || 'args';
  const id = 'trace-' + encodeURIComponent(key);
  return `<div class="tool-trace" data-trace-key="${esc(key)}"><div class="tool-tabs" role="tablist" aria-label="Детали вызова">` +
    traceTabNames.map(([tab,label])=>`<button type="button" role="tab" id="${id}-${tab}" aria-controls="${id}-${tab}-panel" aria-selected="${tab===selected}" tabindex="${tab===selected?0:-1}" data-trace-tab="${tab}" aria-label="${label}">${label.startsWith('JSON-')?`<span class="tab-long">${label}</span><span class="tab-short">${label[5].toUpperCase()+label.slice(6)}</span>`:label}</button>`).join('') + '</div>' +
    traceTabNames.map(([tab])=>`<section role="tabpanel" id="${id}-${tab}-panel" aria-labelledby="${id}-${tab}" data-trace-panel="${tab}" ${tab===selected?'':'hidden'}>${panels[tab] || '<p class="tool-trace-unrecorded">Не записано в журнале.</p>'}</section>`).join('') + '</div>';
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
  entered: 'обработчик вызван', returned: 'процесс завершился', timed_out: 'истекло время ожидания',
  launch_failed: 'запуск не состоялся', running: 'выполняется', failed: 'ошибка', cancelled: 'отменено', started: 'запущен',
};

/** One receipt as the command it records: Python call, process line or bare harness note. */
export function executionText(r: ExecutionReceipt, args: unknown): { title: string; code: string; meta: string } {
  const meta = [r.exit_code != null ? '' : RECEIPT_STATE[r.status || ''] || r.status, r.exit_code != null ? `exit ${r.exit_code}` : '',
    r.pid != null ? `PID ${r.pid}` : '', r.error || ''].filter(Boolean).join(' · ');
  if (r.argv?.length || r.requested_argv?.length) {
    const argv = r.argv?.length ? r.argv : r.requested_argv!;
    const line = argv.map(shellQuote).join(' ');
    return { title: r.argv?.length ? 'Процесс' : 'Попытка запуска (процесс не стартовал)',
      code: (r.cwd ? `$ cd ${shellQuote(r.cwd)}\n` : '') + '$ ' + line, meta };
  }
  if (r.command) return { title: 'Команда', code: (r.cwd ? `$ cd ${shellQuote(r.cwd)}\n` : '') + '$ ' + r.command, meta };
  if (r.handler && (r.kind || 'python') === 'python') return { title: 'Вызов Python', code: pythonCall(r.handler, args), meta };
  return { title: 'Исполнение харнесса', code: pretty(r), meta };
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
  panels.args=t.args!=null ? traceBlock(key+':args','Аргументы',pretty(t.args),'request') : missing('Аргументы не записаны.');
  panels.definition=t.definition ? traceBlock(key+':definition','Схема инструмента',pretty(t.definition),'definition') : missing('Схема инструмента в этом журнале не записана.');
  panels.request=t.model_request ? traceBlock(key+':request','Сохранённый JSON-запрос модели',pretty(t.model_request),'request') : missing('Исходный JSON-запрос модели не записан. Аргументы доступны отдельно.');
  const executions=[t.execution,...(t.operations||[])].filter((r):r is ExecutionReceipt=>!!r);
  panels.execution=executions.length ? executions.map((r,i)=>{
    const shown=executionText(r,t.args);
    return traceBlock(key+':execution:'+i,shown.title+(shown.meta?' · '+shown.meta:''),shown.code,'execution');
  }).join('') : missing('Фактическое исполнение в этом журнале не записано.');
  if(t.nested?.length)panels.execution+=traceBlock(key+':nested','Что рука сделала внутри',nestedText(t.nested),'execution');
  else if(executions.length && !executions.some(r=>r.argv||r.command||r.requested_argv))panels.execution+=missing('Внутренних процессов, файлов и соединений журнал за этим вызовом не записал.');
  if(t.nested_cut)panels.execution+=missing('Список вложенных действий сокращён.');
  if(executions.length)panels.execution+=details(key+':receipt',[['Квитанции исполнения',pretty(executions)]],'Квитанции · JSON');
  const result=t.result?.truncated ? [t.result.head,'… пропущена часть результата …',t.result.tail].filter(Boolean).join('\n') : String(t.result?.head ?? t.result?.tail ?? '');
  panels.response=t.result ? traceBlock(key+':result','Ответ инструмента',resultText(result),'response',{truncated:t.result.truncated,ref:t.result.result_id,run:runId,size:t.result.size,raw:true}) : missing('Ответ инструмента ещё не записан.');
  if(t.model_observation!=null)panels.response+=!t.result?.truncated&&t.model_observation===result ? missing('Модель получила этот же ответ; отдельной приписки нет.') : traceBlock(key+':model-response','Ответ для модели с припиской харнесса',t.model_observation,'model-response');
  if(t.model_response_image_count)panels.response+=missing(`К ответу приложено изображений: ${t.model_response_image_count}`);
  if(t.error)panels.response+=traceBlock(key+':error','Ошибка',t.error,'error');
  return traceTabsHTML(runId+'/'+key,panels);
}

function details(key: string, parts: Array<[string, string]>, label = "Подробности"): string {
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
  if (alarm) { t = t.slice(alarm[0].length); label = "Будильник"; kind = "alarm"; }
  const promise = t.match(/^\[обещание\]\s*напоминание себе:\s*/iu);
  if (promise) { t = t.slice(promise[0].length); label = "Напоминание себе"; kind = "promise"; }
  return { text: t.trim() || text, label, kind };
}

/** Ссылки под поводом: место в чате и, для напоминаний, просьба агенту словами (в композер, не отправка). */
function originLinks(d: RunDetail | undefined, kind: string, goal: string): string {
  if (!d) return "";
  const raw = String(d.manifest?.chat_id ?? "");
  const room = raw === LEGACY_WINDOW_KEY ? "window" : raw;
  const at = d.manifest?.created_at || "";
  const links: string[] = [];
  if (room) links.push(`<a href="#" data-open-room="${esc(room)}" data-at="${esc(at)}">Открыть в чате →</a>`);
  if (room && (kind === "alarm" || kind === "promise")) {
    const g = clip(goal.replace(/\s+/g, " ").trim(), 200);
    links.push(`<a href="#" data-compose="${esc(room)}" data-text="${esc(`Повтори это напоминание через 10 минут: «${g}»`)}">Повторить через 10 минут</a>`);
    links.push(`<a href="#" data-compose="${esc(room)}" data-text="${esc(`Сними напоминание: «${g}»`)}">Снять</a>`);
  }
  return links.length ? `<div class="run-links">${links.join(" · ")}</div>` : "";
}

function readable(text: string, label: string, key: string, d?: RunDetail): string {
  if (!text) return "";
  let kind = "";
  if (key === "origin") ({ text, label, kind } = humanOrigin(text, label));
  const body = `<div class="run-reading md">${md(text)}</div>`;
  const links = key === "origin" ? originLinks(d, kind, text) : "";
  return `<section class="run-message"><div class="action-detail-label">${label}</div>${text.length > 320 ? `<details data-detail="${key}" class="run-reading-more"><summary><div class="run-reading-preview">${md(clip(text, 240))}</div><span>Читать полностью</span></summary>${body}</details>` : body}${links}</section>`;
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
    ? `Показать целиком${opts.size ? ` · ${fmtK(opts.size)} байт` : ""}`
    : `Показать целиком · ${lineCount(body)} строк`;
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
        copyText(text).then((ok) => { copy.textContent = ok ? 'Скопировано' : 'Не скопировалось'; },
          () => { copy.textContent = 'Не скопировалось'; });
      } else copy.textContent = 'Копирование недоступно';
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
      btn.textContent = "читаю…";
      fetchResult(run, rid).then((payload) => {
        const text = textOf(payload, mode === "raw");
        if (text) {
          fetched.set(full, text);
          pre.textContent = text;
          const copy = box.closest('.tool-trace-block')?.querySelector<HTMLButtonElement>('[data-copy-block]');
          if (copy) copy.textContent = 'Копировать';
        }
        pre.classList.remove("clipped");
        btn.disabled = false;
        btn.textContent = text ? "Свернуть" : "Файл результата не прочитался";
      }, () => {
        btn.disabled = false;
        btn.textContent = "Не прочиталось, попробовать ещё";
      });
      return;
    }
    const closed = pre.classList.toggle("clipped");
    btn.textContent = closed ? `Показать целиком · ${lineCount(pre.textContent || "")} строк` : "Свернуть";
  });
}
bindMore();

// ---------------------------------------------------------------- лента шагов

/** Шаги хода: один блок на итерацию модели — номер, время, токены, затем руки и слово. */
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
    const status = it.status === "failed" ? "ошибка модели" : cut ? (it.text_chars ? "ответ оборван потолком, фраза не закончена" : "ответ оборван потолком, ни слова не дошло") : thinking ? "думает" : "";
    const seconds = it.ms != null ? `${(it.ms / 1000).toFixed(1)} с` : "";
    const tokens = u.in != null ? `${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ${fmtK(u.out || 0)}` : "";
    const meta = [seconds, tokens].filter(Boolean).join(" · ");
    const parts: string[] = [];
    parts.push(`<div class="step-head"><span class="step-n">Шаг ${n}</span>${meta ? `<span class="step-meta">${esc(meta)}</span>` : ""}${status ? `<span class="action-status">${esc(status)}</span>` : ""}</div>`);
    parts.push(details("model:" + key, [["Модель", it.model || ""], ["Время", seconds],
      ["Токены", u.in != null ? `вход ${fmtK(total)}${cached ? ` (кэш ${share}%)` : ""} → ответ ${fmtK(u.out || 0)}` : ""],
      ["Остановка", cut ? "max_tokens: потолок ответа исчерпан размышлением или длинным ответом" : ""], ["Ошибка", it.error || ""]], "Подробности шага"));
    parts.push(lesson(n === 1 ? "think_first" : "think"));
    for (const t of it.tools || []) {
      hands += 1;
      const received = t.result != null || t.status === "received";
      const failed = t.status === "failed";
      const active = !received && !failed && live;
      // Шаг доставки ядра с нулём знаков — не её действие и не «получила ноль»: на границе
      // прогона доставлять было нечего (слово ушло рукой reply или границей окна).
      const emptyDelivery = t.tool === "telegram.deliver" && (t.args as { text_chars?: number } | null)?.text_chars === 0 && !(t.args as { media_count?: number } | null)?.media_count;
      const tstatus = failed ? "ошибка" : emptyDelivery ? "нечего доставлять" : received ? "результат получен" : active ? "выполняется" : "результат неизвестен";
      const title = emptyDelivery ? "Доставка ядра" : ACTIONS[t.tool || ""] || t.tool || "Действие";
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
      parts.push(`<div class="hand word"><div class="action-head"><b class="action-title">Слово</b></div>${textBox("text:" + key, it.text, { truncated: !!it.text_truncated, ref: it.text_ref, run: runId })}</div>`);
    } else if (it.text_ref) {
      parts.push(`<div class="hand word"><div class="action-head"><b class="action-title">Слово</b><span class="action-status">длинный ответ сохранён файлом</span></div>${textBox("text:" + key, "", { truncated: true, ref: it.text_ref, run: runId })}</div>`);
    }
    steps.push(`<div class="ev-step step ${thinking ? "action-active" : ""} ${cut || it.status === "failed" ? "action-failed" : ""}">${parts.join("")}</div>`);
  });
  const term = d.manifest?.terminal || {};
  const tail: string[] = [];
  if (term.status) {
    const t = terminalLabel(term.status, term.reason || "");
    tail.push(
      `<div class="ev-step ${t.failed ? "action-failed" : ""}"><b class="action-title">${esc(t.label)}</b>${details("terminal", [["Причина", term.reason || ""]])}${lesson("terminal")}</div>`,
    );
  }
  const origin = readable(d.origin?.text || "", "Повод запуска", "origin", d) || (d.origin?.source === "unknown" ? '<div class="muted">Повод запуска не записан.</div>' : "");
  const outcome = readable(d.outcome?.text || d.outcome?.note || "", "Итог", "outcome");
  let actions = steps.join("") || `<div class="muted">${live ? "Работа началась. Первые шаги ещё не записаны." : "Шаги не записаны."}</div>`;
  if (opts.limit && steps.length > opts.limit) {
    // Счёт один и тот же везде: шаги = итерации модели, не сумма рук и не строки ленты.
    const hidden = steps.length - opts.limit;
    actions = `<details class="action-earlier" data-detail="earlier"><summary>Показать ранние шаги · ${hidden} из ${steps.length}</summary>${steps.slice(0, -opts.limit).join("")}</details>` + steps.slice(-opts.limit).join("");
  }
  actions += tail.join("");
  if (opts.overview) {
    const count = steps.length ? ` · ${steps.length}${hands ? `, рук ${hands}` : ""}` : "";
    return origin + outcome + `<details class="run-actions" data-detail="actions" ${live ? "open" : ""}><summary>Шаги${count}</summary>${actions}</details>`;
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
    if (btn && !btn.dataset.full) btn.textContent = "Свернуть";
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
  return `<div class="frame-strip">
    <div class="frame-strip-head"><span>${esc(title)}</span>${link}</div>
    <div class="frame-bar"><i class="cached" style="width:${w(u.cached)}"></i><i class="fresh" style="width:${w(u.in)}"></i><i class="out" style="width:${w(u.out)}"></i></div>
    <div class="frame-legend">
      <span class="cached"><i></i>из кэша ${fmtK(u.cached)} (${share}%)</span>
      <span class="fresh"><i></i>заново ${fmtK(u.in)}</span>
      <span class="out"><i></i>ответ ${fmtK(u.out)}</span>
    </div>
  </div>`;
}
