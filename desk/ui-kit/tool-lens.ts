import { t as uiText, locale } from "../lang/lang";
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
  get symbols() { return uiText("ui.ui.kit.tool.lens.a83c72b56b"); }, get definition() { return uiText("ui.ui.kit.tool.lens.48e78d3094"); }, get references() { return uiText("ui.ui.kit.tool.lens.8a0bb8b1c6"); },
  get callers() { return uiText("ui.ui.kit.tool.lens.38a914df01"); }, get callees() { return uiText("ui.ui.kit.tool.lens.c89342c644"); }, get impact() { return uiText("ui.ui.kit.tool.lens.125887e9c2"); }, get overview() { return uiText("ui.ui.kit.tool.lens.a5c3b417bb"); },
};

/** code_query: операция словами, имя и модуль, сужение и корень. */
export function codeQueryTarget(a: Args): string {
  const op = str(a, "op"), name = str(a, "name"), module = str(a, "module"), path = str(a, "path"), root = str(a, "root");
  const what = name ? `${name}${module ? ` (${module})` : ""}` : "";
  const head = op === "symbols" ? uiText("ui.ui.kit.tool.lens.9bb601d462", {p0: String(path ? uiText("ui.ui.kit.tool.lens.71bff885ae") + path : "")})
    : op === "impact" ? uiText("ui.ui.kit.tool.lens.9ca8dcad41", {p0: String(path || uiText("ui.ui.kit.tool.lens.df36f8b0cf"))})
    : op === "overview" ? uiText("ui.ui.kit.tool.lens.e9e4a79fd3", {p0: String(path || uiText("ui.ui.kit.tool.lens.4933a8f07c"))})
    : `${CQ_OPS[op] || op} ${what}${path ? uiText("ui.ui.kit.tool.lens.71bff885ae") + path : ""}`;
  return join(head.trim(), root && root !== "home" ? root : "", str(a, "cursor") ? uiText("ui.ui.kit.tool.lens.fb12d6c5d1") : "");
}

