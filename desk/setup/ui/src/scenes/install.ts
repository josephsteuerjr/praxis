// Сцена «Установить»: сводка решений, кнопка, ход установки листом фаз и кнопка
// «Открыть». Установка начинается только нажатием кнопки.
//
// 1.2 (27.09): ход — не список строк, а ЛИСТ фаз, который виден целиком с первой
// секунды: проверка, снимок памяти, раскладка рядом (с живой полосой: файлы и
// мегабайты), остановка прежней, подмена, настройка, ярлыки, служба. «Отмена» —
// на каждом шаге до подмены (требование Егора): новая версия раскладывается рядом,
// прежняя работает, отмена = откат, прежняя цела. Подмена — доли секунды, в это
// время кнопка честно говорит, почему её нельзя нажать.
import { MODE_CARDS } from "virtual:helene-modes";
import { copyText } from "../../../../ui-kit/dom";
import { FormScene } from "./base";
import { button, el } from "./form";
import { cancelInstall, isMac, machine, openFrame, runInstall, setup, type Progress, type Receipt } from "../setup";
import { PRODUCT_NAME, isPraxis } from "../config";

/** Слова фаз на листе — короче, чем в журнале. */
const PHASE_WORDS: Record<string, string> = {
  check: "Проверка установщика",
  backup: "Снимок памяти агента",
  lay: "Новая версия ложится рядом",
  rehearse: "Проверка расширений",
  stop: "Остановка прежней версии",
  swap: "Подмена — одним движением",
  configure: "Настройки и конституция",
  register: "Ярлыки и «Приложения»",
  service: "Служба Windows",
  done: "Готово",
};

type RowState = "pending" | "active" | "done" | "failed" | "cancelled";

/** Порядок фаз — один и тот же у мастера и листа. Непредусмотренная фаза (снимок у
 *  памяти без программы, репетиция расширений) встаёт на СВОЁ место, а не в конец:
 *  живая проба 27.09 — «Снимок памяти» пришёл первым, встал последним, и всё «до
 *  него» лист пометил сделанным. */
const ORDER = ["check", "backup", "lay", "rehearse", "stop", "swap", "configure", "register", "service", "done"];

function rank(phase: string): number {
  const i = ORDER.indexOf(phase);
  return i < 0 ? ORDER.length - 1 : i;
}

export class InstallScene extends FormScene {
  private head: HTMLElement;
  private summary: HTMLElement;
  private actions: HTMLElement;
  private sheet: HTMLElement;
  private sheetFoot: HTMLElement;
  private cancelBtn: HTMLButtonElement;
  private result: HTMLElement;
  private rows = new Map<string, { li: HTMLElement; detail: HTMLElement; bar: HTMLElement | null }>();
  private current = "";
  private started = false;
  private receipt: Receipt | null = null;

  constructor(root: HTMLElement) {
    super(root);
    this.head = el("h2", "form-head");
    this.head.append(el("span", "line", "Готово к установке"));
    this.summary = el("dl", "summary");
    this.actions = el("div", "install-actions");
    this.actions.append(button("Установить", "primary", () => void this.run()));
    this.sheet = el("ol", "sheet");
    this.sheet.hidden = true;
    this.sheetFoot = el("div", "sheet-foot");
    this.sheetFoot.hidden = true;
    this.cancelBtn = button("Отмена", "quiet", () => void this.cancel());
    this.sheetFoot.append(this.cancelBtn, el("span", "sheet-why", ""));
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.mount(this.head, this.summary, this.actions, this.sheet, this.sheetFoot, this.result);
  }

  get locked(): boolean {
    return this.started;
  }

  /** Запустить установку без кнопки — «Обновить» со сцены «уже установлена». */
  start() {
    void this.run();
  }

  private row(term: string, value: string) {
    const dt = el("dt", "", term);
    const dd = el("dd", "", value);
    this.summary.append(dt, dd);
  }

