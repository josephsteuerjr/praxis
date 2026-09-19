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
const TCC_ROWS: ReadonlyArray<[keyof NonNullable<ComputerLive["tcc"]>, string, string]> = [
  ["screen_recording", "screen", "Запись экрана"],
  ["accessibility", "accessibility", "Универсальный доступ"],
];

const TCC_NOTE =
  "Оба разрешения выдаются в Системных настройках, раздел «Конфиденциальность и безопасность». " +
  "После обновления программы система считает её новой: убери Helene из списка и добавь снова.";

/** Строка о теле по снимку харнесса. Только то, что он прислал. `mac` — система
 *  агента: на ней зависят только имена файлов тела. */
export function liveLine(live: ComputerLive | undefined, enabledNow: boolean, mac = false): { text: string; ok: boolean | null } {
  if (!live || typeof live !== "object" || live.enabled === undefined) {
    return { text: "Снимка тела ещё нет: код агента пишет его при старте.", ok: null };
  }
  const at = live.checked_at ? ` Проверено ${fmtTimeSec(live.checked_at)}.` : "";
  if (!live.enabled) {
    const have = live.available === false ? ` Тела в сборке нет: ${bodyNames(mac, " и ")} рядом с программой не найдены.` : "";
    const pending = enabledNow ? " Включено в черновике — поднимется после сохранения и перезапуска." : "";
    return { text: `Выключено.${have}${pending}`, ok: null };
  }
  if (live.available === false) return { text: `Опция включена, но тела в сборке нет: ${live.reason || `${bodyNames(mac, " / ")} не найдены`}.`, ok: false };
  if (live.connected === true) {
    const id = live.identity || {};
    const who = [id.kind, id.session_id !== undefined && id.session_id !== null ? `сессия ${id.session_id}` : "", id.integrity].filter(Boolean).join(", ");
    return { text: `Тело подключено: мост 127.0.0.1:${live.port || "?"}${who ? ` (${who})` : ""}.${at}`, ok: true };
  }
  if (live.connected === false) return { text: `${live.reason || "Тело не отвечает."}${at}`, ok: false };
  return { text: `${live.reason || "Поднимается."}${at}`, ok: null };
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
  let enabled = stored.enabled;
  const scopes = new Set(stored.scopes);
  const option: ComputerOption | null | undefined = live?.computer_option;
  box.append(el("h3", "", option?.title || "Управление компьютером"));

  // Труба не прислала опции — харнесс старее окна. Своих слов не пишем; блок
  // в файле уедет обратно таким, каким лежал.
  if (!option) {
    box.append(
      el(
        "p",
        "receipt err",
        "Про управление компьютером код агента ничего не рассказал — похоже, он старее окна (тело тулы `computer` появилось в 0.3.1). " +
          "Галочки окно оставит в файле такими, какие они есть.",
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
        el("p", "receipt err", `${s.title || s.key}: это право окно писать не умеет — код агента новее окна.`),
        el("p", "field-hint", `Правь его руками в helene.json, список computer.scopes. Что делает: ${s.text || "—"}`),
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
      rightsNote.textContent = "Опция выключена: права лежат в файле, но тела нет и тул отказывает словами. Включение применяется перезапуском.";
    } else if (!scopes.size) {
      rightsNote.className = "receipt err";
      rightsNote.textContent = "Ни одного права не выдано — тело поднимется, а тул откажет на любое действие. Так тоже можно, но зачем.";
    } else {
      rightsNote.className = "field-hint";
      rightsNote.textContent =
        "Права код агента перечитывает из файла на каждый вызов тулы: после «Сохранить» они действуют сразу, перезапуск нужен только для включения самого тела.";
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
    for (const [key, kind, title] of TCC_ROWS) {
      const ok = tcc[key] === true;
      const row = el("div", "actions");
      row.append(el("span", "receipt " + (ok ? "ok" : "err"), `${title}: ${ok ? "есть" : "нет"}`));
      if (!ok) {
        row.append(
          button("Открыть настройки", "quiet", () => void shell("open_privacy_pane", { kind }).catch((e) => toast(humanError(e).text))),
        );
      }
      tccBox.append(row);
    }
    for (const hint of snap?.hints || []) if (hint) tccBox.append(el("p", "field-hint", hint));
    tccBox.append(el("p", "field-hint", TCC_NOTE));
  };

  // --- живое состояние: снимок харнесса, кнопка «Проверить» перечитывает трубу.
  const syncStatus = (snap: ComputerLive | undefined) => {
    const line = liveLine(snap, enabled, mac);
    status.className = "receipt " + (line.ok === true ? "ok" : line.ok === false ? "err" : "");
    status.textContent = line.text;
    syncTcc(snap);
  };
  const check = button("Проверить", "quiet", async () => {
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
      "Тело живёт в твоей сессии, снаружи ограды, и умирает вместе с кодом агента. Токены — только в памяти процесса, на диске их нет." +
        (logs && logs.length ? ` Логи тела: ${logs.join(", ")}.` : ""),
    ),
  );

  syncScopes();
  syncStatus(live?.computer_live);
  return { el: box, enabled: () => enabled, scopes: () => COMPUTER_SCOPES.filter((s) => scopes.has(s)) };
}
