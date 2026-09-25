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
  loaded: "подключено",
  disabled: "выключено",
  incompatible: "несовместимо",
  error: "не загрузилось",
  pending: "проверяется",
};

function when(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

function rowLine(row: ExtensionRow): HTMLElement {
  const ok = row.state === "loaded";
  const line = el("div", "ext-row");
  line.append(el("span", ok ? "receipt ok" : "receipt err", `${row.name} ${row.version || ""}`.trim() + " — " + (STATE_WORD[row.state] || row.state)));
  const details: string[] = [];
  if (row.tools && row.tools.length) details.push("тулы: " + row.tools.join(", "));
  if (row.hooks && Object.keys(row.hooks).length) details.push("крючки: " + Object.keys(row.hooks).join(", "));
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
    `Адаптируй, пожалуйста, моё расширение «${row.name}» ${row.version || ""}`.trim() +
      ` под текущую версию программы${helene ? ` (${helene})` : ""} и API ${api}.`,
    `Папка: ${row.dir || `data/extensions/${row.name}`}.`,
    `При проверке оно не загрузилось: ${row.reason || "причина не названа"}.`,
    row.api ? `Расширение написано под ${row.api}.` : "",
    "Что нужно: прочитать extension.json и код, переписать под API helene.ext текущей версии (register(api) → api.register_tool / api.register_hook), " +
      "прогнать acceptance, если он есть, и показать мне дифф с расписками. Ничего не подменять без моего «да».",
  ].filter(Boolean).join("\n");
}

export function extensionsCard(live: ModeState | null, treePath: string): { el: HTMLElement } {
  const snap = live?.extensions_live;
  const check = live?.extensions_check;
  const body = el("div", "ext-card");
  const rows = snap?.items || [];

  if (!snap || !snap.items) {
    body.append(el("p", "field-hint", "Код агента про расширения ничего не рассказал — похоже, он старее окна (расширения появились в 0.8.7)."));
  } else if (!rows.length) {
    body.append(el("p", "field-hint", "Своих расширений нет. Папка для них — data/extensions/<имя>/ с extension.json и модулем; пример и правила — в РАСШИРЕНИЯ.md рядом с программой."));
  } else {
    for (const row of rows) body.append(rowLine(row));
    if (snap.updated_at) body.append(el("div", "field-hint", "снимок раннера: " + when(snap.updated_at)));
  }

  // Репетиция обновления: отчёт мастера лежит, пока не прошла удачная подмена.
  if (check && check.items && check.items.length) {
    const bad = check.items.filter((r) => r.state !== "loaded");
    const head = el("div", bad.length ? "receipt err" : "receipt ok",
      (bad.length ? "Репетиция обновления: " : "Репетиция обновления прошла: ") + (check.summary || "") +
        (check.checked_at ? ` (${when(check.checked_at)})` : ""));
    body.append(head);
    for (const row of bad) {
      const line = rowLine(row);
      line.append(
        button("Поручить агенту адаптировать", "quiet", () => {
          void post("/api/say", { text: adaptNote(row, check, snap) })
            .then(() => toast("Записка агенту отправлена"))
            .catch((e) => toast(humanError(e).text));
        }),
      );
      body.append(line);
    }
    if (bad.length) {
      body.append(el("p", "field-hint",
        "Пока расширение не адаптировано, программа не обновится сама: «Обновить без него» — запусти установщик с --force-extensions (Настройки → Программа), старая версия остаётся живой."));
    }
  }

  const actions = el("div", "actions");
  actions.append(
    button("Открыть папку расширений", "quiet", () =>
      void shell("open_path", { path: `${treePath}/extensions` }).catch((e) => toast(humanError(e).text))),
  );
  body.append(actions);

  return {
    el: card(
      "Расширения",
      body,
      "Свои тулы и крючки живут отдельными модулями с манифестом и версией API (helene.ext/1), а не патчами кода агента: обновление их не трогает, " +
        "несовместимость видна словами при загрузке, а чинить её — работа агента.",
    ),
  };
}
