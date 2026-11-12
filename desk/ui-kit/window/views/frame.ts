// Shadow reconstruction and the actual offered tool schemas saved with it.
import { api } from "../api";
import { esc, fmtN, md, q, safeRender } from "../lib";
import { S, isWindowRoom } from "../state";

interface Stream { stream: string; captures: number; latest?: string }
interface Capture { text?: string; tools?: Record<string, unknown>[] | null; tools_status?: string; run_id?: string }
const versions = new WeakMap<HTMLElement, number>();

export function captureTime(name: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(name);
  if (!m) return name.replace(/\.md$/, "");
  const date = new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`);
  return Number.isNaN(date.getTime()) ? name : date.toLocaleString("ru-RU", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
}

function streamTitle(stream: string): string {
  const peer = stream.replace(/^(chat|dm)-/, "");
  if (isWindowRoom(peer)) return `Основной чат · ${S.agent}`;
  return S.rooms.find(r => r.key === stream || r.key === peer)?.name
    || (stream.startsWith("no-chat") ? `Самостоятельная работа · ${stream.slice(8)}` : stream);
}

export async function render(container: HTMLElement): Promise<void> {
  const version = (versions.get(container) || 0) + 1;
  versions.set(container, version);
  const streams = await api<Stream[]>("/api/shadow");
  if (versions.get(container) !== version) return;
  if (!streams.length) {
    container.innerHTML = '<div class="empty"><b>Захватов кадра нет</b>Они появятся после первых ходов.</div>';
    return;
  }
  streams.sort((a, b) => (b.latest || "").localeCompare(a.latest || ""));
  if (!streams.some(s => s.stream === S.stream)) S.stream = streams[0].stream;
  container.innerHTML = `<div class="split wide">
    <div class="list"><label class="field-hint" for="frame-search">Чат или работа</label>
      <input id="frame-search" class="field-input" type="search" placeholder="Найти чат" aria-label="Найти чат">
      ${streams.map(s => `<div class="item" role="button" tabindex="0" aria-current="${s.stream === S.stream}" data-stream="${esc(s.stream)}">
        <span title="${esc(s.stream)}">${esc(streamTitle(s.stream))}<small class="muted" style="display:block">${esc(captureTime(s.latest || ""))}</small></span><span class="n">${s.captures}</span></div>`).join("")}
      <p class="field-hint">Снимки по времени. Теневая сборка контекста может отличаться от входа модели; схемы tools сохранены отдельно.</p>
    </div><div class="reading" id="frame-main"></div></div>`;
  const open = (stream: string) => {
    S.stream = stream;
    for (const el of container.querySelectorAll<HTMLElement>("[data-stream]")) el.setAttribute("aria-current", String(el.dataset.stream === stream));
    const main = q<HTMLElement>("#frame-main", container);
    safeRender(main, () => renderStream(main, stream));
  };
  q<HTMLInputElement>("#frame-search", container).addEventListener("input", e => {
    const value = (e.target as HTMLInputElement).value.toLocaleLowerCase();
    for (const el of container.querySelectorAll<HTMLElement>("[data-stream]")) el.hidden = !`${el.textContent} ${el.dataset.stream}`.toLocaleLowerCase().includes(value);
  });
  for (const el of container.querySelectorAll<HTMLElement>("[data-stream]")) {
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } });
    el.addEventListener("click", () => open(el.dataset.stream!));
  }
  await renderStream(q<HTMLElement>("#frame-main", container), S.stream);
}

async function renderStream(main: HTMLElement, stream: string): Promise<void> {
  const version = (versions.get(main) || 0) + 1;
  versions.set(main, version);
  const active = () => versions.get(main) === version && main.isConnected;
  main.innerHTML = '<div class="empty">читаю захваты…</div>';
  const base = `/api/shadow/${encodeURIComponent(stream)}`;
  let captures: string[];
  try { captures = await api<string[]>(`${base}/captures`); }
  catch (error) { if (active()) throw error; else return; }
  if (!active()) return;
  if (!captures.length) { main.innerHTML = '<div class="empty">захватов нет</div>'; return; }
  const option = (name: string) => `<option value="${esc(name)}">${esc(captureTime(name))} · ${esc(name.replace(/\.md$/, "").split("-").pop() || "")}</option>`;
  main.innerHTML = `<h2>${esc(streamTitle(stream))}</h2><div class="tools-row">
    <button class="btn btn-quiet" id="cap-prev" type="button" aria-label="Предыдущий снимок">← Раньше</button>
    <label class="muted" for="cap-new">Снимок</label><select id="cap-new" class="field-input">${captures.map(option).join("")}</select>
    <button class="btn btn-quiet" id="cap-next" type="button" aria-label="Следующий снимок">Позже →</button>
    <button class="btn btn-quiet" id="cap-more" type="button">Ещё снимки</button>
    <button class="btn btn-quiet" id="cap-refresh" type="button">Обновить</button></div>
    <details class="fold"><summary>Сравнение и метрики</summary><div class="fold-body tools-row">
    <label class="muted" for="cap-old">Сравнить с</label><select id="cap-old" class="field-input">${captures.map(option).join("")}</select>
    <button class="btn btn-quiet" id="do-diff" type="button">Сравнить</button><button class="btn btn-quiet" id="do-metrics" type="button">Метрики этого чата</button></div></details>
    <div id="frame-out"></div>`;
  const selected = q<HTMLSelectElement>("#cap-new", main), old = q<HTMLSelectElement>("#cap-old", main);
  old.selectedIndex = captures.length > 1 ? 1 : 0;
  let request = 0;
  const out = q<HTMLElement>("#frame-out", main);
  const update = () => {
    q<HTMLButtonElement>("#cap-prev", main).disabled = selected.selectedIndex >= captures.length - 1;
    q<HTMLButtonElement>("#cap-next", main).disabled = selected.selectedIndex <= 0;
  };
  const show = (mode: "view" | "diff" | "metrics" = "view") => {
    const ticket = ++request, name = selected.value, oldName = old.value;
    update();
    out.innerHTML = '<div class="empty">читаю…</div>';
    safeRender(out, async () => {
      const current = () => active() && ticket === request;
      try {
        const data = await api(mode === "view" ? `${base}/capture?name=${encodeURIComponent(name)}` : mode === "diff" ? `${base}/diff?old=${encodeURIComponent(oldName)}&new=${encodeURIComponent(name)}` : `${base}/metrics`);
        if (!current()) return;
        if (mode === "view") renderCapture(out, data, name);
        else if (mode === "diff") renderDiff(out, data);
        else renderMetrics(out, data);
      } catch (error) { if (current()) throw error; }
    });
  };
  selected.addEventListener("change", () => show());
  q("#cap-prev", main).addEventListener("click", () => { selected.selectedIndex++; show(); });
  q("#cap-next", main).addEventListener("click", () => { selected.selectedIndex--; show(); });
  q("#do-diff", main).addEventListener("click", () => show("diff"));
  q("#do-metrics", main).addEventListener("click", () => show("metrics"));
  q("#cap-refresh", main).addEventListener("click", () => { safeRender(main, () => renderStream(main, stream)); });
  const more = q<HTMLButtonElement>("#cap-more", main);
  more.hidden = captures.length < 30;
  more.addEventListener("click", () => safeRender(more, async () => {
    more.disabled = true;
    try {
      const page = await api<string[]>(`${base}/captures?before=${encodeURIComponent(captures[captures.length - 1])}`);
      if (!active()) return;
      const fresh = page.filter(name => !captures.includes(name));
      captures.push(...fresh);
      for (const name of fresh) { selected.insertAdjacentHTML("beforeend", option(name)); old.insertAdjacentHTML("beforeend", option(name)); }
      more.hidden = page.length < 30 || !fresh.length;
      update();
    } catch (error) { if (active()) throw error; }
    finally { if (active()) more.disabled = false; }
  }));
  show();
}

function renderCapture(out: HTMLElement, c: Capture, name: string): void {
  out.innerHTML = `<p class="muted">${esc(captureTime(name))} · ${fmtN((c.text || "").length)} знаков${c.run_id ? ` · <span class="mono">${esc(c.run_id)}</span>` : ""}</p>
    <div class="card"><h3>Схемы инструментов${Array.isArray(c.tools) ? ` · ${c.tools.length}` : ""}</h3>
    ${Array.isArray(c.tools) ? `<p class="field-hint">Набор tools, зафиксированный перед вызовом модели. Полные схемы, переданные отдельно от текста контекста.</p><input id="schema-search" class="field-input" type="search" placeholder="Найти инструмент" aria-label="Найти инструмент">
      ${c.tools.map(tool => `<details class="fold" data-tool="${esc(String(tool.name || tool.type || ""))}"><summary>${esc(String(tool.name || tool.type || "инструмент"))}</summary><pre class="fold-body">${esc(JSON.stringify(tool, null, 2))}</pre></details>`).join("")}`
      : `<p class="field-hint">${c.tools_status === "unavailable" ? "Не удалось проверить сохранённые схемы этого снимка." : "У этого старого снимка схемы не сохранены. Это не означает, что модель их не получила."}</p>`}</div>
    <p class="field-hint">Ниже — теневая сборка контекста; фактический вход модели может отличаться.</p><div class="card md">${md(c.text || "")}</div>`;
  out.querySelector<HTMLInputElement>("#schema-search")?.addEventListener("input", e => {
    const value = (e.target as HTMLInputElement).value.toLocaleLowerCase();
    for (const row of out.querySelectorAll<HTMLElement>("[data-tool]")) row.hidden = !row.dataset.tool!.toLocaleLowerCase().includes(value);
  });
}

function renderDiff(out: HTMLElement, d: any): void {
  if (!d.zones) { out.innerHTML = '<div class="empty">Сравнение недоступно: один из снимков отсутствует.</div>'; return; }
  const labels: Record<string, string> = { same: "байт в байт", changed: "разошлась", new: "новая" };
  out.innerHTML = `<p class="muted">Общий префикс: ${fmtN(d.prefix_bytes)} из ${fmtN(d.new_len)} знаков</p>
    <table class="grid"><tr><th>Зона</th><th>Состояние</th><th>Знаков</th></tr>${d.zones.map((z: any) => `<tr><td>${esc(z.zone)}</td><td>${esc(labels[z.state] || z.state)}</td><td>${fmtN(z.chars)}</td></tr>`).join("")}</table>
    <details class="fold"><summary>Подробный дифф</summary><pre class="fold-body diff">${esc((d.diff || []).join("\n"))}</pre></details>`;
}

function renderMetrics(out: HTMLElement, rows: Array<Record<string, unknown>>): void {
  if (!rows.length) { out.innerHTML = '<div class="empty">Метрик этого чата нет.</div>'; return; }
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))].slice(0, 10);
  out.innerHTML = `<table class="grid"><tr>${keys.map(k => `<th>${esc(k)}</th>`).join("")}</tr>${rows.slice(-60).reverse().map(r => `<tr>${keys.map(k => `<td class="mono">${esc(JSON.stringify(r[k]) ?? "")}</td>`).join("")}</tr>`).join("")}</table>`;
}
