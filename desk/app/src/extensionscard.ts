import { t as uiText, locale } from "../../lang/lang";
// Карточка «Расширения» (25.09, поток K): свои тулы и крючки владельца из
// data/extensions/<имя>/ — что загружено, что нет и почему, и отчёт репетиции
// обновления. Данные — из трубы (`/api/mode`: `extensions_live` — снимок раннера,
// `extensions_check` — отчёт мастера до подмены папок). Окно ничего не решает
// само: «поручить агенту адаптировать» — записка агенту с фактами, не правка.
import { post, shell } from "../../ui-kit/window/api";
import { el, humanError, toast } from "../../ui-kit/window/lib";
import { button, card } from "../../ui-kit/dom";
import type { ExtensionRow, ExtensionsSnapshot, ModeState } from "../../ui-kit/window/mode";

const STATE_WORD: Record<string, string> = {
  get loaded() { return uiText("ui.app.src.extensionscard.500869bbb0"); },
  get disabled() { return uiText("ui.app.src.extensionscard.558b26bd49"); },
  get incompatible() { return uiText("ui.app.src.extensionscard.15e942c1db"); },
  get error() { return uiText("ui.app.src.extensionscard.406075b011"); },
  get pending() { return uiText("ui.app.src.extensionscard.8e81b9ac0c"); },
};

function when(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleString(locale(), { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

function rowLine(row: ExtensionRow): HTMLElement {
  const ok = row.state === "loaded";
  const line = el("div", "ext-row");
  line.append(el("span", ok ? "receipt ok" : "receipt err", `${row.name} ${row.version || ""}`.trim() + " — " + (STATE_WORD[row.state] || row.state)));
  const details: string[] = [];
  if (row.tools && row.tools.length) details.push(uiText("ui.app.src.extensionscard.ad9a4fa870") + row.tools.join(", "));
  if (row.hooks && Object.keys(row.hooks).length) details.push(uiText("ui.app.src.extensionscard.ee826f9bb9") + Object.keys(row.hooks).join(", "));
  if (row.api) details.push(row.api);
  if (details.length) line.append(el("div", "field-hint", details.join(" · ")));
  if (row.reason) line.append(el("div", ok ? "field-hint" : "receipt err", row.reason));
  return line;
}

/** Записка агенту: адаптировать расширение под новую версию или перенести патч. */
export function adaptNote(row: ExtensionRow, check: ExtensionsSnapshot | undefined, live: ExtensionsSnapshot | undefined): string {
  const api = check?.api || live?.api || "helene.ext/?";
  const helene = check?.helene || live?.helene || "";
  return [
    uiText("ui.app.src.extensionscard.26a05f86fd", {p0: String(row.name), p1: String(row.version || "")}).trim() +
      uiText("ui.app.src.extensionscard.739e94345b", {p0: String(helene ? ` (${helene})` : ""), p1: String(api)}),
    uiText("ui.app.src.extensionscard.22f653c626", {p0: String(row.dir || `data/extensions/${row.name}`)}),
    uiText("ui.app.src.extensionscard.114d0338f5", {p0: String(row.reason || uiText("ui.app.src.extensionscard.3a6034310d"))}),
    row.api ? uiText("ui.app.src.extensionscard.307c7a9224", {p0: String(row.api)}) : "",
    uiText("ui.app.src.extensionscard.79d768a50c"),
  ].filter(Boolean).join("\n");
}

export function extensionsCard(live: ModeState | null, treePath: string): { el: HTMLElement } {
  const snap = live?.extensions_live;
  const check = live?.extensions_check;
  const body = el("div", "ext-card");
  const rows = snap?.items || [];

  if (!snap || !snap.items) {
    body.append(el("p", "field-hint", uiText("ui.app.src.extensionscard.6c3d9de031")));
  } else if (!rows.length) {
    body.append(el("p", "field-hint", uiText("ui.app.src.extensionscard.dcf113ef22")));
  } else {
    for (const row of rows) body.append(rowLine(row));
    if (snap.updated_at) body.append(el("div", "field-hint", uiText("ui.app.src.extensionscard.8a14655c9e") + when(snap.updated_at)));
  }

  // Репетиция обновления: отчёт мастера лежит, пока не прошла удачная подмена.
  if (check && check.items && check.items.length) {
    const bad = check.items.filter((r) => r.state !== "loaded");
    const head = el("div", bad.length ? "receipt err" : "receipt ok",
      (bad.length ? uiText("ui.app.src.extensionscard.7c0025aefa") : uiText("ui.app.src.extensionscard.88674ed8c2")) + (check.summary || "") +
        (check.checked_at ? ` (${when(check.checked_at)})` : ""));
    body.append(head);
    for (const row of bad) {
      const line = rowLine(row);
      line.append(
        button(uiText("ui.app.src.extensionscard.5b98cf06ab"), "quiet", () => {
          void post("/api/say", { text: adaptNote(row, check, snap) })
            .then(() => toast(uiText("ui.app.src.extensionscard.4bccf6dc2c")))
            .catch((e) => toast(humanError(e).text));
        }),
      );
      body.append(line);
    }
    if (bad.length) {
      body.append(el("p", "field-hint",
        uiText("ui.app.src.extensionscard.97074975e5")));
    }
  }

  const actions = el("div", "actions");
  actions.append(
    button(uiText("ui.app.src.extensionscard.4b7eb537de"), "quiet", () =>
      void shell("open_path", { path: `${treePath}/extensions` }).catch((e) => toast(humanError(e).text))),
  );
  body.append(actions);

  return {
    el: card(
      uiText("ui.app.src.extensionscard.ba1b8e9001"),
      body,
      uiText("ui.app.src.extensionscard.dfcb30a606"),
    ),
  };
}