  protected beforeEnter() {
    if (this.started) return;
    this.summary.replaceChildren();
    if (isPraxis()) {
      this.row("Что", "окно к агенту на твоём сервере — сам агент живёт там");
      this.row(
        "Подключение",
        machine.installed
          ? "адрес сервера и ключ канала останутся как были"
          : "адрес сервера и ключ канала окно спросит при первом запуске",
      );
      const scope = machine.installedFound?.scope || setup.scope;
      this.row("Для кого", scope === "machine" ? "для всех на этом компьютере" : "только для меня");
      this.row("Папка", setup.dir);
      return;
    }
    // Явный switch, а не цепочка тернарников: провайдер anthropic проваливался
    // в ветку local, и перед самой кнопкой «Установить» владельцу показывали
    // чужой адрес (Ollama) и пустое имя модели.
    let model: string;
    switch (setup.provider) {
      case "api":
        model = `${setup.api.model} · ${setup.api.base_url}`;
        break;
      case "anthropic":
        model = `${setup.anthropic.model} · ${setup.anthropic.base_url}`;
        break;
      case "chatgpt":
        model = `подписка ChatGPT через встроенное реле · ${setup.chatgpt_model}`;
        break;
      case "local":
        model = `${setup.local.model} · ${setup.local.base_url}`;
        break;
    }
    const inst = machine.installed;
    if (inst) {
      const ver = inst.version ? `версия ${inst.version}` : "прежняя установка";
      this.row(
        "Уже установлено",
        `${ver}${inst.agent ? `, агент ${inst.agent}` : ""} — это обновление: перед ним снимется копия памяти, ` +
          "новая версия ляжет рядом и встанет на место одним движением; память, конституция и настройки останутся",
      );
    } else if (setup.carry_from) {
      const same = setup.carry_from.replace(/[\\/]+$/, "").toLowerCase() === setup.dir.replace(/[\\/]+$/, "").toLowerCase();
      this.row(
        "Память",
        same
          ? "остаётся на месте — программа встанет рядом с ней; перед этим снимется её копия"
          : `копия из ${setup.carry_from} — сама находка не тронется`,
      );
    }
    this.row("Агент", setup.agent.trim());
    this.row("Владелец", setup.owner.trim());
    this.row("Модель", model);
    this.row("Telegram", setup.telegram.bot_token.trim() ? "бот подключён" : "только окно");
    // Два вопроса — две строки сводки, ровно как на экране выбора. Одной
    // строкой их писать нельзя: из склейки ограды со службой и вырос P0
    // (см. шапку scenes/mode.ts).
    const picked = MODE_CARDS.find((m) => m.name === setup.agent_mode);
    this.row("Ограда", picked?.title ?? setup.agent_mode);
    this.row(
      isMac() ? "Служба" : "Служба Windows",
      setup.service
        ? (isMac()
            ? "поставить — система спросит пароль администратора"
            : "поставить — Windows спросит права администратора") +
          (!isMac() && setup.session0 ? "; нулевая сессия РАЗРЕШЕНА" : "")
        : "не ставить — программа живёт из окна",
    );
    this.row(
      "Управление компьютером",
      setup.computer
        ? "включить — агент получит окна, экран, клавиатуру и мышь; сузить права можно в настройках"
        : "не включать — включается потом, в настройках",
    );
    if (!isMac()) {
      const scope = machine.installedFound?.scope || setup.scope;
      this.row(
        "Для кого",
        scope === "machine"
          ? "для всех на этом компьютере" + (machine.elevated ? "" : " — Windows один раз спросит права администратора")
          : "только для меня",
      );
    }
    this.row("Папка", setup.dir);
  }

  /** Лист фаз — целиком с первой секунды: человек видит весь путь, а не ждёт строку. */
  private buildSheet() {
    this.rows.clear();
    this.sheet.replaceChildren();
    const update = !!machine.installed || !!machine.installedFound || !!setup.carry_from;
    const phases = ["check", ...(update && !isPraxis() ? ["backup"] : []), "lay", ...(update ? ["stop"] : []), "swap", "configure"];
    if (!isMac()) phases.push("register");
    if (setup.service) phases.push("service");
    phases.push("done");
    for (const p of phases) this.addRow(p);
  }