/** Цель строки шага по смыслу руки; "" — пусть решает общий subject(). */
export function toolTarget(tool: string, args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const a = args as Args, action = str(a, "action");
  if (tool.startsWith('windows_mcp_')) {
    const location = Array.isArray(a.loc) ? a.loc.join(', ') : num(a, 'label') ? uiText("ui.ui.kit.tool.lens.f5b5f509e0", {p0: String(num(a, 'label'))}) : '';
    return join(str(a, 'mode'), str(a, 'path') || str(a, 'name') || firstLine(str(a, 'command')) || str(a, 'url') || str(a, 'shortcut'), location);
  }
  switch (tool) {
    case "code_query": return codeQueryTarget(a);
    case "python": return firstLine(str(a, "code"));
    case "fs_read": {
      if (Array.isArray(a.paths)) return uiText("ui.ui.kit.tool.lens.5f6537545b", {p0: String(a.paths.length), p1: String(a.paths.slice(0, 3).map(String).join(", ")), p2: String(a.paths.length > 3 ? "…" : "")});
      const range = num(a, "start") || num(a, "end") ? `:${num(a, "start") || 1}–${num(a, "end") || uiText("ui.ui.kit.tool.lens.5f19631852")}` : "";
      return str(a, "path") + range;
    }
    case "fs_search": return join(str(a, "pattern") ? `«${str(a, "pattern")}»` : "", str(a, "root"), str(a, "glob") !== "**/*" ? str(a, "glob") : "");
    case "edit": return Array.isArray(a.files) ? join(action, uiText("ui.ui.kit.tool.lens.d6d7c420ce", {p0: String(a.files.length)})) : join(action, str(a, "path"));
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
  get invalid_op() { return uiText("ui.ui.kit.tool.lens.2074247a5e"); }, get limit_must_be_1_to_200() { return uiText("ui.ui.kit.tool.lens.e7bd61f3ae"); }, get string_arguments_required() { return uiText("ui.ui.kit.tool.lens.fea7e3368a"); },
  get name_required() { return uiText("ui.ui.kit.tool.lens.07e15a012c"); }, get name_must_be_dotted_identifier() { return uiText("ui.ui.kit.tool.lens.aca5cd93b2"); },
  get module_must_be_dotted_identifier() { return uiText("ui.ui.kit.tool.lens.78bfb2bf33"); }, get unsupported_remote_task() { return uiText("ui.ui.kit.tool.lens.d87aaabab8"); },
  get task_root_unavailable() { return uiText("ui.ui.kit.tool.lens.aedfe2be68"); }, get absolute_root_required() { return uiText("ui.ui.kit.tool.lens.681de1b70a"); },
  get root_not_directory() { return uiText("ui.ui.kit.tool.lens.d78550e553"); }, get path_outside_root() { return uiText("ui.ui.kit.tool.lens.eb8f04b34e"); }, get path_unavailable() { return uiText("ui.ui.kit.tool.lens.4356da11fc"); },
  get root_unavailable() { return uiText("ui.ui.kit.tool.lens.a2446b1515"); }, get invalid_cursor() { return uiText("ui.ui.kit.tool.lens.f8e63307b7"); },
  get cursor_expired_or_query_changed() { return uiText("ui.ui.kit.tool.lens.77f84d7922"); }, get stale_cursor() { return uiText("ui.ui.kit.tool.lens.595227350a"); },
  get snapshot_changed() { return uiText("ui.ui.kit.tool.lens.661ef6d8c9"); }, get owned_search_failed() { return uiText("ui.ui.kit.tool.lens.db52e346ff"); },
  get query_failed() { return uiText("ui.ui.kit.tool.lens.0d72835126"); }, get invalid_query() { return uiText("ui.ui.kit.tool.lens.2687518ac5"); },
};
const SKIP: Record<string, string> = {
  get directory() { return uiText("ui.ui.kit.tool.lens.0199d7eea7"); }, get non_code_file() { return uiText("ui.ui.kit.tool.lens.23551dddf4"); }, get unsupported_suffix() { return uiText("ui.ui.kit.tool.lens.3888e02f95"); },
  get binary() { return uiText("ui.ui.kit.tool.lens.ccdf476658"); }, get grammar_unavailable_for_calls() { return uiText("ui.ui.kit.tool.lens.d794eab6b3"); }, get legacy_text_reference_4MiB_cap() { return uiText("ui.ui.kit.tool.lens.d17ef93045"); },
  get legacy_impact_display_cap() { return uiText("ui.ui.kit.tool.lens.ab440a8f3f"); }, get io_error() { return uiText("ui.ui.kit.tool.lens.1f73ec1fdd"); }, get missing_or_unreadable() { return uiText("ui.ui.kit.tool.lens.843fa8a9b5"); },
  get gitignore_inventory_failed() { return uiText("ui.ui.kit.tool.lens.3d98c88bef"); },
};
const INCOMPLETE = new Set(["io_error", "missing_or_unreadable", "gitignore_inventory_failed", "grammar_unavailable_for_calls",
  "legacy_text_reference_4MiB_cap", "legacy_impact_display_cap", "binary"]);
const EVIDENCE: Record<string, string> = {
  get definition() { return uiText("ui.ui.kit.tool.lens.3bb8d04a61"); }, get same_module_name() { return uiText("ui.ui.kit.tool.lens.901d03daa7"); }, get import_bound() { return uiText("ui.ui.kit.tool.lens.29c8c4244f"); },
  get import_bound_alias() { return uiText("ui.ui.kit.tool.lens.a9db88fd2c"); }, get module_attribute() { return uiText("ui.ui.kit.tool.lens.8efb4c269c"); }, get import_candidate() { return uiText("ui.ui.kit.tool.lens.98e8208d97"); },
  get name_match_other_scope() { return uiText("ui.ui.kit.tool.lens.9391771840"); }, get attribute_receiver_unknown() { return uiText("ui.ui.kit.tool.lens.98cf98bbe3"); },
  get text_mention() { return uiText("ui.ui.kit.tool.lens.fd4561ccf0"); }, get heuristic() { return uiText("ui.ui.kit.tool.lens.1ff11116a0"); }, get inventory() { return uiText("ui.ui.kit.tool.lens.1b38a27ec7"); },
};
const METHOD: Record<string, string> = { get python_ast() { return uiText("ui.lens.python_ast"); }, get regex() { return uiText("ui.ui.kit.tool.lens.0143ae74a6"); }, get text() { return uiText("ui.ui.kit.tool.lens.ab15d5ab16"); }, get heuristic() { return uiText("ui.ui.kit.tool.lens.1ff11116a0"); } };
const n = (v: number | null | undefined) => v == null ? "?" : v.toLocaleString(locale());
const code = (s: string) => `<code>${esc(s)}</code>`;

/** Why the scan is not complete, from the tool's own coverage fields only. */
export function codeQueryGaps(l: CodeQueryLens): string[] {
  const c = l.coverage || {}, gaps: string[] = [];
  if (c.stopped_by) gaps.push(uiText("ui.ui.kit.tool.lens.ec73ee124d", {p0: String(c.stopped_by)}));
  if (c.failed) gaps.push(uiText("ui.ui.kit.tool.lens.43114b55b1", {p0: String(c.failed)}));
  for (const [reason, count] of Object.entries(c.skipped || {})) if (INCOMPLETE.has(reason)) gaps.push(`${SKIP[reason] || reason}: ${count}`);
  if (l.impact?.truncated) gaps.push(uiText("ui.ui.kit.tool.lens.feacf63d2f"));
  if (!gaps.length && !l.complete && !l.error) gaps.push(uiText("ui.ui.kit.tool.lens.16b7669ad3"));
  return gaps;
}

/** Short state for the collapsed row: the result in one phrase. */
export function lensState(lens: ToolLens | undefined): { state: string; attention: boolean; failed: boolean } | null {
  if (!lens || lens.kind !== "code_query") return null;
  if (lens.error) return { state: uiText("ui.ui.kit.tool.lens.6d31113206") + (CQ_ERRORS[lens.error] || lens.error), attention: false, failed: true };
  const total = lens.total ?? lens.rows?.length ?? 0;
  const found = lens.op === "overview" ? uiText("ui.ui.kit.tool.lens.d6d7c420ce", {p0: String(n(lens.overview?.files_scanned ?? lens.coverage?.seen))})
    : lens.op === "impact" ? uiText("ui.ui.kit.tool.lens.255420b24e", {p0: String(n(lens.impact?.impacted ?? total))})
    : total ? uiText("ui.ui.kit.tool.lens.c54d6a5dee", {p0: String(n(total))}) : uiText("ui.ui.kit.tool.lens.46d9c05f72");
  return { state: found + (lens.complete ? "" : uiText("ui.ui.kit.tool.lens.50ac1c3dae")), attention: !lens.complete, failed: false };
}

function rowHTML(r: CodeQueryRow, op: string): string {
  const where = `${r.path || "?"}${r.line ? ":" + r.line : ""}`;
  // impact/overview rows are all heuristic by definition: the note says it once.
  const inventory = op === "impact" || op === "overview";
  const tags: Array<[string, string]> = ([[EVIDENCE[r.evidence || ""] || r.evidence || "", ""],
    [r.ambiguous ? uiText("ui.ui.kit.tool.lens.49e7230fbc") : "", " cq-tag-warn"], [r.matches_target === false ? uiText("ui.ui.kit.tool.lens.aee97df0dc") : "", ""]] as Array<[string, string]>).filter(([t]) => t && !inventory);
  let what = "";
  if (op === "overview") what = uiText("ui.ui.kit.tool.lens.a71b1b42e2", {p0: String(n(r.files)), p1: String(n(r.lines))});
  else if (op === "callers" && r.caller) what = uiText("ui.ui.kit.tool.lens.43e80bf583", {p0: String(code(r.caller))});
  else if (op === "callees") what = `${code(r.qualname || r.name || "")}${r.module ? uiText("ui.ui.kit.tool.lens.a0ac7ef435", {p0: String(code(r.module))}) : ""}`;
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
  facts.push([uiText("ui.ui.kit.tool.lens.e08bc1c8b1"), esc(target || l.op || "?")]);
  const root = l.root || {};
  const rootText = root.kind === "home" ? uiText("ui.ui.kit.tool.lens.f5b3d757a7") : root.kind === "task" ? uiText("ui.ui.kit.tool.lens.26de1b4a28") : root.kind === "external" ? uiText("ui.ui.kit.tool.lens.b8e8e77022") : root.kind || "";
  const where = join((rootText + (root.path ? ` ${root.path}` : "")).trim(), root.head ? `git ${root.head.slice(0, 8)}` : "", l.snapshot ? uiText("ui.ui.kit.tool.lens.1641154d4a", {p0: String(l.snapshot.slice(0, 8))}) : "");
  if (where) facts.push([uiText("ui.ui.kit.tool.lens.4a7ac81df6"), esc(where)]);
  if (l.error) {
    facts.push([uiText("ui.ui.kit.tool.lens.4706e4a4a3"), esc(uiText("ui.ui.kit.tool.lens.76965d3420") + (CQ_ERRORS[l.error] || l.error)) + ` ${code(l.error)}${l.reason ? " · " + esc(l.reason) : ""}`, "bad"]);
    if (l.changed_paths?.length) facts.push([uiText("ui.ui.kit.tool.lens.2f2d168be9"), l.changed_paths.map(code).join(" ")]);
  } else {
    const rows = l.rows?.length || 0, total = l.total ?? rows;
    facts.push([uiText("ui.ui.kit.tool.lens.4706e4a4a3"), esc(total === 0 ? uiText("ui.ui.kit.tool.lens.6393bed327") : rows < total ? uiText("ui.ui.kit.tool.lens.4217c8f7a5", {p0: String(n(rows)), p1: String(n(total))}) : uiText("ui.ui.kit.tool.lens.91f5387fc4", {p0: String(n(total))})) +
      (l.next_cursor ? uiText("ui.ui.kit.tool.lens.5b503847e4") : "")]);
    const gaps = codeQueryGaps(l);
    facts.push([uiText("ui.ui.kit.tool.lens.7a6481e3e1"), l.complete ? uiText("ui.ui.kit.tool.lens.db98fc4fce") : esc(uiText("ui.ui.kit.tool.lens.b6b5806602") + gaps.join("; ")), l.complete ? "ok" : "warn"]);
    if (!l.complete && total === 0) facts.push([uiText("ui.ui.kit.tool.lens.49cf521f01"), uiText("ui.ui.kit.tool.lens.0644e06a18"), "warn"]);
  }
  const c = l.coverage || {};
  if (c.seen != null) {
    const spent = c.spent_s?.toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1});
    const time = spent == null ? "" : c.time_budget_s
      ? uiText("ui.lens.scan_time_budget", {spent, budget: c.time_budget_s.toLocaleString(locale(), {maximumFractionDigits: 0})})
      : uiText("ui.lens.scan_time", {spent});
    facts.push([uiText("ui.ui.kit.tool.lens.1c8d132bd3"), esc(join(uiText("ui.ui.kit.tool.lens.643d284afe", {p0: String(n(c.seen))}), c.parsed != null ? uiText("ui.ui.kit.tool.lens.6b53cb66b4", {p0: String(n(c.parsed))}) : "",
      c.prefiltered ? uiText("ui.ui.kit.tool.lens.e693ade2b8", {p0: String(n(c.prefiltered))}) : "", time))]);
  }
  const skipped = Object.entries(c.skipped || {}).filter(([r]) => r !== "name_byte_prefilter").map(([r, k]) => `${SKIP[r] || r} ${n(k)}`);
  const extra = [...skipped, c.dynamic_imports ? uiText("ui.ui.kit.tool.lens.6864259adf", {p0: String(c.dynamic_imports)}) : "",
    c.missing_grammars?.length ? uiText("ui.ui.kit.tool.lens.e802feef8b", {p0: String(c.missing_grammars.join(", "))}) : ""].filter(Boolean);
  if (extra.length) facts.push([uiText("ui.ui.kit.tool.lens.e4114aaa59"), esc(extra.join(" · "))]);
  if (c.failed_sample?.length) facts.push([uiText("ui.ui.kit.tool.lens.027d65c94d"), c.failed_sample.map(f => `${code(f.path + (f.line ? ":" + f.line : ""))} ${esc(f.reason || "")}`).join("<br>")]);
  const methods = Object.entries(l.methods || {}).map(([m, k]) => `${METHOD[m] || m} ${k}`);
  if (methods.length) facts.push([uiText("ui.ui.kit.tool.lens.ceab671084"), esc(methods.join(" · "))]);
  const o = l.overview;
  if (o) {
    const langs = Object.entries(o.languages || {}).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${k} ${v}`);
    if (langs.length) facts.push([uiText("ui.ui.kit.tool.lens.19e4bb1a50"), esc(langs.join(" · "))]);
    if (o.central_modules?.length) facts.push([uiText("ui.ui.kit.tool.lens.4b07a45c9c"), o.central_modules.map(m => `${code(m.module || "")} ← ${n(m.imported_by)}`).join("<br>")]);
    if (o.entrypoints?.length) facts.push([uiText("ui.ui.kit.tool.lens.8d6e25aa7b"), o.entrypoints.map(code).join("<br>")]);
    if (o.hotspots?.length) facts.push([uiText("ui.ui.kit.tool.lens.57651628fd"), o.hotspots.map(h => uiText("ui.ui.kit.tool.lens.8a5efbc2c4", {p0: String(code(h.path || "")), p1: String(n(h.lines))})).join("<br>")]);
    facts.push([uiText("ui.ui.kit.tool.lens.7969fec7c2"), esc(`${n(o.tests)} · ${o.docs ?? 0}`)]);
  }
  const im = l.impact;
  if (im) {
    facts.push([uiText("ui.ui.kit.tool.lens.d9f6717e14"), esc(uiText("ui.ui.kit.tool.lens.d58cd91410", {p0: String(n(im.impacted)), p1: String(n(im.tests)), p2: String(im.parse_errors ? uiText("ui.ui.kit.tool.lens.ff3fc0e021", {p0: String(im.parse_errors)}) : "")}))]);
    if (im.tests_sample?.length) facts.push([uiText("ui.ui.kit.tool.lens.d15959f229"), im.tests_sample.map(code).join("<br>") + (im.tests && im.tests > im.tests_sample.length ? "<br>…" : "")]);
  }
  const dl = `<dl class="cq-facts">${facts.map(([k, v, cls]) => `<dt>${esc(k)}</dt><dd${cls ? ` class="cq-${cls}"` : ""}>${v}</dd>`).join("")}</dl>`;
  const rows = l.rows?.length ? `<ol class="cq-rows">${l.rows.map(r => rowHTML(r, l.op || "")).join("")}</ol>` : "";
  const note = l.error ? "" : `<p class="cq-note">${l.op === "impact" || l.op === "overview"
    ? uiText("ui.ui.kit.tool.lens.ff28148b07")
    : uiText("ui.ui.kit.tool.lens.e9dbd15852")}</p>`;
  return `<section class="cq">${dl}${rows}${note}</section>`;
}

export function lensHTML(lens: ToolLens | undefined, args: unknown): string {
  return lens?.kind === "code_query" ? codeQueryHTML(lens, args) : "";
}
