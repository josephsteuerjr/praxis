// Линзы рук: что спросили и что вернулось — человеческими словами, поверх сырых вкладок.
//
// Строка шага раньше брала цель из первого подходящего ключа аргументов (command, path…):
// у python цели не было вовсе, task_control показывал «done», coding_session — «status».
// Здесь цель строится по смыслу каждой частой руки, а для code_query ещё и сводка ответа:
// операция, найденное, полнота обхода. Сырые аргументы и JSON-ответ остаются во вкладках.
//
// Импортирует только ./text: steps.ts подключает этот модуль, обратной связи нет.
import { esc } from "./text";

type Args = Record<string, unknown>;
const str = (a: Args, k: string) => typeof a[k] === "string" ? (a[k] as string).trim() : "";
const num = (a: Args, k: string) => typeof a[k] === "number" || (typeof a[k] === "string" && /^\d+$/.test(a[k] as string)) ? String(a[k]) : "";
const firstLine = (s: string) => s.split("\n").map(x => x.trim()).find(x => x && !x.startsWith("#")) || "";
const join = (...parts: string[]) => parts.filter(Boolean).join(" · ");

export const CQ_OPS: Record<string, string> = {
  symbols: "символы", definition: "где определено", references: "ссылки на",
  callers: "кто вызывает", callees: "что вызывает", impact: "что затронет правка", overview: "обзор",
};

/** code_query: операция словами, имя и модуль, сужение и корень. */
export function codeQueryTarget(a: Args): string {
  const op = str(a, "op"), name = str(a, "name"), module = str(a, "module"), path = str(a, "path"), root = str(a, "root");
  const what = name ? `${name}${module ? ` (${module})` : ""}` : "";
  const head = op === "symbols" ? `символы${path ? " в " + path : ""}`
    : op === "impact" ? `что затронет правка ${path || "дерева"}`
    : op === "overview" ? `обзор ${path || "проекта"}`
    : `${CQ_OPS[op] || op} ${what}${path ? " в " + path : ""}`;
  return join(head.trim(), root && root !== "home" ? root : "", str(a, "cursor") ? "следующая страница" : "");
}

/** Цель строки шага по смыслу руки; "" — пусть решает общий subject(). */
export function toolTarget(tool: string, args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const a = args as Args, action = str(a, "action");
  switch (tool) {
    case "code_query": return codeQueryTarget(a);
    case "python": return firstLine(str(a, "code"));
    case "fs_read": {
      if (Array.isArray(a.paths)) return `${a.paths.length} файлов: ${a.paths.slice(0, 3).map(String).join(", ")}${a.paths.length > 3 ? "…" : ""}`;
      const range = num(a, "start") || num(a, "end") ? `:${num(a, "start") || 1}–${num(a, "end") || "конец"}` : "";
      return str(a, "path") + range;
    }
    case "fs_search": return join(str(a, "pattern") ? `«${str(a, "pattern")}»` : "", str(a, "root"), str(a, "glob") !== "**/*" ? str(a, "glob") : "");
    case "edit": return Array.isArray(a.files) ? join(action, `${a.files.length} файлов`) : join(action, str(a, "path"));
    case "task_control": return join(action, str(a, "summary"));
    case "end_turn": return join(str(a, "outcome"), str(a, "note"));
    case "coding_session": return join(action, str(a, "task_id"), str(a, "title") || str(a, "target"));
    case "coding_agent": return join(action, str(a, "role"), str(a, "agent_id") || str(a, "phase"), str(a, "task_id"));
    case "coding_process": return join(action, str(a, "name") || str(a, "process_id"), str(a, "command"));
    case "coding_inspect": return join(action, str(a, "task_id"), str(a, "path") || str(a, "query"));
    case "memory_compact": return join(action, str(a, "place"));
    case "manage_notes": return join(action, str(a, "kind"), str(a, "text") || str(a, "note_id"));
    case "telegram_account": return join(action, str(a, "target"));
    case "computer": return join(action, str(a, "command") || str(a, "path") || str(a, "operation_id"));
    case "remind_self": return join(str(a, "when"), str(a, "goal"));
    case "send_file": return join(str(a, "kind"), str(a, "path"));
    case "remember": return join(str(a, "person"), str(a, "fact"));
    case "stay_silent": return str(a, "reason");
    default: return "";
  }
}

// ---------------------------------------------------------------- code_query