  private addRow(phase: string, label?: string) {
    if (this.rows.has(phase)) return;
    const li = el("li", "sheet-row");
    li.dataset.state = "pending";
    const dot = el("span", "sheet-dot");
    const own = isPraxis() && phase === "configure" ? "Настройки подключения" : PHASE_WORDS[phase];
    const words = el("span", "sheet-label", own || label || phase);
    const detail = el("span", "sheet-detail", "");
    li.append(dot, words, detail);
    let bar: HTMLElement | null = null;
    if (phase === "lay" || phase === "backup") {
      bar = el("span", "sheet-bar");
      bar.append(el("i", ""));
      li.append(bar);
    }
    // На своё место по порядку фаз (см. ORDER), а не в конец листа.
    li.dataset.phase = phase;
    const after = [...this.sheet.children].find((c) => rank((c as HTMLElement).dataset.phase || "") > rank(phase));
    if (after) this.sheet.insertBefore(li, after);
    else this.sheet.append(li);
    this.rows.set(phase, { li, detail, bar });
  }

  private mark(phase: string, state: RowState) {
    const row = this.rows.get(phase);
    if (row) row.li.dataset.state = state;
  }

  private onProgress(p: Progress) {
    const phase = p.phase || `step-${p.step}`;
    if (!this.rows.has(phase)) this.addRow(phase, p.label);
    if (phase !== this.current) {
      // Всё, что по порядку раньше этой фазы, — сделано; не случившиеся (снимка не
      // было — нечего снимать) уходят тише. Порядок — фаз, а не строк на листе.
      for (const [key, row] of this.rows) {
        if (key === phase || rank(key) >= rank(phase)) continue;
        const st = row.li.dataset.state;
        if (st === "pending") row.li.classList.add("skipped");
        if (st === "pending" || st === "active") this.mark(key, "done");
      }
      this.current = phase;
      this.mark(phase, phase === "done" ? "done" : "active");
    }
    const row = this.rows.get(phase)!;
    if (p.detail) row.detail.textContent = p.detail;
    if (row.bar && typeof p.frac === "number") {
      (row.bar.firstElementChild as HTMLElement).style.width = `${Math.round(p.frac * 1000) / 10}%`;
    }
    const can = !!p.cancellable && phase !== "done";
    this.cancelBtn.disabled = !can;
    const why = this.sheetFoot.querySelector<HTMLElement>(".sheet-why")!;
    const had = !!machine.installed || !!machine.installedFound;
    why.textContent = can
      ? had
        ? "Отмена вернёт всё как было: прежняя версия цела, пока новая ложится рядом."
        : "Отмена уберёт всё разложенное: на диске ничего не останется."
      : phase === "done"
        ? ""
        : had
          ? "Сейчас отменить нельзя — это секунды, и прежняя версия уже уступила место."
          : "Сейчас отменить нельзя — это секунды: программа уже встала на место.";
  }

  private async cancel() {
    this.cancelBtn.disabled = true;
    this.cancelBtn.textContent = "Отменяю…";
    await cancelInstall();
  }

  private async run() {
    if (this.started) return;
    this.started = true;
    this.actions.hidden = true;
    this.summary.hidden = true;
    this.result.hidden = true;
    this.head.replaceChildren(el("span", "line", machine.installed ? "Обновляю" : "Ставлю"));
    this.buildSheet();
    this.sheet.hidden = false;
    this.sheetFoot.hidden = false;
    this.cancelBtn.textContent = "Отмена";
    this.cancelBtn.disabled = false;
    this.current = "";
    try {
      this.receipt = await runInstall((p) => this.onProgress(p));
      this.mark(this.current, "done");
      this.sheetFoot.hidden = true;
      this.showResult(this.receipt);
    } catch (err) {
      const text = String(err);
      this.sheetFoot.hidden = true;
      if (text.startsWith("Отменено")) {
        if (this.current) {
          this.mark(this.current, "cancelled");
          const row = this.rows.get(this.current);
          if (row) row.detail.textContent = "отменено — убрано";
        }
        this.showCancelled(text);
      } else {
        if (this.current) this.mark(this.current, "failed");
        this.showFailure(text);
      }
    }
  }

