import { t as uiText } from "../../lang/lang";
// Управление компьютером — опция ПОВЕРХ любого режима, не режим и не служба.
//
// Рука `computer` дерева (окна, экран, клавиатура и мышь, файлы и процессы)
// работает через отдельное тело: `helene-bridge.exe` + `helene-body.exe`,
// которые харнесс поднимает рядом с собой в сессии владельца
// (localharness/body.py). Ограда до тела не достаёт — оно снаружи контейнера,
// поэтому опция одинаково работает в песочнице и в интерактивном режиме.
//
// Что решает владелец и где это лежит (helene.json, блок `computer`):
//   * `enabled` — поднимать ли тело вообще; применяется ПЕРЕЗАПУСКОМ;
//   * `scopes`  — четыре права дерева (`computer.read` … `computer.apps`);
//                 харнесс перечитывает их на каждый вызов руки, перезапуск
//                 не нужен;
//   * `port`    — порт моста, окно его не трогает.
//
// Тексты — из трубы (`/api/mode` → `computer_option`, localharness/modes.py):
// своей копии здесь нет, иначе владелец читал бы одно, а агент получал другое.
// Живая правда о теле («подключено», «мост есть, тела нет», «выключено») —
// снимок харнесса `memory/.state/body.json`, он же `computer_live` в ответе
// трубы. Окно его только показывает и никогда не пишет.
//
// macOS (0.8.0): тело есть и там — `helene-body` и `helene-bridge` без `.exe`,
// окна через Accessibility. Системе нужны два разрешения (TCC): «Запись экрана»
// и «Универсальный доступ»; их состояние тело отдаёт в снимок (`tcc`, `hints`),
// а карточка рисует две строки «есть/нет» и кнопки «Открыть настройки»
// (команда оболочки `open_privacy_pane`). Выдать разрешение программно нельзя —
// галочку ставит владелец сам, и после обновления программы заново.
import { api, shell } from "../../ui-kit/window/api";
import { el, fmtTimeSec, humanError, toast } from "../../ui-kit/window/lib";
import contract from "../../ui-kit/contract.json";
import { button, toggle as switchRow } from "../../ui-kit/dom";
import type { ComputerLive, ComputerOption, ModeState } from "../../ui-kit/window/mode";
import { modeMetadata } from '../../ui-kit/window/mode-owned';
import { machineCaption } from '../../ui-kit/machine-status';

/** Что ЛЕЖИТ в файле — по нему и пишем обратно. */
export interface StoredComputer {
  enabled: boolean;
  scopes: string[];
}

export interface ComputerCard {
  /** Готовая карточка для экрана Настроек. */
  el: HTMLElement;
  /** `computer.enabled`, как его надо записать. */
  enabled(): boolean;
  /** `computer.scopes`, как их надо записать. */
  scopes(): string[];
}

/** Четыре права дерева — порядок показа тот же, что у харнесса; список — из
 *  ui-kit/contract.json (его же сверяют Python и Rust). Нужен только чтобы
 *  прочитать блок из файла ДО ответа канала: рисуем по его списку. */
export const COMPUTER_SCOPES: readonly string[] = contract.computer_scopes;

/** Блок `computer` из черновика конфига → что записано. Нет ключа `scopes` —
 *  все четыре (то же правило, что у харнесса: включил опцию — получил руку
 *  целиком, сузить можно галочками). */
export function storedComputer(block: unknown): StoredComputer {
  const b = block && typeof block === "object" && !Array.isArray(block) ? (block as Record<string, unknown>) : {};
  const raw = b.scopes;
  const scopes = raw === undefined
    ? [...COMPUTER_SCOPES]
    : Array.isArray(raw)
      ? COMPUTER_SCOPES.filter((s) => raw.map(String).includes(s))
      : [];
  return { enabled: b.enabled === true, scopes };
}

/** Имена тела и моста рядом с программой — по системе агента: на Mac без `.exe`. */
function bodyNames(mac: boolean, sep: string): string {
  return mac ? `helene-body${sep}helene-bridge` : `helene-body.exe${sep}helene-bridge.exe`;
}

/** Два разрешения macOS в порядке показа: ключ снимка, раздел настроек, слова. */
const TCC_ROWS = (): ReadonlyArray<[keyof NonNullable<ComputerLive["tcc"]>, string, string]> => [
  // ⚠ Имя пункта — ровно то, что написано в Системных настройках Sequoia:
  // «Запись экрана» там больше нет, есть «Запись экрана и системного звука».
  // Короткое имя отправляло владельца искать несуществующую строку.
  ["screen_recording", "screen", uiText("ui.app.src.computer.829ed8b787")],
  ["accessibility", "accessibility", uiText("ui.app.src.computer.ce2519caf3")],
];

