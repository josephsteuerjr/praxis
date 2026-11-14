import { t as uiText } from "../../lang/lang";
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
import { S } from "../../ui-kit/window/state";
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
  box.append(el("h3", "", uiText("ui.app.src.mounts.d6b58137df")));
  const state = el("p", "field-hint");
  const listBox = el("div", "mount-list");
  const askBox = el("div", "mount-list");
  const denyBox = el("div", "mount-list");
  const addOut = el("p", "receipt");

  const liveOf = (path: string): LiveMount | undefined => {
    const key = mountKey(path);
    return (liveSandbox?.mounts || []).find((m) => mountKey(m.path) === key || mountKey(m.real) === key);
  };
  const answerFor = (path: string): "mounted" | "denied" | "" => {
    const key = mountKey(path);
    if (rows.some((r) => mountKey(r.path) === key)) return "mounted";
    if (denials.some((d) => mountKey(d.path) === key)) return "denied";
    return "";
  };

  const setFence = (on: boolean, title: string) => {
    fenceTitle = title || fenceTitle;
    state.className = "field-hint";
    state.textContent = on
      ? uiText("ui.app.src.mounts.98af5d99c3", {p0: String(fenceTitle)})
      : uiText("ui.app.src.mounts.ef5f37c2ed", {p0: String(fenceTitle)}) +
        uiText("ui.app.src.mounts.6acc9eda3e");
  };

  // --- список смонтированного
  const drawList = () => {
    listBox.replaceChildren();
    if (!rows.length) {
      listBox.append(el("p", "field-hint", uiText("ui.app.src.mounts.bbc86e42da")));
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
        smallBtn(uiText("ui.app.src.mounts.07cb650874"), "quiet", () => {
          const i = rows.indexOf(row);
          if (i >= 0) rows.splice(i, 1);
          drawList();
          drawAsk();
          say(uiText("ui.app.src.mounts.56088bdb01"));
        }),
      );
      line.append(head);
      if (row.why) line.append(el("p", "field-hint", uiText("ui.app.src.mounts.53e1bcc419", {p0: String(row.why)})));
      // Приговор харнесса по этой папке — из снимка анатомии, а не наш.
      const live = liveOf(row.path);
      if (live?.error) line.append(el("p", "receipt err", uiText("ui.app.src.mounts.0393c1ff9b", {p0: String(live.error)})));
      else if (live?.link_error) {
        line.append(
          el("p", "receipt err", uiText("ui.app.src.mounts.6639a802df", {p0: String(live.link_error)})),
        );
      } else if (live?.link) line.append(el("p", "field-hint", uiText("ui.app.src.mounts.ab5133569c", {p0: String(live.link)})));
      // Про «нет в снимке» говорим, только если снимок вообще есть: иначе это
      // не факт о папке, а наша слепота, и писать её строкой под каждой папкой —
      // шум. О самой слепоте сказано один раз, в блоке просьб.
      else if (!live && liveSandbox) {
        line.append(el("p", "field-hint", uiText("ui.app.src.mounts.552594604f")));
      }
      listBox.append(line);
    }
  };

  const say = (text: string, bad = false) => {
    addOut.className = "receipt" + (bad ? " err" : "");
    addOut.textContent = text;
  };

  const add = (path: string, access: "read" | "write", why: string): boolean => {
    // Форма полного пути — системы агента: на macOS `/Users/…`, а не `C:\…`.
    const problem = pathProblem(path, S.platform);
    if (problem) {
      say(problem, true);
      return false;
    }
    const clean = path.trim().replace(/^"+|"+$/g, "").replace(/[\\/]+$/, "");
    const known = answerFor(clean);
    if (known === "mounted") {
      say(uiText("ui.app.src.mounts.55365d575d"), true);
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
  input.placeholder = S.platform === "linux" ? uiText("ui.app.src.mounts.e3578e4bf4") : S.platform === "macos" ? uiText("ui.app.src.mounts.826a161eb0") : uiText("ui.app.src.mounts.2da8cc9935");
  input.autocomplete = "off";
  input.spellcheck = false;
  let addAccess: "read" | "write" = "read";
  const addBtn = smallBtn(uiText("ui.app.src.mounts.71038c53bb"), "quiet", () => {
    if (add(input.value, addAccess, "")) {
      say(uiText("ui.app.src.mounts.c3a0c6a88f", {p0: String(input.value.trim()), p1: String(ACCESS_WORDS[addAccess])}));
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
    askBox.append(el("h4", "", uiText("ui.app.src.mounts.dd489211fc")));
    if (!liveSandbox) {
      const why = liveFail ? uiText("ui.app.src.mounts.0ece825edc") : uiText("ui.app.src.mounts.1522748f4c");
      askBox.append(el("p", "receipt err", why));
      return;
    }
    const asks = (liveSandbox.mount_requests || []).filter((r) => {
      const key = mountKey(r.path || r.real);
      return key && !answered.has(key) && !answerFor(String(r.path || r.real || ""));
    });
    if (!asks.length) {
      askBox.append(el("p", "field-hint", uiText("ui.app.src.mounts.563528a6a5")));
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
      reason.placeholder = uiText("ui.app.src.mounts.f95e53c536");
      reason.autocomplete = "off";
      head.append(
        smallBtn(uiText("ui.app.src.mounts.2e4dde93c7"), "primary", () => {
          if (add(path, give, String(ask.why || ""))) {
            answered.add(mountKey(path));
            say(uiText("ui.app.src.mounts.3d4e4d770f", {p0: String(path), p1: String(ACCESS_WORDS[give])}));
          }
        }),
        smallBtn(uiText("ui.app.src.mounts.586dfefc2d"), "quiet", () => {
          const key = mountKey(path);
          for (let i = rows.length - 1; i >= 0; i--) if (mountKey(rows[i].path) === key) rows.splice(i, 1);
          denials.push({ path: path.trim().replace(/[\\/]+$/, ""), why: reason.value.trim() || undefined, at: stamp() });
          answered.add(key);
          drawList();
          drawDenied();
          drawAsk();
          say(uiText("ui.app.src.mounts.d84ecb4e47"));
        }),
      );
      line.append(head);
      const asked = Number(ask.asked || 1);
      const when = ask.at ? `, ${ask.at}` : "";
      line.append(el("p", "field-hint", uiText("ui.app.src.mounts.98720ed4e9", {p0: String(ACCESS_WORDS[access]), p1: String(when), p2: String(asked > 1 ? uiText("ui.app.src.mounts.a8dfbdc5f5", {p0: String(asked)}) : "")})));
      if (ask.why) line.append(el("p", "choice-text", uiText("ui.app.src.mounts.53e1bcc419", {p0: String(ask.why)})));
      line.append(reason);
      askBox.append(line);
    }
  };

  // --- отказы
  const drawDenied = () => {
    denyBox.replaceChildren();
    if (!denials.length) return;
    denyBox.append(el("h4", "", uiText("ui.app.src.mounts.c65141292b")));
    denyBox.append(
      el("p", "field-hint", uiText("ui.app.src.mounts.302e386a77")),
    );
    for (const row of denials) {
      const line = el("div", "mount-row");
      const head = el("div", "mount-head");
      head.append(el("span", "mount-path mono", row.path));
      head.append(
        smallBtn(uiText("ui.app.src.mounts.5ae0e885f6"), "quiet", () => {
          const i = denials.indexOf(row);
          if (i >= 0) denials.splice(i, 1);
          drawDenied();
          drawAsk();
          say(uiText("ui.app.src.mounts.79a7515dcf"));
        }),
      );
      line.append(head);
      const tail = [row.at ? String(row.at) : "", row.why ? uiText("ui.app.src.mounts.a9a37ae9a7", {p0: String(row.why)}) : ""].filter(Boolean).join(" · ");
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
      uiText("ui.app.src.mounts.6acb438f32"),
    ),
  );

  return {
    el: box,
    mounts: () => rows.map((r) => ({ ...r })),
    denied: () => denials.map((r) => ({ ...r })),
    setFence,
  };
}
