// Монтирование экраном: список смонтированного, добавление, снятие и просьбы
// агента с кнопками «дать» и «отказать».
//
// Правила разбора и записи живут рядом, в `./mountdata` — там же они и
// проверены прогоном. Здесь только DOM: этот файл ничего не решает сам.
//
// Что где лежит (разбор — localharness/fence.py):
//   * `sandbox.mounts` в helene.json — РЕШЕНИЕ ВЛАДЕЛЬЦА, что открыто;
//   * `sandbox.mounts_denied` — его же отказы: «нет» должно пережить
//     перезапуск, иначе агент спрашивает по кругу;
//   * `data/memory/.state/mounts.json` — ПРОСЬБЫ АГЕНТА (рука
//     `mount_request`). Это его слово, а не решение владельца: окно их
//     только показывает и никогда не пишет.
//
// Живую правду о том, что из списка ДЕЙСТВИТЕЛЬНО открылось (плохой путь,
// системная папка, не заведённый стык в `mnt/`), знает только харнесс, и она
// приезжает снимком в анатомии.
import { el } from "../../ui-kit/window/lib";
import { button as smallBtn } from "../../ui-kit/dom";
import {
  ACCESS_WORDS,
  mountAccess,
  mountKey,
  parseDenied,
  parseMounts,
  pathProblem,
  stamp,
  type DeniedRow,
  type LiveMount,
  type LiveSandbox,
  type MountRow,
} from "./mountdata";

// Типы монтирования — один набор на карточку и на её правила: экран настроек
// берёт `LiveSandbox` отсюда, чтобы не знать про два файла.
export type { DeniedRow, LiveMount, LiveSandbox, MountRequest, MountRow } from "./mountdata";

export interface MountsCard {
  /** Готовая карточка для экрана Настроек. */
  el: HTMLElement;
  /** Что записать в `sandbox.mounts`. */
  mounts(): MountRow[];
  /** Что записать в `sandbox.mounts_denied`. */
  denied(): DeniedRow[];
  /** Ограда сменилась в соседней карточке: строка состояния должна это сказать. */
  setFence(on: boolean, title: string): void;
}

function accessSelect(value: "read" | "write", onChange: (v: "read" | "write") => void): HTMLSelectElement {
  const sel = el("select", "field-input mount-access");
  for (const name of ["read", "write"] as const) {
    const opt = el("option", "", ACCESS_WORDS[name]);
    opt.value = name;
    sel.append(opt);
  }
  sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value === "write" ? "write" : "read"));
  return sel;
}

/**
 * Карточка «Монтирование».
 *
 * @param sandboxBlock блок `sandbox` из ЧЕРНОВИКА конфига (правит владелец)
 * @param liveSandbox  блок `sandbox` из анатомии; null — снимок не прочитан
 * @param liveFail     почему снимок не прочитан (говорим словами, а не молчим)
 * @param fenceOn      выбрана ли сейчас ограда «Песочница»
 * @param fenceTitle   как эта ограда называется у харнесса
 */
