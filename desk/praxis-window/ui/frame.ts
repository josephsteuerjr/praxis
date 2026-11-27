import { runTitle } from "../../text";
import { cutsHTML, spendCutsHTML } from "./anatomy";

type Run = { id: string; created_at: string; display_title?: unknown; kind: string };
type Call = { call: string; at: string; model?: string; status: string; fallback?: boolean };
type Frame = { available: boolean; reason?: string; bytes: number; system: unknown; messages: unknown[]; tools: unknown[] };

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error("Сервер вернул " + response.status);
  return response.json();
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = "") {
  const node = document.createElement(tag);
  node.className = cls;
  node.textContent = text;
  return node;
}

function timestamp(at: string) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? at : date.toLocaleString();
}

function blocks(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function readable(value: unknown): HTMLElement {
  const body = element("div", "fold-body");
  body.style.cssText = "max-height:60vh;overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable";
  const list = Array.isArray(value) ? value : [value];
  for (const row of list) {
    if (row && typeof row === "object" && typeof row.role === "string" && "content" in row) {
      body.append(section(row.role, row.content));
    } else if (row && typeof row === "object" && typeof row.name === "string" && "input_schema" in row) {
      body.append(section(row.name, JSON.stringify(row, null, 2)));
    } else {
      const text = row && typeof row === "object" && row.type === "text" && typeof row.text === "string"
        ? row.text : blocks(row);
      const pre = element("pre", "", text);
      pre.style.cssText = "white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%;user-select:text";
      body.append(pre);
    }
  }
  return body;
}

function section(title: string, value: unknown) {
  const details = element("details", "fold");
  const summary = element("summary", "", title);
  details.append(summary);
  details.addEventListener("toggle", () => {
    if (details.open && details.childElementCount === 1) {
      details.append(readable(value));
    }
  });
  return details;
}

let previousRun = "", previousCall = "";

async function showCache(box: HTMLElement, back: () => void) {
  const returnButton = element("button", "btn", "Вернуться к вызову");
  returnButton.onclick = back;
  const frameReport = element("section", "", "Читаю кэш по действиям…");
  const usageReport = element("section", "", "Читаю общий расход…");
  box.replaceChildren(returnButton, frameReport, usageReport);
  box.closest("#view")?.scrollTo({ top: 0 });
  async function report<T>(part: HTMLElement, url: string, html: (data: T) => string) {
    try {
      const data = await read<T>(url);
      part.innerHTML = html(data);
      if (!part.childElementCount) part.textContent = "За эти семь дней записей нет.";
    } catch { part.textContent = "Не удалось прочитать отчёт."; }
  }
  await Promise.all([report(frameReport, "/api/frame-stats?days=7", cutsHTML),
    report(usageReport, "/api/spend?days=7", spendCutsHTML)]);
}

export async function render(container: HTMLElement) {
  const box = element("div", "center");
  const select = (title: string) => {
    const label = element("label", "field");
    const input = element("select", "field-input");
    label.append(element("span", "field-hint", title), input);
    box.append(label);
    return input;
  };
  const runs = select("Ход");
  const actions = element("div", "actions frame-actions");
  const cache = element("button", "btn", "Статистика кэша");
  cache.type = "button";
  cache.onclick = () => {
    void showCache(box, () => void render(container));
  };
  const refresh = element("button", "btn", "Обновить");
  const more = element("button", "btn", "Предыдущие ходы");
  more.type = "button";
  refresh.type = "button";
  actions.append(refresh, more, cache);
  box.append(actions);
  const calls = select("Вызов модели");
  const content = element("div");
  box.append(content);
  container.replaceChildren(box);
  let lastRun = "", request = 0;
  const active = (n: number) => n === request && box.isConnected;
  const fail = (n: number, error: unknown) => {
    if (active(n)) content.textContent = error instanceof Error ? error.message : "Ошибка чтения";
  };
  async function page() {
    more.disabled = true;
    try {
      const rows = await read<Run[]>("/api/runs?limit=50&before=" + encodeURIComponent(lastRun));
      for (const row of rows) {
        runs.add(new Option(timestamp(row.created_at) + " · " + (runTitle(row) || row.kind || row.id), row.id));
        lastRun = row.id;
      }
      more.hidden = rows.length < 50;
    } finally { more.disabled = false; }
  }
  function base() { return "/api/model-frames/" + encodeURIComponent(runs.value); }
  async function loadFrame() {
    const n = ++request;
    previousCall = calls.value;
    content.textContent = "Читаю запись вызова…";
    try {
      const frame = await read<Frame>(base() + "?call=" + encodeURIComponent(calls.value));
      if (!active(n)) return;
      if (!frame.available) {
        content.textContent = frame.reason === "integrity" ? "Контрольная сумма записи не совпала." : "Текст входа для этого вызова недоступен.";
        return;
      }
      content.replaceChildren(element("h3", "section-title", "Вход модели"),
        element("p", "muted", "Запись харнесса перед отправкой · " + frame.bytes.toLocaleString() + " байт"),
        section("Системный контекст", frame.system), section("Сообщения · " + frame.messages.length, frame.messages),
        section("Инструменты · " + frame.tools.length, frame.tools),
        section("Исходная структура входа", {
          system: frame.system, messages: frame.messages, tools: frame.tools
        }));
    } catch (e) { fail(n, e); }
  }
  async function loadRun() {
    const n = ++request;
    previousRun = runs.value;
    calls.replaceChildren();
    if (!runs.value) { content.textContent = "Записанных ходов пока нет."; return; }
    content.textContent = "Читаю журнал вызовов…";
    try {
      const data = await read<{ calls: Call[] }>(base());
      if (!active(n)) return;
      const states: Record<string, string> = { completed: "получен ответ", started: "начат", failed: "ошибка", recorded: "вход записан" };
      data.calls.forEach((row, i) => calls.add(new Option(
        (i + 1) + " · " + timestamp(row.at) + " · " + (row.model ? row.model + " · " : "") +
        (states[row.status] || row.status) + (row.fallback ? " · резервный вход" : ""), row.call)));
      if (!calls.length) { content.textContent = "В журнале этого хода нет записей о входе модели."; return; }
      if (data.calls.some(row => row.call === previousCall)) calls.value = previousCall;
      await loadFrame();
    } catch (e) { fail(n, e); }
  }
  runs.onchange = () => { previousCall = ""; void loadRun(); };
  calls.onchange = () => void loadFrame();
  more.onclick = () => void page().catch(e => fail(request, e));
  refresh.onclick = () => void render(container);
  content.textContent = "Читаю ходы…";
  try {
    await page();
    if (!box.isConnected) return;
    if (Array.from(runs.options).some(o => o.value === previousRun)) runs.value = previousRun;
    await loadRun();
  } catch (e) { fail(request, e); }
}
