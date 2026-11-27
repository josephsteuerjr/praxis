// «Приборы» → разбор хода: что записал харнесс об одном ходе — кадры, которые она
// читала, подготовку каждого вызова, телеметрию генерации, исходящие и смены статуса.
// Всё берётся из её журнала хода; окно ничего не домысливает — только раскладывает.
import { stepsHTML, type RunDetail } from "../../../ui-kit/steps";
import { copyText, toast } from "../../../ui-kit/dom";
import { md } from "../../../ui-kit/text";
import { api, bar, bytes, el, kv, ms, pct, relTime, why } from "../core";
import { KIND, type Run } from "../runs";

interface Budget { limit?: number; unit?: string; used_start?: number; used_final?: number; excludes?: string[] }
interface Frame { basis?: string; mode?: string; reused?: boolean; budget?: Budget;
  counts?: { tiers_offered?: number; tiers_included?: number; tiers_dropped?: number }; honesty?: { ok?: boolean };
  sections?: unknown[]; frame_id?: string }
interface Call {
  call_id: string; at?: string; state: string; role?: string; messages?: number; tools?: number;
  input?: { result_id?: string; size?: number; truncated?: boolean }; frame?: Frame;
  preparation?: { observed: boolean; total_ms?: number; stages_ms?: Record<string, number>; substages_ms?: Record<string, number> };
  generation?: { argument_bytes?: number; longest_whitespace_run?: number; attempt_ms?: number; outcome?: string };
  completed?: { at?: string; duration_ms?: number; stop_reason?: string; model?: string; role?: string; vision?: boolean; tool_calls?: number; text_chars?: number; usage?: Record<string, unknown> };
  failed?: { at?: string; duration_ms?: number; error?: string };
}
interface Timeline {
  id: string; created?: string; status?: string; kind?: string; goal?: string;
  calls: Call[]; tools: Record<string, number>;
  statuses: Array<{ at?: string; from?: string; to?: string; reason?: string; requested_by?: string; control_action?: string }>;
  outgoing: Array<{ at?: string; kind: string; reason?: string; receipt_key?: string; tool?: string; call_id?: string; result_id?: string; size?: number }>;
  artifacts: Array<{ at?: string; artifact_id?: string; name?: string; size?: number; media_type?: string }>;
  context_snapshot?: { path?: string; size?: number; sha256?: string }; recap?: { size?: number; sha256?: string };
  documents?: Record<string, boolean>;
  envelope?: { envelope?: Record<string, unknown>; note?: string } | null; turn?: Record<string, unknown> | null;
}
interface FrameRead {
  call_id: string; available: boolean; reason?: string; bytes?: number; verified?: boolean; sha256?: string; frame?: Frame;
  system?: unknown; messages?: Array<{ role?: string; content?: unknown }>; tools?: Array<{ name?: string; description?: string; input_schema?: unknown }>;
}
export interface RunPick { id: string; label: string; kind?: string; chat?: string; created?: string }

const STAGES: Record<string, string> = { old_context: "прежний контекст", message_tools: "сообщения и инструменты", shadow: "тень",
  call_setup: "установка вызова", measure: "замер", canary: "канарейка", model_input_artifact: "запись входа" };
const STOP: Record<string, string> = { tool_use: "вызвала руку", end_turn: "ответила", stop_sequence: "стоп-строка", max_tokens: "обрыв потолком",
  pause_turn: "пауза" };
const STATUS: Record<string, string> = { pending: "поставлен", running: "идёт", done: "завершён", failed: "не удался", cancelled: "прерван", waiting: "ждёт" };
const OUTCOME: Record<string, string> = { completed: "завершена", streaming: "идёт", interrupted: "прервана", upstream_failed: "сбой у провайдера", runaway_generation: "ушла вразнос" };
const OUTGOING: Record<string, string> = { delivery_skipped: "Не отправила", direct_outbox_intent: "Собиралась отправить",
  direct_outbox_projection: "Записала в исходящие", outbound_guard_input: "Страж исходящих проверил" };