const TCC_NOTE = () =>
  uiText("ui.app.src.computer.cdd00c811c");

/** Строка о теле по снимку харнесса. Только то, что он прислал. `mac` — система
 *  агента: на ней зависят только имена файлов тела. */
export function liveLine(live: ComputerLive | undefined, enabledNow: boolean, mac = false): { text: string; ok: boolean | null } {
  if (!live || typeof live !== "object" || live.enabled === undefined) {
    return { text: uiText("ui.app.src.computer.b6a61ac066"), ok: null };
  }
  const at = live.checked_at ? uiText("ui.app.src.computer.27bcfb63b6", {p0: String(fmtTimeSec(live.checked_at))}) : "";
  if (!live.enabled) {
    const have = live.available === false ? uiText("ui.app.src.computer.aa759b8f1f", {p0: String(bodyNames(mac, uiText("ui.app.src.computer.21db14e45f")))}) : "";
    const pending = enabledNow ? uiText("ui.app.src.computer.961a7b4df7") : "";
    return { text: uiText("ui.app.src.computer.b06efdab80", {p0: String(have), p1: String(pending)}), ok: null };
  }
  if (live.available === false) return { text: uiText("ui.app.src.computer.24a38cbcb5", {p0: String(machineCaption(live,'reason') || uiText("ui.app.src.computer.11e209b450", {p0: String(bodyNames(mac, " / "))}))}), ok: false };
  if (live.connected === true) {
    if (live.platform === "linux" && live.console === false) {
      return { text: uiText("ui.app.src.computer.b23853f545", {p0: String((live.hints || []).join(' ')), p1: String(at)}), ok: false };
    }
    if (live.platform === "linux" && live.console !== true) {
      return { text: uiText("ui.app.src.computer.3a3db14e03", {p0: String(at)}), ok: null };
    }
    const id = live.identity || {};
    const who = [id.kind, id.session_id !== undefined && id.session_id !== null ? uiText("ui.app.src.computer.02e323576f", {p0: String(id.session_id)}) : "", id.integrity].filter(Boolean).join(", ");
    return { text: uiText("ui.app.src.computer.1a24fbb915", {p0: String(live.port || "?"), p1: String(who ? ` (${who})` : ""), p2: String(at)}), ok: true };
  }
  if (live.connected === false) return { text: `${machineCaption(live,'reason') || uiText("ui.app.src.computer.a51665ae00")}${at}`, ok: false };
  return { text: `${machineCaption(live,'reason') || uiText("ui.app.src.computer.2a323bd1f1")}${at}`, ok: null };
}

/**
 * Карточка «Управление компьютером».
 *
 * @param live     ответ `/api/mode` (там `computer_option` и `computer_live`); null — труба не ответила
 * @param stored   блок `computer` из ФАЙЛА: по нему пишем обратно
 * @param platform система агента (`windows` | `macos`; пусто — Windows, как было): на Mac —
 *                 имена без `.exe` и строки про два разрешения системы
 */