export function mountsCard(
  sandboxBlock: Record<string, unknown> | undefined,
  liveSandbox: LiveSandbox | null,
  liveFail: unknown,
  fenceOn: boolean,
  fenceTitle: string,
): MountsCard {
  const rows = parseMounts(sandboxBlock?.mounts);
  const denials = parseDenied(sandboxBlock?.mounts_denied);
  // Просьбы, на которые владелец ответил прямо сейчас: харнесс уберёт их из
  // своего файла сам (`Mounts.forget_answered`), но не раньше, чем прочитает
  // сохранённый конфиг. До тех пор их прячет окно — иначе владелец жмёт «дать»,
  // а просьба остаётся на экране, как будто он ничего не сделал.
  const answered = new Set<string>();

  const box = el("section", "card");
  box.append(el("h3", "", "Монтирование"));
  const state = el("p", "field-hint");
  const listBox = el("div", "mount-list");
  const askBox = el("div", "mount-list");
  const denyBox = el("div", "mount-list");
  const addOut = el("p", "receipt");

  const liveOf = (path: string): LiveMount | undefined => {
    const key = mountKey(path);
    return (liveSandbox?.mounts || []).find((m) => mountKey(m.path) === key || mountKey(m.real) === key);
  };
  const answerFor = (path: string): string => {
    const key = mountKey(path);
    if (rows.some((r) => mountKey(r.path) === key)) return "уже смонтирована";
    if (denials.some((d) => mountKey(d.path) === key)) return "уже в отказах";
    return "";
  };

  const setFence = (on: boolean, title: string) => {
    fenceTitle = title || fenceTitle;
    state.className = "field-hint";
    state.textContent = on
      ? `Ограда «${fenceTitle}» включена: агент видит только свою папку и то, что в списке ниже.`
      : `Сейчас выбрана ограда «${fenceTitle}» — она агента в папке не запирает: файловые тулы и shell видят всё, ` +
        `что доступно твоей учётке, монтировать нечего. Список ниже сохранится и заработает, когда включишь песочницу.`;
  };

  // --- список смонтированного
  const drawList = () => {
    listBox.replaceChildren();
    if (!rows.length) {
      listBox.append(el("p", "field-hint", "Пока ничего не смонтировано: за пределы своей папки агент не выходит."));
      return;
    }
    for (const row of rows) {
      const line = el("div", "mount-row");
      const head = el("div", "mount-head");
      head.append(el("span", "mount-path mono", row.path));
      head.append(
        accessSelect(row.access, (v) => {
          row.access = v;
          drawList();
        }),
      );
      head.append(
        smallBtn("Убрать", "quiet", () => {
          const i = rows.indexOf(row);
          if (i >= 0) rows.splice(i, 1);
          drawList();
          drawAsk();
          say("Убрал из списка. Папка закроется, когда сохранишь.");
        }),
      );
      line.append(head);
      if (row.why) line.append(el("p", "field-hint", `Зачем: ${row.why}`));
      // Приговор харнесса по этой папке — из снимка анатомии, а не наш.
      const live = liveOf(row.path);
      if (live?.error) line.append(el("p", "receipt err", `Не открыта: ${live.error}`));
      else if (live?.link_error) {
        line.append(
          el("p", "receipt err", `Папка открыта по полному пути, но стык в mnt/ не завёлся: ${live.link_error}`),
        );
      } else if (live?.link) line.append(el("p", "field-hint", `Агенту видна как ${live.link}`));
      // Про «нет в снимке» говорим, только если снимок вообще есть: иначе это
      // не факт о папке, а наша слепота, и писать её строкой под каждой папкой —
      // шум. О самой слепоте сказано один раз, в блоке просьб.
      else if (!live && liveSandbox) {
        line.append(el("p", "field-hint", "В снимке программы агента этой папки ещё нет — появится после сохранения и перезапуска."));
      }
      listBox.append(line);
    }
  };

  const say = (text: string, bad = false) => {
    addOut.className = "receipt" + (bad ? " err" : "");
    addOut.textContent = text;
  };

  const add = (path: string, access: "read" | "write", why: string): boolean => {
    const problem = pathProblem(path);
    if (problem) {
      say(problem, true);
      return false;
    }
    const clean = path.trim().replace(/^"+|"+$/g, "").replace(/[\\/]+$/, "");
    const known = answerFor(clean);
    if (known === "уже смонтирована") {
      say("Эта папка уже в списке.", true);
      return false;
    }
    // Отказ на ту же папку снимаем молча: владелец передумал — это его право, а
    // держать «нет» рядом с «да» значило бы врать агенту обоими списками сразу.
    const key = mountKey(clean);
    for (let i = denials.length - 1; i >= 0; i--) if (mountKey(denials[i].path) === key) denials.splice(i, 1);
    rows.push({ path: clean, access, why: why.trim() || undefined, at: stamp() });
    drawList();
    drawDenied();
    drawAsk();
    return true;
  };

  // --- добавить папку руками
  const addRow = el("div", "mount-add");
  const input = el("input", "field-input mono");
  input.type = "text";
  input.placeholder = "C:\\Users\\Имя\\Документы";
  input.autocomplete = "off";
  input.spellcheck = false;
  let addAccess: "read" | "write" = "read";
  const addBtn = smallBtn("Добавить", "quiet", () => {
    if (add(input.value, addAccess, "")) {
      say(`Добавил: ${input.value.trim()} (${ACCESS_WORDS[addAccess]}). Папка откроется, когда сохранишь.`);
      input.value = "";
    }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addBtn.click();
    }
  });
  addRow.append(input, accessSelect(addAccess, (v) => (addAccess = v)), addBtn);

  // --- просьбы агента
  const drawAsk = () => {
    askBox.replaceChildren();
    askBox.append(el("h4", "", "Просьбы агента"));
    if (!liveSandbox) {
      const why = liveFail ? "Снимок устройства не прочитан — просьбы агента спросить не у кого." : "Снимок устройства пуст — просьб не видно.";
      askBox.append(el("p", "receipt err", why));
      return;
    }
    const asks = (liveSandbox.mount_requests || []).filter((r) => {
      const key = mountKey(r.path || r.real);
      return key && !answered.has(key) && !answerFor(String(r.path || r.real || ""));
    });
    if (!asks.length) {
      askBox.append(el("p", "field-hint", "Агент ничего не просил. Попросить папку он может сам — тулом mount_request; решаешь ты, здесь."));
      return;
    }
    for (const ask of asks) {
      const path = String(ask.path || ask.real || "");
      const access = mountAccess(ask.access);
      const line = el("div", "mount-row");
      const head = el("div", "mount-head");
      head.append(el("span", "mount-path mono", path));
      let give: "read" | "write" = access;
      head.append(accessSelect(access, (v) => (give = v)));
      const reason = el("input", "field-input");
      reason.type = "text";
      reason.placeholder = "почему нет (необязательно)";
      reason.autocomplete = "off";
      head.append(
        smallBtn("Дать", "primary", () => {
          if (add(path, give, String(ask.why || ""))) {
            answered.add(mountKey(path));
            say(`Дал: ${path} (${ACCESS_WORDS[give]}). Папка откроется, когда сохранишь.`);
          }
        }),
        smallBtn("Отказать", "quiet", () => {
          const key = mountKey(path);
          for (let i = rows.length - 1; i >= 0; i--) if (mountKey(rows[i].path) === key) rows.splice(i, 1);
          denials.push({ path: path.trim().replace(/[\\/]+$/, ""), why: reason.value.trim() || undefined, at: stamp() });
          answered.add(key);
          drawList();
          drawDenied();
          drawAsk();
          say("Отказал. Агент прочитает отказ и не будет спрашивать второй раз — после сохранения.");
        }),
      );
      line.append(head);
      const asked = Number(ask.asked || 1);
      const when = ask.at ? `, ${ask.at}` : "";
      line.append(el("p", "field-hint", `Просит на ${ACCESS_WORDS[access]}${when}${asked > 1 ? `, спрашивал ${asked} раз` : ""}.`));
      if (ask.why) line.append(el("p", "choice-text", `Зачем: ${ask.why}`));
      line.append(reason);
      askBox.append(line);
    }
  };

  // --- отказы
  const drawDenied = () => {
    denyBox.replaceChildren();
    if (!denials.length) return;
    denyBox.append(el("h4", "", "Отказано"));
    denyBox.append(
      el("p", "field-hint", "Про эти папки агент уже получил «нет» и второй раз не спросит. Вернёшь — сможет попросить снова."),
    );
    for (const row of denials) {
      const line = el("div", "mount-row");
      const head = el("div", "mount-head");
      head.append(el("span", "mount-path mono", row.path));
      head.append(
        smallBtn("Вернуть", "quiet", () => {
          const i = denials.indexOf(row);
          if (i >= 0) denials.splice(i, 1);
          drawDenied();
          drawAsk();
          say("Убрал из отказов. Агент сможет попросить эту папку снова — после сохранения.");
        }),
      );
      line.append(head);
      const tail = [row.at ? String(row.at) : "", row.why ? `почему: ${row.why}` : ""].filter(Boolean).join(" · ");
      if (tail) line.append(el("p", "field-hint", tail));
      denyBox.append(line);
    }
  };

  setFence(fenceOn, fenceTitle);
  drawList();
  drawAsk();
  drawDenied();
  box.append(
    state,
    listBox,
    addRow,
    addOut,
    askBox,
    denyBox,
    el(
      "p",
      "field-hint",
      "Смонтированная папка открывается агенту целиком: он видит её и внутри своего дома, как mnt/<имя>. " +
        "Годится ли путь, решает программа агента — системную папку, корень диска и папку самой Hélène он не откроет и скажет почему. " +
        "Список применяется сохранением; перезапуск для этого не нужен.",
    ),
  );

  return {
    el: box,
    mounts: () => rows.map((r) => ({ ...r })),
    denied: () => denials.map((r) => ({ ...r })),
    setFence,
  };
}
