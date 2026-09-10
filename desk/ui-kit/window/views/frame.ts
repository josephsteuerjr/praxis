// Кадр: живые слепки того, что видит модель, по потокам; чтение целиком,
// дифф соседних захватов (где порвался префикс кэша) и метрики.
import { api } from "../api";
import { esc, fmtN, md, q, safeRender } from "../lib";
import { S } from "../state";

interface Stream {
  stream: string;
  captures: number;
  latest?: string;
}

export async function render(container: HTMLElement): Promise<void> {
  const streams = await api<Stream[]>("/api/shadow");
  if (!streams.length) {
    container.innerHTML = '<div class="empty"><b>Захватов кадра нет</b>Они появятся после первых ходов.</div>';
    return;
  }
  if (!S.stream || !streams.some((s) => s.stream === S.stream)) {
    S.stream = streams.slice().sort((a, b) => (b.latest || "").localeCompare(a.latest || ""))[0].stream;
  }
  container.innerHTML = `<div class="split wide">
    <div class="list">${streams
      .map(
        (s) => `<div class="item" role="button" aria-current="${s.stream === S.stream}" data-stream="${esc(s.stream)}">
          <span>${esc(s.stream)}</span><span class="n">${s.captures}</span></div>`,
      )
      .join("")}
      <p class="field-hint" style="margin:14px 10px">Тень пишется по PRAXIS_FRAME_SHADOW: если потока нет, значит тени там нет, и это честно.</p>
    </div>
    <div class="reading" id="frame-main"><div class="empty">…</div></div>
  </div>`;
  for (const el of container.querySelectorAll<HTMLElement>(".item[data-stream]")) {
    el.addEventListener("click", () => {
      S.stream = el.dataset.stream!;
      safeRender(container, () => render(container));
    });
  }
  await renderStream(container);
}

async function renderStream(container: HTMLElement) {
  const main = q<HTMLElement>("#frame-main", container);
  main.innerHTML = '<div class="empty">читаю захваты…</div>';
  const captures = await api<string[]>(`/api/shadow/${encodeURIComponent(S.stream)}/captures`);
  S.captures = captures;
  if (!captures.length) {
    main.innerHTML = '<div class="empty">захватов нет</div>';
    return;
  }
  const opts = captures.map((c) => `<option value="${esc(c)}">${esc(c.replace(".md", ""))}</option>`).join("");
  main.innerHTML = `<div class="tools-row">
      <select id="cap-new" class="field-input">${opts}</select>
      <button class="btn btn-primary" id="do-view" type="button">Читать кадр</button>
      <select id="cap-old" class="field-input">${opts}</select>
      <button class="btn btn-quiet" id="do-diff" type="button">Дифф: где порвался префикс</button>
      <button class="btn btn-quiet" id="do-metrics" type="button">Метрики</button>
    </div>
    <div id="frame-out"></div>`;
  if (captures.length > 1) q<HTMLSelectElement>("#cap-old", main).selectedIndex = 1;
  // Раньше все четыре были голым `void`: при отказе кнопки навсегда оставляли
  // «читаю кадр…», «считаю дифф…», «читаю метрики…» — без ошибки и без выхода.
  const out = () => q<HTMLElement>("#frame-out", main);
  q("#do-diff", main).addEventListener("click", () => safeRender(out(), () => doDiff(main)));
  q("#do-view", main).addEventListener("click", () => safeRender(out(), () => doView(main)));
  q("#do-metrics", main).addEventListener("click", () => safeRender(out(), () => doMetrics(main)));
  q("#cap-new", main).addEventListener("change", () => safeRender(out(), () => doView(main)));
  await doView(main);
}

async function doView(main: HTMLElement) {
  const out = q<HTMLElement>("#frame-out", main);
  out.innerHTML = '<div class="empty">читаю кадр…</div>';
  const name = q<HTMLSelectElement>("#cap-new", main).value;
  const c = await api<{ text?: string }>(`/api/shadow/${encodeURIComponent(S.stream)}/capture?name=${encodeURIComponent(name)}`);
  const raw = (name || "").split("-")[0];
  const stamp = raw.length >= 13 ? `${raw.slice(6, 8)}.${raw.slice(4, 6)} ${raw.slice(9, 11)}:${raw.slice(11, 13)}` : name;
  out.innerHTML = `<p class="muted">Захват <b class="mono">${esc(stamp)}</b> · поток <span class="mono">${esc(S.stream)}</span> ·
      ${fmtN((c.text || "").length)} знаков. Это текст кадра целиком, как он уезжает модели.</p>
    <div class="card md">${md(c.text || "")}</div>`;
}

async function doDiff(main: HTMLElement) {
  const out = q<HTMLElement>("#frame-out", main);
  out.innerHTML = '<div class="empty">считаю дифф…</div>';
  const oldName = q<HTMLSelectElement>("#cap-old", main).value;
  const newName = q<HTMLSelectElement>("#cap-new", main).value;
  const d = await api(`/api/shadow/${encodeURIComponent(S.stream)}/diff?old=${encodeURIComponent(oldName)}&new=${encodeURIComponent(newName)}`);
  if (!d.zones) {
    out.innerHTML = '<div class="empty">дифф не собрался</div>';
    return;
  }
  const label: Record<string, string> = { same: "байт в байт", changed: "разошлась", new: "новая" };
  const zones = d.zones
    .map(
      (z: { zone: string; state: string; chars: number; was_chars?: number; prefix?: number }) => `<tr>
      <td>${esc(z.zone)}</td>
      <td><span class="zstate ${z.state}">${label[z.state] || z.state}</span></td>
      <td class="mono">${fmtN(z.chars)}${z.was_chars != null && z.was_chars !== z.chars ? " ← " + fmtN(z.was_chars) : ""}</td>
      <td class="mono">${z.prefix != null ? "цел до " + fmtN(z.prefix) : ""}</td>
    </tr>`,
    )
    .join("");
  const diffHtml = (d.diff || [])
    .map((l: string) => {
      const cls = l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : "";
      return `<span class="${cls}">${esc(l)}</span>`;
    })
    .join("\n");
  out.innerHTML = `<p class="muted">Общий префикс: <b class="mono">${fmtN(d.prefix_bytes)}</b> из ${fmtN(d.new_len)} знаков
      (${d.new_len ? Math.round((100 * d.prefix_bytes) / d.new_len) : 0}%)${d.gone?.length ? " · исчезли зоны: " + esc(d.gone.join(", ")) : ""}</p>
    <table class="grid"><tr><th>зона</th><th>состояние</th><th>знаков</th><th>префикс</th></tr>${zones}</table>
    <details class="fold" style="margin-top:14px"><summary>подробный дифф (${(d.diff || []).length} строк)</summary>
      <div class="fold-body"><div class="diff">${diffHtml}</div></div></details>`;
}

async function doMetrics(main: HTMLElement) {
  const out = q<HTMLElement>("#frame-out", main);
  out.innerHTML = '<div class="empty">читаю метрики…</div>';
  const rows = await api<Array<Record<string, unknown>>>("/api/shadow-metrics");
  if (!rows.length) {
    out.innerHTML = '<div class="empty">метрик нет</div>';
    return;
  }
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 10);
  out.innerHTML = `<table class="grid"><tr>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr>
    ${rows
      .slice(-60)
      .reverse()
      .map((r) => `<tr>${keys.map((k) => `<td class="mono">${esc(JSON.stringify(r[k]) ?? "")}</td>`).join("")}</tr>`)
      .join("")}</table>`;
}