const num = (value: unknown) => (value == null || value === "" || Number.isNaN(Number(value)) ? "—" : Number(value).toLocaleString("ru-RU"));
const cachedOf = (usage?: Record<string, unknown>) => Number(usage?.cache_read ?? usage?.cached ?? 0) || 0;
const sizeOf = (value: unknown) => (typeof value === "string" ? value.length : JSON.stringify(value)?.length ?? 0);

function fact(label: string, value: string, sub = ""): HTMLElement {
  const node = el("div", "instr-fact");
  node.append(el("span", "instr-fact-label", label), el("strong", "instr-fact-value", value));
  if (sub) node.append(el("span", "muted", sub));
  return node;
}

function fold(title: string, ...body: HTMLElement[]): HTMLElement {
  const details = el("details", "fold");
  details.append(el("summary", "", title));
  const inner = el("div", "fold-body");
  inner.append(...body);
  details.append(inner);
  return details;
}

/** Кнопка-блок: что это и сколько в нём знаков; клик показывает текст целиком. */
function block(label: string, size: number, value: unknown, viewer: Viewer, cls = ""): HTMLElement {
  const button = el("button", "instr-block " + cls);
  button.type = "button";
  button.innerHTML = `<span class="instr-block-label">${label.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string)}</span><span class="muted">${num(size)} знаков</span>`;
  button.addEventListener("click", () => viewer.show(label, value));
  return button;
}

/** Место, куда блоки отдают свой текст целиком — с копированием. */
function viewer(): Viewer {
  const tools = el("div", "instr-viewer-tools");
  const pre = el("pre", "code instr-viewer");
  pre.hidden = true;
  const node = el("div", "instr-viewer-box");
  node.append(tools, pre);
  return {
    node,
    show(label, value) {
      const text = typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "";
      pre.textContent = text;
      pre.hidden = false;
      tools.replaceChildren(el("span", "muted", `${label} · ${num(text.length)} знаков`));
      const copy = el("button", "chip", "Скопировать");
      copy.type = "button";
      copy.addEventListener("click", async () => { toast((await copyText(text)) ? "Скопировано." : "Не удалось скопировать."); });
      const hide = el("button", "chip", "Скрыть");
      hide.type = "button";
      hide.addEventListener("click", () => { pre.hidden = true; tools.replaceChildren(); });
      tools.append(copy, hide);
      pre.scrollIntoView({ block: "nearest" });
    },
  };
}

interface Viewer { node: HTMLElement; show(label: string, value: unknown): void }