  /** Отмена — спокойный итог, не авария. */
  private showCancelled(text: string) {
    this.head.replaceChildren(el("span", "line", "Отменено"));
    this.result.hidden = false;
    this.result.replaceChildren();
    const lines = el("p", "", machine.installed ? text : "Ничего не установлено: новая версия убрана, на диске её не осталось.");
    const again = button("Начать снова", "primary", () => this.reset());
    this.result.append(lines, again);
  }

  private showFailure(text: string) {
    this.head.replaceChildren(el("span", "line", "Не вышло"));
    this.result.hidden = false;
    this.result.replaceChildren();
    const p = el("p", "err", text);
    // Отчёт живёт не только на экране: окно закроют — текст исчезнет навсегда.
    // `copyText`, а не голый `navigator.clipboard`: на macOS страница живёт на
    // helene://localhost без secure context (ui-kit/dom.ts).
    const copy = button("Скопировать отчёт", "quiet", () => {
      void copyText(text).then((ok) => {
        copy.textContent = ok ? "Скопировано" : "Не скопировалось — выдели текст мышью";
      });
    });
    const again = button("Повторить", "primary", () => {
      this.reset();
      void this.run();
    });
    const actions = el("div", "install-actions");
    actions.append(again, copy);
    this.result.append(p, actions);
  }

  private reset() {
    this.started = false;
    this.sheet.hidden = true;
    this.sheetFoot.hidden = true;
    this.result.hidden = true;
    this.summary.hidden = false;
    this.actions.hidden = false;
    this.head.replaceChildren(el("span", "line", "Готово к установке"));
    this.beforeEnter();
  }

  private showResult(r: Receipt) {
    this.head.replaceChildren(el("span", "line", machine.installed ? "Обновлено" : isPraxis() ? "Установлен" : "Установлено"));
    this.result.hidden = false;
    this.result.replaceChildren();
    // «absent» приходил и когда службы нет в поставке, и когда владелец отказал
    // в правах: людям говорили про UAC, которого они не видели.
    const service =
      r.service === "running"
        ? "Служба работает."
        : r.service === "stopped"
          ? "Служба поставлена, но не запустилась: подробности в журнале службы."
          : r.service === "absent"
            ? "Служба не поставилась: права администратора не были даны."
            : r.service === "missing"
              ? `Служба не установлена: в этой сборке нет ${isMac() ? "helene-svc" : "helene-svc.exe"}.`
              : r.service.startsWith("failed: ")
                ? `Служба не поставилась: ${r.service.slice(8)}`
                : "";
    const lines = el(
      "p",
      "",
      isPraxis() ? `Praxis стоит в ${r.dir}. При первом запуске окно спросит адрес сервера и ключ канала, если их ещё нет.` : `${setup.agent.trim() || "Агент"} живёт в ${r.dir}. ${service}`.trim(),
    );
    this.result.append(lines);
    if (r.backup) this.result.append(el("p", "muted", `Копия памяти до обновления: ${r.backup}`));
    // Предупреждение службы печатаем ТЕКСТОМ здесь: сама служба сказать этого
    // не может — её ставят скрытым поднятым процессом.
    if (r.warning) this.result.append(el("p", "err", r.warning));
    const fails = r.steps.filter((s) => !s.ok);
    if (fails.length) {
      const list = el("ul", "result-notes");
      for (const s of fails) list.append(el("li", "", `${s.label}: ${s.note ?? "не вышло"}`));
      this.result.append(list);
    }
    // Промис нельзя терять: если helene.exe не стартует, кнопка «Открыть»
    // молча не делала ничего — ни окна, ни сообщения.
    const open = button(`Открыть ${PRODUCT_NAME}`, "primary", () => {
      open.disabled = true;
      open.textContent = setup.service ? "Жду службу…" : "Открываю…";
      openFrame(r.exe).catch((err) => {
        const how = isMac() ? "открой Helene.app оттуда" : "открой ярлык на рабочем столе";
        this.result.append(el("p", "err", `Не удалось открыть Hélène: ${err}. Она установлена в ${r.dir} — ${how}.`));
      });
    });
    this.result.append(open);
  }
}