export interface CodeQueryRow {
  path?: string; line?: number | null; char?: number | null; kind?: string; evidence?: string; method?: string;
  ambiguous?: boolean; matches_target?: boolean | null; name?: string; qualname?: string; caller?: string;
  receiver?: string; signature?: string; snippet?: string; module?: string; files?: number; lines?: number;
}
export interface CodeQueryLens {
  kind: "code_query"; op?: string; error?: string; reason?: string; changed_paths?: string[];
  root?: { kind?: string; path?: string; head?: string }; snapshot?: string; total?: number | null;
  rows?: CodeQueryRow[]; next_cursor?: string | null; complete?: boolean;
  coverage?: {
    seen?: number | null; parsed?: number | null; prefiltered?: number | null; failed?: number;
    failed_sample?: Array<{ path?: string; reason?: string; line?: number | null }>; skipped?: Record<string, number>;
    dynamic_imports?: number; missing_grammars?: string[]; stopped_by?: string; spent_s?: number | null;
    time_budget_s?: number | null; file_cap?: number | null; note?: string;
  };
  methods?: Record<string, number>; cache?: { hits?: number | null; misses?: number | null };
  overview?: { files_scanned?: number | null; languages?: Record<string, number>; entrypoints?: string[];
    central_modules?: Array<{ module?: string; imported_by?: number | null }>; hotspots?: Array<{ path?: string; lines?: number | null }>;
    tests?: number | null; docs?: number };
  impact?: { changed?: string[]; impacted?: number; tests?: number; tests_sample?: string[]; parse_errors?: number; truncated?: boolean };
}
export type ToolLens = CodeQueryLens;

const CQ_ERRORS: Record<string, string> = {
  invalid_op: "неизвестная операция", limit_must_be_1_to_200: "limit вне 1–200", string_arguments_required: "аргументы должны быть строками",
  name_required: "для этой операции нужно name", name_must_be_dotted_identifier: "name — не имя Python",
  module_must_be_dotted_identifier: "module — не имя модуля", unsupported_remote_task: "удалённые задачи не поддерживаются",
  task_root_unavailable: "корень задачи недоступен", absolute_root_required: "нужен абсолютный корень",
  root_not_directory: "корень — не каталог", path_outside_root: "путь вне корня", path_unavailable: "путь не найден",
  root_unavailable: "корень недоступен", invalid_cursor: "курсор не разобран",
  cursor_expired_or_query_changed: "курсор истёк или запрос изменился", stale_cursor: "файлы изменились между страницами",
  snapshot_changed: "файлы изменились во время обхода", owned_search_failed: "процесс поиска не вернул ответ",
  query_failed: "разбор упал", invalid_query: "запрос не разобран",
};
const SKIP: Record<string, string> = {
  directory: "каталоги вне обхода", non_code_file: "не код", unsupported_suffix: "неподдерживаемые расширения",
  binary: "двоичные", grammar_unavailable_for_calls: "нет грамматики вызовов", legacy_text_reference_4MiB_cap: "больше 4 МиБ",
  legacy_impact_display_cap: "обрезано лимитом показа", io_error: "ошибка чтения", missing_or_unreadable: "не читаются",
  gitignore_inventory_failed: "не прочитался .gitignore",
};
const INCOMPLETE = new Set(["io_error", "missing_or_unreadable", "gitignore_inventory_failed", "grammar_unavailable_for_calls",
  "legacy_text_reference_4MiB_cap", "legacy_impact_display_cap", "binary"]);
const EVIDENCE: Record<string, string> = {
  definition: "определение", same_module_name: "имя из этого модуля", import_bound: "связано импортом",
  import_bound_alias: "импорт под другим именем", module_attribute: "через модуль", import_candidate: "импорт",
  name_match_other_scope: "совпало только имя", attribute_receiver_unknown: "получатель неизвестен",
  text_mention: "упоминание текстом", heuristic: "эвристика", inventory: "инвентарь",
};
const METHOD: Record<string, string> = { python_ast: "AST Python", regex: "регулярка", text: "текст", heuristic: "эвристика" };
const n = (v: number | null | undefined) => v == null ? "?" : v.toLocaleString("ru-RU");
const code = (s: string) => `<code>${esc(s)}</code>`;