/** Правая часть: разбор одного хода. */
export function mountRun(box: HTMLElement): { show(pick: RunPick): void } {
  let version = 0;
  let current: RunPick | null = null;
  let callId = "";
  let timeline: Timeline | null = null;
  let table: HTMLTableElement | null = null;

  const choose = (id: string) => {
    callId = id;
    for (const row of table?.querySelectorAll<HTMLElement>("tr[data-call]") ?? []) {
      row.classList.toggle("chosen", row.dataset.call === id);
      row.setAttribute("aria-selected", String(row.dataset.call === id));
    }
    void renderFrame();
  };

  async function renderFrame() {
    const frameBox = box.querySelector<HTMLElement>(".instr-frame");
    if (!frameBox || !current || !callId) return;
    const mine = version;
    frameBox.replaceChildren(el("h3", "", "Что прочитала модель"), el("p", "muted", "Читаю вход вызова…"));
    let data: FrameRead;
    try {
      data = await api<FrameRead>(`/api/window/run/${encodeURIComponent(current.id)}/frame?call=${encodeURIComponent(callId)}`);
    } catch (error) {
      if (mine === version) frameBox.append(el("p", "err", why(error)));
      return;
    }
    if (mine !== version) return;
    const call = timeline?.calls.find((item) => item.call_id === callId);
    frameBox.replaceChildren(el("h3", "", "Что прочитала модель"),
      el("p", "field-hint", `Вызов ${call?.completed?.model || call?.role || ""} · ${call?.at ? relTime(call.at) : ""} · записан харнессом до отправки`));
    if (!data.available) {
      const reasons: Record<string, string> = { missing: "Запись входа для этого вызова не сохранилась.", large: "Запись входа слишком велика, чтобы показать целиком.",
        unreadable: "Запись входа не прочиталась.", integrity: "Контрольная сумма записи не совпала: показывать её нельзя.", format: "Запись входа не в знакомом формате." };
      frameBox.append(el("div", "empty", reasons[data.reason || ""] || "Запись недоступна."));
      return;
    }
    const meta = data.frame || {};
    const facts = el("div", "instr-facts");
    const cache = fact("Вес входа", bytes(data.bytes), data.sha256 ? (data.verified ? "сверено по SHA-256" : "сумма SHA-256 не совпала") : "");
    facts.append(cache,
      fact("Сообщений", String(data.messages?.length ?? 0)),
      fact("Инструментов", String(data.tools?.length ?? 0)));
    if (meta.budget?.used_final != null)
      facts.append(fact("Бюджет кадра", `${num(meta.budget.used_start ?? 0)} → ${num(meta.budget.used_final)} ${meta.budget.unit === "chars" ? "знаков" : meta.budget.unit || ""}`,
        meta.budget.limit ? `предел ${num(meta.budget.limit)}` : "без предела"));
    if (meta.counts) facts.append(fact("Ярусы памяти", `${meta.counts.tiers_included ?? 0} из ${meta.counts.tiers_offered ?? 0} включены`,
      meta.counts.tiers_dropped ? `отброшено ${meta.counts.tiers_dropped}` : "ни один ярус не отброшен"));
    if (meta.honesty) facts.append(fact("Честность кадра", meta.honesty.ok ? "в порядке" : "с замечанием"));
    frameBox.append(facts);

    const watch = viewer();
    const part = (title: string, hint: string, ...children: HTMLElement[]) => {
      const node = el("section", "instr-part");
      node.append(el("h4", "", title), el("p", "muted", hint), ...children);
      return node;
    };
    // ---- система
    const systemBox = el("div", "instr-blocks");
    const system = data.system;
    if (typeof system === "string") systemBox.append(block("Системный контекст", system.length, system, watch));
    else if (Array.isArray(system)) system.forEach((item, index) => {
      const piece = (item ?? {}) as { type?: string; text?: unknown };
      systemBox.append(block(`Система · блок ${index + 1}${piece.type && piece.type !== "text" ? ` · ${piece.type}` : ""}`, sizeOf(piece.text ?? item), piece.text ?? item, watch));
    });
    // ---- сообщения
    const messages = data.messages ?? [];
    const roles: Record<string, number> = {};
    for (const message of messages) roles[message.role || "?"] = (roles[message.role || "?"] || 0) + 1;
    const name = (role: string) => (role === "user" ? "входящее" : role === "assistant" ? "её реплика" : role);
    const rowBlock = (index: number, message: { role?: string; content?: unknown }, piece: unknown, position: number) => {
      const item = (piece ?? {}) as { type?: string; name?: string; text?: unknown; content?: unknown; input?: unknown };
      const kind = item.type || (typeof piece === "string" ? "text" : "блок");
      const value = item.text ?? item.content ?? item.input ?? piece;
      const who = name(message.role || "");
      return block(`№${index + 1} · ${who}${kind !== "text" ? ` · ${kind}${item.name ? ` ${item.name}` : ""}` : ""}${position ? ` · часть ${position + 1}` : ""}`,
        sizeOf(value), value, watch, kind === "tool_result" ? "result" : kind === "tool_use" ? "call" : "");
    };
    const items: Array<{ index: number; message: { role?: string; content?: unknown }; piece: unknown; position: number }> = [];
    messages.forEach((message, index) => {
      const content = message.content;
      if (typeof content === "string") items.push({ index, message, piece: content, position: 0 });
      else if (Array.isArray(content)) content.forEach((piece, position) => items.push({ index, message, piece, position }));
    });
    // Последние сообщения решают, что она ответит: они видны сразу, остальные — в развороте.
    const tailCount = Math.min(6, items.length);
    const last = el("div", "instr-blocks");
    for (const item of items.slice(-tailCount)) last.append(rowBlock(item.index, item.message, item.piece, item.position));
    const every = el("div", "instr-blocks");
    for (const item of items) every.append(rowBlock(item.index, item.message, item.piece, item.position));
    const all = fold(`Все сообщения · ${messages.length}`, every);
    // ---- инструменты
    const toolsBox = el("div", "instr-blocks");
    for (const item of data.tools ?? []) {
      const tool = (item ?? {}) as { name?: string; description?: string; input_schema?: unknown };
      toolsBox.append(block(tool.name || "инструмент", sizeOf(tool.input_schema) + sizeOf(tool.description), tool, watch, "call"));
    }
    const columns = el("div", "instr-columns");
    columns.append(
      part("Система", "Кто она и как говорит с этой аудиторией — стоит перед всей перепиской.", systemBox),
      part("Сообщения", `${Object.entries(roles).map(([role, count]) => `${name(role)}: ${count}`).join(", ")}. Показаны последние ${tailCount}; остальные — в развороте.`, last, all),
      part("Инструменты", "Руки, которые она могла позвать в этом вызове.", toolsBox));
    frameBox.append(columns, watch.node);
  }

  async function render(pick: RunPick) {
    const mine = ++version;
    current = pick;
    box.replaceChildren(el("p", "muted", "Читаю журнал хода…"));
    let data: Timeline;
    try {
      data = await api<Timeline>(`/api/window/run/${encodeURIComponent(pick.id)}`);
    } catch (error) {
      if (mine === version) box.replaceChildren(el("div", "empty", why(error)));
      return;
    }
    if (mine !== version) return;
    timeline = data;
    callId = data.calls[0]?.call_id ?? "";

    const head = el("header", "instr-head");
    const chips = el("div", "chips");
    chips.append(el("span", "pill", KIND[data.kind || ""] || data.kind || "ход"),
      el("span", "pill " + (data.status === "done" ? "ok" : data.status === "failed" ? "err" : "live"), STATUS[data.status || ""] || data.status || "—"));
    if (pick.chat) chips.append(el("span", "pill", pick.chat));
    // Название хода — это её слова, часто длинные: заголовком идёт обычным шрифтом, не почерком.
    head.append(el("div", "top-kicker", `Разбор хода · ${relTime(pick.created)}`), el("h2", "instr-run-title", pick.label || data.goal || "Ход"), chips);

    // ---- сводка
    const calls = data.calls;
    const done = calls.filter((call) => call.completed);
    const sum = (pick2: (call: Call) => number) => done.reduce((total, call) => total + pick2(call), 0);
    const input = sum((call) => Number(call.completed?.usage?.in ?? 0));
    const cached = sum((call) => cachedOf(call.completed?.usage));
    const share = input + cached ? cached / (input + cached) : null;
    const cacheFact = fact("Вход из кэша", pct(share == null ? null : share * 100), `${num(cached)} из ${num(input + cached)} токенов`);
    cacheFact.append(bar(share, share != null && share < 0.5 ? "warn" : ""));
    const facts = el("div", "instr-facts");
    facts.append(fact("Вызовов модели", String(calls.length), `${calls.filter((call) => call.frame?.reused).length} с тем же кадром`), cacheFact,
      fact("Ответ", num(sum((call) => Number(call.completed?.usage?.out ?? 0))), "токенов за все вызовы"),
      fact("Подготовка", ms(calls.reduce((total, call) => total + Number(call.preparation?.total_ms ?? 0), 0)), "сборка кадров до вызовов"),
      fact("Сам ответ модели", ms(sum((call) => Number(call.completed?.duration_ms ?? 0))), "сумма вызовов"));
    const summary = el("section", "card instr-card");
    summary.append(el("h3", "", "Сводка"), facts);
    const flow = el("div", "instr-flow");
    for (const row of data.statuses)
      flow.append(el("span", "pill " + (row.to === "done" ? "ok" : row.to === "failed" ? "err" : "live"),
        `${row.at ? relTime(row.at) : ""} ${STATUS[row.from || ""] || row.from || "—"} → ${STATUS[row.to || ""] || row.to}${row.reason ? ` · ${row.reason}` : ""}`));
    for (const row of data.outgoing)
      flow.append(el("span", "pill", `${row.at ? relTime(row.at) : ""} ${OUTGOING[row.kind] || row.kind}${row.reason ? ` · ${row.reason}` : ""}${row.size ? ` · ${bytes(row.size)}` : ""}`));
    if (flow.childElementCount) summary.append(flow);
    const hands = Object.entries(data.tools).sort((a, b) => b[1] - a[1]);
    if (hands.length) summary.append(el("p", "muted", `Руки: ${hands.map(([tool, count]) => `${tool}${count > 1 ? ` ×${count}` : ""}`).join(", ")}`));

    // ---- вызовы модели
    table = el("table", "table calls");
    const headRow = el("tr");
    for (const name2 of ["#", "Время", "Кадр", "Подготовка до вызова", "Сам вызов", "Вход", "Из кэша", "Выход", "Чем кончился", "Генерация"]) headRow.append(el("th", "", name2));
    table.append(headRow);
    calls.forEach((call, index) => {
      const row = el("tr");
      row.dataset.call = call.call_id;
      row.tabIndex = 0;
      row.setAttribute("aria-selected", String(call.call_id === callId));
      if (call.call_id === callId) row.classList.add("chosen");
      row.addEventListener("click", () => choose(call.call_id));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(call.call_id); } });
      const frame = call.frame;
      const cellFrame = el("td");
      if (frame) {
        cellFrame.append(el("span", "pill" + (frame.reused ? " ok" : ""), frame.reused ? "тот же кадр" : frame.basis || "кадр"));
        if (frame.budget?.used_final != null) cellFrame.append(el("div", "small muted", `кадр ${num(frame.budget.used_start ?? 0)} → ${num(frame.budget.used_final)} ${frame.budget.unit === "chars" ? "знаков" : frame.budget.unit || ""}`));
        if (frame.counts) cellFrame.append(el("div", "small muted", `ярусы: ${frame.counts.tiers_included ?? 0} из ${frame.counts.tiers_offered ?? 0}${frame.counts.tiers_dropped ? `, отброшено ${frame.counts.tiers_dropped}` : ""}`));
        row.title = [frame.basis, frame.mode, frame.reused ? "вход переиспользован" : "", call.completed?.model].filter(Boolean).join(" · ");
      } else cellFrame.textContent = "—";
      const cellPrep = el("td");
      const total = call.preparation?.total_ms;
      if (total != null) {
        cellPrep.append(el("span", "num", ms(total)));
        const stack = el("div", "stack");
        for (const [stage, value] of Object.entries(call.preparation?.stages_ms ?? {}).sort((a, b) => b[1] - a[1])) {
          if (total > 0 && value / total < 0.04) continue;
          const piece = el("i");
          piece.style.width = ((value / total) * 100).toFixed(1) + "%";
          piece.title = `${STAGES[stage] || stage}: ${ms(value)}`;
          stack.append(piece);
        }
        cellPrep.append(stack);
        const heavy = Object.entries(call.preparation?.substages_ms ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 2);
        if (heavy.length) cellPrep.append(el("div", "small muted", heavy.map(([stage, value]) => `${stage.split(".").pop()}: ${ms(value)}`).join(" · ")));
      } else cellPrep.textContent = "—";
      const usage = call.completed?.usage ?? {};
      const cellEnd = el("td", call.completed?.stop_reason === "max_tokens" ? "err" : "");
      cellEnd.textContent = call.failed ? "ошибка" : STOP[call.completed?.stop_reason || ""] || call.completed?.stop_reason || (call.state === "started" ? "идёт" : "—");
      const cellGen = el("td", call.generation?.outcome && call.generation.outcome !== "completed" ? "err" : "");
      if (call.generation) cellGen.innerHTML = `<span>${OUTCOME[call.generation.outcome || ""] || call.generation.outcome || ""}</span><div class="small muted">${num(call.generation.argument_bytes)} Б аргументов · длиннейшая цепочка пробелов ${num(call.generation.longest_whitespace_run)}</div>`;
      else cellGen.textContent = "—";
      row.append(el("td", "num", String(index + 1)), el("td", "num", call.at ? relTime(call.at) : "—"), cellFrame, cellPrep,
        el("td", "num", call.completed?.duration_ms ?? call.failed?.duration_ms != null ? ms(call.completed?.duration_ms ?? call.failed?.duration_ms) : "—"),
        el("td", "num", usage.in != null ? num(usage.in) : "—"), el("td", "num", usage.in != null ? num(cachedOf(usage)) : "—"),
        el("td", "num", usage.out != null ? num(usage.out) : "—"), cellEnd, cellGen);
      table!.append(row);
    });
    const callsCard = el("section", "card instr-card");
    callsCard.append(el("h3", "", "Вызовы модели"),
      el("p", "field-hint", "Строка — один вызов; щёлкните по ней, чтобы увидеть, что прочитала модель. «Тот же кадр» — вход переиспользован, харнесс не собирал его заново. Столбик показывает, из чего сложилось ожидание до вызова."),
      el("div", "table-wrap"));
    callsCard.lastElementChild!.append(table);

    // ---- шаги и то, что осталось после хода
    const frameBox = el("section", "card instr-card instr-frame");
    const steps = el("section", "card instr-card");
    const stepsBody = el("div", "st-list instr-steps", "Читаю шаги…");
    steps.append(el("h3", "", "Шаги хода"), stepsBody);
    const docs = el("section", "card instr-card");
    docs.append(el("h3", "", "Что осталось после хода"));
    box.replaceChildren(head, summary, callsCard, frameBox, steps, docs);

    const put = (details: HTMLElement) => { if (details.childElementCount > 1) docs.append(details); };
    void api<RunDetail>(`/api/run/${encodeURIComponent(pick.id)}`).then((detail) => { if (mine === version) stepsBody.innerHTML = stepsHTML(detail); })
      .catch((error) => { if (mine === version) stepsBody.textContent = why(error); });
    for (const [name, title] of [["context", "Контекст хода"], ["recap", "Рекап"]] as const) {
      if (data.documents && !data.documents[name]) continue;   // ход ещё не оставил этот документ
      void api<{ text: string; truncated?: boolean }>(`/api/window/run/${encodeURIComponent(pick.id)}/doc/${name}`).then((doc) => {
        if (mine !== version) return;
        const paper = el("article", "card md");
        paper.innerHTML = md(doc.text);
        put(fold(`${title}${doc.truncated ? " · показано начало" : ""}`, paper));
      }).catch(() => undefined);
    }
    if (data.envelope?.envelope) {
      const body = el("div", "kv-list");
      for (const [key, value] of Object.entries(data.envelope.envelope)) if (value != null && value !== "" && value !== false) body.append(kv(key, typeof value === "object" ? JSON.stringify(value) : String(value)));
      put(fold("Конверт хода — с каким поводом и к какой аудитории он ушёл", body));
    }
    if (data.turn) {
      const body = el("div", "kv-list");
      for (const [key, value] of Object.entries(data.turn)) if (value != null && value !== "" && !(Array.isArray(value) && !value.length)) body.append(kv(key, typeof value === "object" ? JSON.stringify(value) : String(value)));
      put(fold("Её решение по ходу", body));
    }
    if (data.artifacts?.length) {
      const body = el("div", "kv-list");
      for (const item of data.artifacts) body.append(kv(item.name || item.artifact_id || "", `${bytes(item.size)}${item.media_type ? ` · ${item.media_type}` : ""}`));
      put(fold(`Артефакты хода · ${data.artifacts.length}`, body));
    }
    await renderFrame();
  }

  return { show: (pick: RunPick) => { void render(pick); } };
}

export type { Run };
