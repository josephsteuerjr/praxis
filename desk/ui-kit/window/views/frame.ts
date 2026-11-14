import { t as uiText, locale } from "../../../lang/lang";
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
  return Number.isNaN(date.getTime()) ? name : date.toLocaleString(locale(), { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
}

function streamTitle(stream: string): string {
  const peer = stream.replace(/^(chat|dm)-/, "");
  const name = S.rooms.find(r => r.key === stream || r.key === peer)?.name;
  if (name) return name;
  if (isWindowRoom(peer)) return uiText("ui.ui.kit.window.views.frame.002b74ab57", {p0: String(S.agent)});
  return stream.startsWith("no-chat") ? uiText("ui.ui.kit.window.views.frame.2558660870", {p0: String(stream.slice(8))}) : stream;
}

export async function render(container: HTMLElement): Promise<void> {
  const version = (versions.get(container) || 0) + 1;
  versions.set(container, version);
  let streams: Stream[];
  try { streams = await api<Stream[]>("/api/shadow"); }
  catch (error) { if (versions.get(container) === version) throw error; else return; }
  if (versions.get(container) !== version) return;
  if (!streams.length) {
    container.innerHTML = uiText("ui.ui.kit.window.views.frame.7ce7c766dd");
    return;
  }
  streams.sort((a, b) => (b.latest || "").localeCompare(a.latest || ""));
  if (!streams.some(s => s.stream === S.stream)) S.stream = streams[0].stream;
  container.innerHTML = uiText("ui.ui.kit.window.views.frame.4d4eb21f58", {p0: String(container.clientWidth >= 760 ? "open" : ""), p1: String(esc(streamTitle(S.stream))), p2: String(streams.map(s => `<div class="item" role="button" tabindex="0" aria-current="${s.stream === S.stream}" data-stream="${esc(s.stream)}">
        <span title="${esc(s.stream)}">${esc(streamTitle(s.stream))}<small class="muted" style="display:block">${esc(captureTime(s.latest || ""))}</small></span><span class="n">${s.captures}</span></div>`).join(""))});
  const open = (stream: string) => {
    S.stream = stream;
    q("#frame-stream-title", container).textContent = uiText("ui.ui.kit.window.views.frame.358f67b54f", {p0: String(streamTitle(stream))});
    if (container.clientWidth < 760) q<HTMLDetailsElement>("#frame-streams", container).open = false;
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
  main.innerHTML = uiText("ui.ui.kit.window.views.frame.f755177cc8");
  const base = `/api/shadow/${encodeURIComponent(stream)}`;
  let captures: string[];
  try { captures = await api<string[]>(`${base}/captures`); }
  catch (error) { if (active()) throw error; else return; }
  if (!active()) return;
  if (!captures.length) { main.innerHTML = uiText("ui.ui.kit.window.views.frame.908ecfd040"); return; }
  const option = (name: string) => `<option value="${esc(name)}">${esc(captureTime(name))} · ${esc(name.replace(/\.md$/, "").split("-").pop() || "")}</option>`;
  main.innerHTML = uiText("ui.ui.kit.window.views.frame.d203a44f62", {p0: String(esc(streamTitle(stream))), p1: String(captures.map(option).join("")), p2: String(captures.map(option).join(""))});
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
    out.innerHTML = uiText("ui.ui.kit.window.views.frame.fe732bbd85");
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
  const status = q<HTMLElement>("#cap-status", main);
  more.addEventListener("click", () => safeRender(status, async () => {
    status.textContent = "";
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
  if (!c.text) {
    out.innerHTML = uiText("ui.ui.kit.window.views.frame.4ad3150c1c");
    return;
  }
  out.innerHTML = uiText("ui.ui.kit.window.views.frame.deacc07f47", {p0: String(esc(captureTime(name))), p1: String(fmtN((c.text || "").length)), p2: String(c.run_id ? ` · <span class="mono">${esc(c.run_id)}</span>` : ""), p3: String(Array.isArray(c.tools) ? ` · ${c.tools.length}` : ""), p4: String(Array.isArray(c.tools) ? uiText("ui.ui.kit.window.views.frame.b6b8e1b4bf", {p0: String(c.tools.map(tool => `<details class="fold" data-tool="${esc(String(tool.name || tool.type || ""))}"><summary>${esc(String(tool.name || tool.type || uiText("ui.ui.kit.window.views.frame.ea299cc892")))}</summary><pre class="fold-body" style="overflow:auto">${esc(JSON.stringify(tool, null, 2))}</pre></details>`).join(""))})
      : `<p class="field-hint">${c.tools_status === "unavailable" ? uiText("ui.ui.kit.window.views.frame.5ccccd221e") : uiText("ui.ui.kit.window.views.frame.de706133ee")}</p>`), p5: String(md(c.text || ""))});
  out.querySelector<HTMLInputElement>("#schema-search")?.addEventListener("input", e => {
    const value = (e.target as HTMLInputElement).value.toLocaleLowerCase();
    for (const row of out.querySelectorAll<HTMLElement>("[data-tool]")) row.hidden = !row.dataset.tool!.toLocaleLowerCase().includes(value);
  });
}

function renderDiff(out: HTMLElement, d: any): void {
  if (!d.zones) { out.innerHTML = uiText("ui.ui.kit.window.views.frame.a50beca605"); return; }
  const labels: Record<string, string> = { same: uiText("ui.ui.kit.window.views.frame.3901a28f0c"), changed: uiText("ui.ui.kit.window.views.frame.b039e6fddc"), new: uiText("ui.ui.kit.window.views.frame.15cf2a3607") };
  out.innerHTML = uiText("ui.ui.kit.window.views.frame.1bbae085b1", {p0: String(fmtN(d.prefix_bytes)), p1: String(fmtN(d.new_len)), p2: String(d.zones.map((z: any) => `<tr><td>${esc(z.zone)}</td><td>${esc(labels[z.state] || z.state)}</td><td>${fmtN(z.chars)}</td></tr>`).join("")), p3: String(esc((d.diff || []).join("\n")))});
}

function renderMetrics(out: HTMLElement, rows: Array<Record<string, unknown>>): void {
  if (!rows.length) { out.innerHTML = uiText("ui.ui.kit.window.views.frame.890152e755"); return; }
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))].slice(0, 10);
  out.innerHTML = `<table class="grid"><tr>${keys.map(k => `<th>${esc(k)}</th>`).join("")}</tr>${rows.slice(-60).reverse().map(r => `<tr>${keys.map(k => `<td class="mono">${esc(JSON.stringify(r[k]) ?? "")}</td>`).join("")}</tr>`).join("")}</table>`;
}