/** Why the scan is not complete, from the tool's own coverage fields only. */
export function codeQueryGaps(l: CodeQueryLens): string[] {
  const c = l.coverage || {}, gaps: string[] = [];
  if (c.stopped_by) gaps.push(`бюджет обхода исчерпан (${c.stopped_by})`);
  if (c.failed) gaps.push(`не разобрано файлов: ${c.failed}`);
  for (const [reason, count] of Object.entries(c.skipped || {})) if (INCOMPLETE.has(reason)) gaps.push(`${SKIP[reason] || reason}: ${count}`);
  if (l.impact?.truncated) gaps.push("список затронутого обрезан");
  if (!gaps.length && !l.complete && !l.error) gaps.push("инструмент не назвал причину (например, файлы менялись во время обхода)");
  return gaps;
}

/** Short state for the collapsed row: the result in one phrase. */
export function lensState(lens: ToolLens | undefined): { state: string; attention: boolean; failed: boolean } | null {
  if (!lens || lens.kind !== "code_query") return null;
  if (lens.error) return { state: "отказ: " + (CQ_ERRORS[lens.error] || lens.error), attention: false, failed: true };
  const total = lens.total ?? lens.rows?.length ?? 0;
  const found = lens.op === "overview" ? `${n(lens.overview?.files_scanned ?? lens.coverage?.seen)} файлов`
    : lens.op === "impact" ? `затронуто ${n(lens.impact?.impacted ?? total)}`
    : total ? `найдено ${n(total)}` : "не найдено";
  return { state: found + (lens.complete ? "" : " · неполно"), attention: !lens.complete, failed: false };
}

function rowHTML(r: CodeQueryRow, op: string): string {
  const where = `${r.path || "?"}${r.line ? ":" + r.line : ""}`;
  // impact/overview rows are all heuristic by definition: the note says it once.
  const inventory = op === "impact" || op === "overview";
  const tags: Array<[string, string]> = ([[EVIDENCE[r.evidence || ""] || r.evidence || "", ""],
    [r.ambiguous ? "неоднозначно" : "", " cq-tag-warn"], [r.matches_target === false ? "другая цель" : "", ""]] as Array<[string, string]>).filter(([t]) => t && !inventory);
  let what = "";
  if (op === "overview") what = `файлов ${n(r.files)} · строк ${n(r.lines)}`;
  else if (op === "callers" && r.caller) what = `в ${code(r.caller)}`;
  else if (op === "callees") what = `${code(r.qualname || r.name || "")}${r.module ? ` из ${code(r.module)}` : ""}`;
  else if (r.kind && ["function", "async_function", "class", "assignment", "method"].includes(r.kind))
    what = `${code(r.qualname || r.name || "")} <span class="cq-kind">${esc(r.kind)}</span>${r.signature ? ` <code class="cq-sig">${esc(r.signature)}</code>` : ""}`;
  else if (r.qualname && r.qualname !== r.name) what = code(r.qualname);
  return `<li class="cq-row${r.ambiguous ? " is-ambiguous" : ""}${r.matches_target === false ? " is-other" : ""}">` +
    `<div class="cq-loc"><code class="cq-where">${esc(where)}</code>${tags.map(([t, cls]) => `<span class="cq-tag${cls}">${esc(t)}</span>`).join("")}</div>` +
    (what ? `<div class="cq-what">${what}</div>` : "") +
    (r.snippet ? `<pre class="cq-snip">${esc(r.snippet)}</pre>` : "") + `</li>`;
}