export function computerCard(live: ModeState | null, stored: StoredComputer, platform = ""): ComputerCard {
  const box = el("section", "card");
  const mac = platform === "macos";
  const posix = mac || platform === "linux";
  let enabled = stored.enabled;
  const scopes = new Set(stored.scopes);
  const option: ComputerOption | null | undefined = modeMetadata(live?.computer_option);
  box.append(el("h3", "", option?.title || uiText("ui.app.src.computer.d58fe7fd81")));

  // Труба не прислала опции — харнесс старее окна. Своих слов не пишем; блок
  // в файле уедет обратно таким, каким лежал.
  if (!option) {
    box.append(
      el(
        "p",
        "receipt err",
        uiText("ui.app.src.computer.a43a180213"),
      ),
    );
    return { el: box, enabled: () => enabled, scopes: () => [...scopes] };
  }

  box.append(el("p", "field-hint", option.text));
  // Оговорка — всегда на виду, а не после включения: её читают ДО.
  if (option.warning) box.append(el("p", "receipt err", option.warning));

  const status = el("p", "receipt");
  const master = switchRow(option.title, enabled, (v) => {
    enabled = v;
    syncScopes();
    syncStatus(live?.computer_live);
  });
  box.append(master);

  // Четыре права — по списку трубы, а не своему: харнесс знает и порядок, и
  // слова. Ключ, которого окно не знает, честно называем — молча съесть
  // галочку хуже, чем сказать «правь руками».
  const rights = el("div", "mode-block");
  const scopeRows: HTMLButtonElement[] = [];
  for (const s of option.scopes || []) {
    const known = (COMPUTER_SCOPES as readonly string[]).includes(s.key);
    if (!known) {
      rights.append(
        el("p", "receipt err", uiText("ui.app.src.computer.5c904b8350", {p0: String(s.title || s.key)})),
        el("p", "field-hint", uiText("ui.app.src.computer.0421599c96", {p0: String(s.text || "—")})),
      );
      continue;
    }
    const row = switchRow(s.title || s.key, scopes.has(s.key), (v) => {
      if (v) scopes.add(s.key);
      else scopes.delete(s.key);
      syncScopes();
    });
    row.title = s.key;
    scopeRows.push(row);
    rights.append(row, el("p", "field-hint", `${s.text || ""} (${s.key})`));
  }
  const rightsNote = el("p", "field-hint");
  rights.append(rightsNote);
  box.append(rights);

  const syncScopes = () => {
    for (const r of scopeRows) r.setAttribute("aria-disabled", String(!enabled));
    if (!enabled) {
      rightsNote.className = "field-hint";
      rightsNote.textContent = uiText("ui.app.src.computer.6b6c8e9ebd");
    } else if (!scopes.size) {
      rightsNote.className = "receipt err";
      rightsNote.textContent = uiText("ui.app.src.computer.a7552be5f2");
    } else {
      rightsNote.className = "field-hint";
      rightsNote.textContent =
        uiText("ui.app.src.computer.9953806a14");
    }
  };

  // --- macOS: два разрешения системы — по слову тела (снимок `tcc`), не по
  // догадке. Нет поля — строк нет: тело ещё не спрашивали или оно старее окна.
  const tccBox = el("div", "mode-block");
  tccBox.hidden = true;
  const syncTcc = (snap: ComputerLive | undefined) => {
    tccBox.replaceChildren();
    const tcc = mac && snap && typeof snap === "object" ? snap.tcc : null;
    tccBox.hidden = !tcc || typeof tcc !== "object";
    if (tccBox.hidden || !tcc) return;
    for (const [key, kind, title] of TCC_ROWS()) {
      const ok = tcc[key] === true;
      const row = el("div", "actions");
      row.append(el("span", "receipt " + (ok ? "ok" : "err"), `${title}: ${ok ? uiText("ui.app.src.computer.747f78e5a0") : uiText("ui.app.src.computer.dc9337ff5b")}`));
      if (!ok) {
        row.append(
          button(uiText("ui.app.src.computer.7c8ef82d31"), "quiet", () => void shell("open_privacy_pane", { kind }).catch((e) => toast(humanError(e).text))),
        );
      }
      tccBox.append(row);
    }
    for (const hint of snap?.hints || []) if (hint) tccBox.append(el("p", "field-hint", hint));
    tccBox.append(el("p", "field-hint", TCC_NOTE()));
  };

  // --- живое состояние: снимок харнесса, кнопка «Проверить» перечитывает трубу.
  const syncStatus = (snap: ComputerLive | undefined) => {
    const line = liveLine(snap, enabled, posix);
    status.className = "receipt " + (line.ok === true ? "ok" : line.ok === false ? "err" : "");
    status.textContent = line.text;
    syncTcc(snap);
  };
  const check = button(uiText("ui.app.src.computer.e4424a6df6"), "quiet", async () => {
    try {
      const fresh = await api<ModeState>("/api/mode");
      syncStatus(fresh.computer_live);
    } catch (e) {
      status.className = "receipt err";
      status.textContent = humanError(e).text;
    }
  });
  const row = el("div", "actions");
  row.append(check, status);
  box.append(row, tccBox);

  const logs = live?.computer_live?.logs;
  box.append(
    el(
      "p",
      "field-hint",
      uiText("ui.app.src.computer.86e8e82cc4") +
        (logs && logs.length ? uiText("ui.app.src.computer.e87da5285c", {p0: String(logs.join(", "))}) : ""),
    ),
  );

  syncScopes();
  syncStatus(live?.computer_live);
  return { el: box, enabled: () => enabled, scopes: () => COMPUTER_SCOPES.filter((s) => scopes.has(s)) };
}