/** Summary panel: request, result, completeness, coverage, then the evidence rows. */
export function codeQueryHTML(l: CodeQueryLens, args: unknown): string {
  const a = (args && typeof args === "object" && !Array.isArray(args) ? args : {}) as Args;
  const facts: Array<[string, string, string?]> = [];
  const target = codeQueryTarget(a);
  facts.push(["Запрос", esc(target || l.op || "?")]);
  const root = l.root || {};
  const rootText = root.kind === "home" ? "дом" : root.kind === "task" ? "задача" : root.kind === "external" ? "внешнее дерево" : root.kind || "";
  const where = join((rootText + (root.path ? ` ${root.path}` : "")).trim(), root.head ? `git ${root.head.slice(0, 8)}` : "", l.snapshot ? `снимок ${l.snapshot.slice(0, 8)}` : "");
  if (where) facts.push(["Где", esc(where)]);
  if (l.error) {
    facts.push(["Итог", esc("Отказ: " + (CQ_ERRORS[l.error] || l.error)) + ` ${code(l.error)}${l.reason ? " · " + esc(l.reason) : ""}`, "bad"]);
    if (l.changed_paths?.length) facts.push(["Изменились", l.changed_paths.map(code).join(" ")]);
  } else {
    const rows = l.rows?.length || 0, total = l.total ?? rows;
    facts.push(["Итог", esc(total === 0 ? "ничего не найдено" : rows < total ? `показано ${n(rows)} из ${n(total)}` : `все ${n(total)}`) +
      (l.next_cursor ? " · есть следующая страница" : "")]);
    const gaps = codeQueryGaps(l);
    facts.push(["Полнота", l.complete ? "полный обход" : esc("неполный: " + gaps.join("; ")), l.complete ? "ok" : "warn"]);
    if (!l.complete && total === 0) facts.push(["Важно", "пустой неполный обход не доказывает отсутствия", "warn"]);
  }
  const c = l.coverage || {};
  if (c.seen != null) {
    const time = c.spent_s != null ? `${c.spent_s.toFixed(1)} с${c.time_budget_s ? ` из ${c.time_budget_s.toFixed(0)}` : ""}` : "";
    facts.push(["Покрытие", esc(join(`файлов ${n(c.seen)}`, c.parsed != null ? `разобрано ${n(c.parsed)}` : "",
      c.prefiltered ? `отсеяно по имени ${n(c.prefiltered)}` : "", time))]);
  }
  const skipped = Object.entries(c.skipped || {}).filter(([r]) => r !== "name_byte_prefilter").map(([r, k]) => `${SKIP[r] || r} ${n(k)}`);
  const extra = [...skipped, c.dynamic_imports ? `динамические импорты ${c.dynamic_imports}` : "",
    c.missing_grammars?.length ? `нет грамматики: ${c.missing_grammars.join(", ")}` : ""].filter(Boolean);
  if (extra.length) facts.push(["Мимо разбора", esc(extra.join(" · "))]);
  if (c.failed_sample?.length) facts.push(["Не разобраны", c.failed_sample.map(f => `${code(f.path + (f.line ? ":" + f.line : ""))} ${esc(f.reason || "")}`).join("<br>")]);
  const methods = Object.entries(l.methods || {}).map(([m, k]) => `${METHOD[m] || m} ${k}`);
  if (methods.length) facts.push(["Методы", esc(methods.join(" · "))]);
  const o = l.overview;
  if (o) {
    const langs = Object.entries(o.languages || {}).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${k} ${v}`);
    if (langs.length) facts.push(["Языки", esc(langs.join(" · "))]);
    if (o.central_modules?.length) facts.push(["Центральные", o.central_modules.map(m => `${code(m.module || "")} ← ${n(m.imported_by)}`).join("<br>")]);
    if (o.entrypoints?.length) facts.push(["Точки входа", o.entrypoints.map(code).join("<br>")]);
    if (o.hotspots?.length) facts.push(["Самые большие", o.hotspots.map(h => `${code(h.path || "")} · ${n(h.lines)} стр.`).join("<br>")]);
    facts.push(["Тесты · документы", esc(`${n(o.tests)} · ${o.docs ?? 0}`)]);
  }
  const im = l.impact;
  if (im) {
    facts.push(["Затронуто", esc(`${n(im.impacted)} модулей · тестов ${n(im.tests)}${im.parse_errors ? ` · ошибок разбора ${im.parse_errors}` : ""}`)]);
    if (im.tests_sample?.length) facts.push(["Тесты", im.tests_sample.map(code).join("<br>") + (im.tests && im.tests > im.tests_sample.length ? "<br>…" : "")]);
  }
  const dl = `<dl class="cq-facts">${facts.map(([k, v, cls]) => `<dt>${esc(k)}</dt><dd${cls ? ` class="cq-${cls}"` : ""}>${v}</dd>`).join("")}</dl>`;
  const rows = l.rows?.length ? `<ol class="cq-rows">${l.rows.map(r => rowHTML(r, l.op || "")).join("")}</ol>` : "";
  const note = l.error ? "" : `<p class="cq-note">${l.op === "impact" || l.op === "overview"
    ? "Эвристика по импортам и файлам; полная проверка — verification_plan(full=True)."
    : "Свидетельства из разбора кода, не доказанные связи: неоднозначные строки стоят в конце."}</p>`;
  return `<section class="cq">${dl}${rows}${note}</section>`;
}

export function lensHTML(lens: ToolLens | undefined, args: unknown): string {
  return lens?.kind === "code_query" ? codeQueryHTML(lens, args) : "";
}
