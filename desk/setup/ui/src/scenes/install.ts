import { t as uiText } from "../../../../lang/lang";
// Сцена «Установить»: сводка решений, кнопка, ход установки листом фаз и кнопка
// «Открыть». Установка начинается только нажатием кнопки.
//
// 1.2 (27.09): ход — не список строк, а ЛИСТ фаз, который виден целиком с первой
// секунды: проверка, снимок памяти, раскладка рядом (с живой полосой: файлы и
// мегабайты), остановка прежней, подмена, настройка, ярлыки, служба. «Отмена» —
// на каждом шаге до подмены (требование Егора): новая версия раскладывается рядом,
// прежняя работает, отмена = откат, прежняя цела. Подмена — доли секунды, в это
// время кнопка честно говорит, почему её нельзя нажать.
import { modeOwnedText } from "../../../../ui-kit/window/mode-owned";
import { MODE_CARDS } from "virtual:helene-modes";
import { copyText } from "../../../../ui-kit/dom";
import { FormScene } from "./base";
import { button, el } from "./form";
import { cancelInstall, isMac, machine, openFrame, runInstall, setup, type Progress, type Receipt } from "../setup";
import { PRODUCT_NAME, isPraxis } from "../config";

/** Слова фаз на листе — короче, чем в журнале. */
const PHASE_WORDS: Record<string, string> = {
  get check() { return uiText("ui.setup.ui.src.scenes.install.2e8bc78cec"); },
  get backup() { return uiText("ui.setup.ui.src.scenes.install.99cb80c14e"); },
  get lay() { return uiText("ui.setup.ui.src.scenes.install.2d3b6086e6"); },
  get rehearse() { return uiText("ui.setup.ui.src.scenes.install.a1ece660fc"); },
  get stop() { return uiText("ui.setup.ui.src.scenes.install.090bdaabf9"); },
  get swap() { return uiText("ui.setup.ui.src.scenes.install.6a54fe1e09"); },
  get configure() { return uiText("ui.setup.ui.src.scenes.install.f5dc496dbb"); },
  get register() { return uiText("ui.setup.ui.src.scenes.install.2bbb1c4f2f"); },
  get service() { return uiText("ui.setup.ui.src.scenes.install.36a571f31a"); },
  get done() { return uiText("ui.setup.ui.src.scenes.install.ef05d57959"); },
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

export function installCancelled(text: string): boolean {
  // Native legacy errors keep their protocol prefix independently of UI.
  return text.startsWith('Отменено') || text.startsWith(uiText("ui.setup.ui.src.scenes.install.23a2a9bfcf"));
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
  private note: HTMLElement;
  private preparation = false;
  private running = false;

  constructor(root: HTMLElement, private updating = false) {
    super(root);
    this.head = el("h2", "form-head");
    this.head.setAttribute("aria-live", "polite");
    this.head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.install.359260512b")));
    this.summary = el("dl", "summary");
    this.note = el("p", "form-lead", ""); this.note.hidden = true;
    this.actions = el("div", "install-actions");
    this.actions.append(button(uiText("ui.setup.ui.src.scenes.install.b7cd6fae08"), "primary", () => void this.run()));
    this.sheet = el("ol", "sheet");
    this.sheet.hidden = true;
    this.sheetFoot = el("div", "sheet-foot");
    this.sheetFoot.hidden = true;
    this.cancelBtn = button(uiText("ui.setup.ui.src.scenes.install.8fbe9b75cb"), "quiet", () => void this.cancel());
    this.sheetFoot.append(this.cancelBtn, el("span", "sheet-why", ""));
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.mount(this.head, this.note, this.summary, this.actions, this.sheet, this.sheetFoot, this.result);
  }

  get locked(): boolean {
    return this.started;
  }
  get isRunning(): boolean { return this.running; }
  get canCancel(): boolean { return this.running && !this.cancelBtn.disabled; }
  requestCancel() { if (this.canCancel) void this.cancel(); }

  /** Запустить установку без кнопки — «Обновить» со сцены «уже установлена». */
  start() {
    this.preparation = false;
    void this.run();
  }

  prepare(version: string) {
    this.preparation = true;
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.install.4080ca2531")));
    const old = machine.installed?.version;
    this.note.textContent = uiText("ui.setup.ui.src.scenes.install.26d218928c", {p0: String(old ? uiText("ui.setup.ui.src.scenes.install.83c21cec4c") + old : uiText("ui.setup.ui.src.scenes.install.319b48dfe2")), p1: String(version ? " → " + version : "")});
    this.note.hidden = false; this.summary.hidden = true; this.actions.hidden = true;
  }
  preparationFailed(error: string, retry: () => void, back: () => void) {
    this.preparation = false;
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.install.2b5f89a7e3")));
    this.note.textContent = error; this.note.hidden = false;
    this.actions.replaceChildren(button(uiText("ui.setup.ui.src.scenes.install.5189135a61"), "primary", retry), button(uiText("ui.setup.ui.src.scenes.install.6c99e7d8fb"), "quiet", back));
    this.actions.hidden = false;
  }

  private row(term: string, value: string) {
    const dt = el("dt", "", term);
    const dd = el("dd", "", value);
    this.summary.append(dt, dd);
  }

  protected beforeEnter() {
    if (this.updating || this.preparation) return;
    if (this.started) return;
    this.summary.replaceChildren();
    if (isPraxis()) {
      this.row(uiText("ui.setup.ui.src.scenes.install.c1c487fabc"), uiText("ui.setup.ui.src.scenes.install.07098fad90"));
      this.row(
        uiText("ui.setup.ui.src.scenes.install.ab71afb97f"),
        machine.installed
          ? uiText("ui.setup.ui.src.scenes.install.0185d25960")
          : uiText("ui.setup.ui.src.scenes.install.7c86ac777c"),
      );
      const scope = machine.installedFound?.scope || setup.scope;
      this.row(uiText("ui.setup.ui.src.scenes.install.cc2bd4cf1a"), scope === "machine" ? uiText("ui.setup.ui.src.scenes.install.e6fd18f1d2") : uiText("ui.setup.ui.src.scenes.install.8e19c02a27"));
      this.row(uiText("ui.setup.ui.src.scenes.install.73a0f69d54"), setup.dir);
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
        model = uiText("ui.setup.ui.src.scenes.install.90c78206e2", {p0: String(setup.chatgpt_model)});
        break;
      case "local":
        model = `${setup.local.model} · ${setup.local.base_url}`;
        break;
    }
    const inst = machine.installed;
    if (inst) {
      const ver = inst.version ? uiText("ui.setup.ui.src.scenes.install.0295e8b3df", {p0: String(inst.version)}) : uiText("ui.setup.ui.src.scenes.install.a7a8fe885c");
      this.row(
        uiText("ui.setup.ui.src.scenes.install.7ca15e3d2d"),
        uiText("ui.setup.ui.src.scenes.install.390cbcf7be", {p0: String(ver), p1: String(inst.agent ? uiText("ui.setup.ui.src.scenes.install.ac9614b48c", {p0: String(inst.agent)}) : "")}) +
          uiText("ui.setup.ui.src.scenes.install.3c6abb8eff"),
      );
    } else if (setup.carry_from) {
      const same = setup.carry_from.replace(/[\\/]+$/, "").toLowerCase() === setup.dir.replace(/[\\/]+$/, "").toLowerCase();
      this.row(
        uiText("ui.setup.ui.src.scenes.install.3972116405"),
        same
          ? uiText("ui.setup.ui.src.scenes.install.0f48215776")
          : uiText("ui.setup.ui.src.scenes.install.cc28d6e60c", {p0: String(setup.carry_from)}),
      );
    }
    this.row(uiText("ui.setup.ui.src.scenes.install.d5fd08be28"), setup.agent.trim());
    this.row(uiText("ui.setup.ui.src.scenes.install.e26fad2853"), setup.owner.trim());
    this.row(uiText("ui.setup.ui.src.scenes.install.f3bdf7cce3"), model);
    this.row("Telegram", setup.telegram.bot_token.trim() ? uiText("ui.setup.ui.src.scenes.install.c245477077") : uiText("ui.setup.ui.src.scenes.install.b0b81b1661"));
    // Два вопроса — две строки сводки, ровно как на экране выбора. Одной
    // строкой их писать нельзя: из склейки ограды со службой и вырос P0
    // (см. шапку scenes/mode.ts).
    const picked = MODE_CARDS.find((m) => m.name === setup.agent_mode);
    this.row(uiText("ui.setup.ui.src.scenes.install.3c4a44adf1"), modeOwnedText(picked?.title ?? setup.agent_mode));
    this.row(
      isMac() ? uiText("ui.setup.ui.src.scenes.install.dc43cb0ba6") : uiText("ui.setup.ui.src.scenes.install.36a571f31a"),
      setup.service
        ? (isMac()
            ? uiText("ui.setup.ui.src.scenes.install.b57021e8ec")
            : uiText("ui.setup.ui.src.scenes.install.a04d20eab1")) +
          (!isMac() && setup.session0 ? uiText("ui.setup.ui.src.scenes.install.fc6895e567") : "")
        : uiText("ui.setup.ui.src.scenes.install.e76d45a688"),
    );
    this.row(
      uiText("ui.setup.ui.src.scenes.install.d58fe7fd81"),
      setup.computer
        ? uiText("ui.setup.ui.src.scenes.install.1eac8d364a")
        : uiText("ui.setup.ui.src.scenes.install.e8a880c4ec"),
    );
    if (!isMac()) {
      const scope = machine.installedFound?.scope || setup.scope;
      this.row(
        uiText("ui.setup.ui.src.scenes.install.cc2bd4cf1a"),
        scope === "machine"
          ? uiText("ui.setup.ui.src.scenes.install.e6fd18f1d2") + (machine.elevated ? "" : uiText("ui.setup.ui.src.scenes.install.42d65f070f"))
          : uiText("ui.setup.ui.src.scenes.install.8e19c02a27"),
      );
    }
    this.row(uiText("ui.setup.ui.src.scenes.install.73a0f69d54"), setup.dir);
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
    const own = this.updating && phase === "configure" ? uiText("ui.setup.ui.src.scenes.install.7d976e55cf") : isPraxis() && phase === "configure" ? uiText("ui.setup.ui.src.scenes.install.f1e682238b") : PHASE_WORDS[phase];
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
        ? uiText("ui.setup.ui.src.scenes.install.c10b5caa28")
        : uiText("ui.setup.ui.src.scenes.install.1042330345")
      : phase === "done"
        ? ""
        : had
          ? uiText("ui.setup.ui.src.scenes.install.9da9fbfa59")
          : uiText("ui.setup.ui.src.scenes.install.86e30fae03");
  }

  private async cancel() {
    this.cancelBtn.disabled = true;
    this.cancelBtn.textContent = uiText("ui.setup.ui.src.scenes.install.1ec84de7b9");
    try { await cancelInstall(); }
    catch (e) { this.cancelBtn.disabled = false; this.cancelBtn.textContent = uiText("ui.setup.ui.src.scenes.install.8fbe9b75cb"); this.sheetFoot.querySelector<HTMLElement>(".sheet-why")!.textContent = uiText("ui.setup.ui.src.scenes.install.947b008ac9") + String(e); }
  }

  private async run() {
    if (this.started) return;
    this.started = true;
    this.running = true;
    this.actions.hidden = true;
    this.summary.hidden = true;
    this.result.hidden = true;
    this.head.replaceChildren(el("span", "line", machine.installed ? uiText("ui.setup.ui.src.scenes.install.6ab055b355") : uiText("ui.setup.ui.src.scenes.install.bb45594902")));
    if (this.updating) { this.note.hidden = false; this.note.textContent = uiText("ui.setup.ui.src.scenes.install.84f638c3e3"); }
    this.buildSheet();
    this.sheet.hidden = false;
    this.sheetFoot.hidden = false;
    this.cancelBtn.textContent = uiText("ui.setup.ui.src.scenes.install.8fbe9b75cb");
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
      if (installCancelled(text)) {
        if (this.current) {
          this.mark(this.current, "cancelled");
          const row = this.rows.get(this.current);
          if (row) row.detail.textContent = uiText("ui.setup.ui.src.scenes.install.4af1ae1bc5");
        }
        this.showCancelled(text);
      } else {
        if (this.current) this.mark(this.current, "failed");
        this.showFailure(text);
      }
    } finally { this.running = false; }
  }

  /** Отмена — спокойный итог, не авария. */
  private showCancelled(text: string) {
    this.note.hidden = true;
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.install.23a2a9bfcf")));
    this.result.hidden = false;
    this.result.replaceChildren();
    const lines = el("p", "", machine.installed ? text : uiText("ui.setup.ui.src.scenes.install.2f5d17f78d"));
    const again = button(this.updating ? uiText("ui.setup.ui.src.scenes.install.976e03d39c") : uiText("ui.setup.ui.src.scenes.install.9c227fe8a7"), "primary", () => { this.reset(); if (this.updating) void this.run(); });
    this.result.append(lines, again);
    this.reveal(this.result);
    again.focus({ preventScroll: true });
  }

  private showFailure(text: string) {
    this.note.hidden = true;
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.install.2f7d0e9398")));
    this.result.hidden = false;
    this.result.replaceChildren();
    const p = el("p", "err", text);
    // Отчёт живёт не только на экране: окно закроют — текст исчезнет навсегда.
    // `copyText`, а не голый `navigator.clipboard`: на macOS страница живёт на
    // helene://localhost без secure context (ui-kit/dom.ts).
    const copy = button(uiText("ui.setup.ui.src.scenes.install.8ec2c58498"), "quiet", () => {
      void copyText(text).then((ok) => {
        copy.textContent = ok ? uiText("ui.setup.ui.src.scenes.install.d2d08c715e") : uiText("ui.setup.ui.src.scenes.install.80053e1245");
      });
    });
    const again = button(uiText("ui.setup.ui.src.scenes.install.5189135a61"), "primary", () => {
      this.reset();
      void this.run();
    });
    const actions = el("div", "install-actions");
    actions.append(again, copy);
    this.result.append(p, actions);
    this.reveal(this.result);
    again.focus({ preventScroll: true });
  }

  private reset() {
    this.started = false;
    this.sheet.hidden = true;
    this.sheetFoot.hidden = true;
    this.result.hidden = true;
    this.summary.hidden = this.updating;
    this.actions.hidden = this.updating;
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.install.359260512b")));
    this.beforeEnter();
  }

  private showResult(r: Receipt) {
    this.head.replaceChildren(el("span", "line", machine.installed ? uiText("ui.setup.ui.src.scenes.install.798343da0b") : isPraxis() ? uiText("ui.setup.ui.src.scenes.install.0a7f05b43a") : uiText("ui.setup.ui.src.scenes.install.0b29a3640f")));
    this.result.hidden = false;
    this.result.replaceChildren();
    // «absent» приходил и когда службы нет в поставке, и когда владелец отказал
    // в правах: людям говорили про UAC, которого они не видели.
    const service =
      r.service === "running"
        ? uiText("ui.setup.ui.src.scenes.install.4c4129d446")
        : r.service === "stopped"
          ? uiText("ui.setup.ui.src.scenes.install.8804e1dbe5")
          : r.service === "absent"
            ? uiText("ui.setup.ui.src.scenes.install.5e294efe19")
            : r.service === "missing"
              ? uiText("ui.setup.ui.src.scenes.install.97bbbd635a", {p0: String(isMac() ? "helene-svc" : "helene-svc.exe")})
              : r.service.startsWith("failed: ")
                ? uiText("ui.setup.ui.src.scenes.install.997fb76aa5", {p0: String(r.service.slice(8))})
                : "";
    const lines = el(
      "p",
      "",
      isPraxis() ? uiText("ui.setup.ui.src.scenes.install.73d1f487d6", {p0: String(r.dir)}) : uiText("ui.setup.ui.src.scenes.install.b78d9adc3d", {p0: String(setup.agent.trim() || uiText("ui.setup.ui.src.scenes.install.d5fd08be28")), p1: String(r.dir), p2: String(service)}).trim(),
    );
    this.result.append(lines);
    if (this.updating) { this.note.hidden = true; this.result.append(el("p", "", uiText("ui.setup.ui.src.scenes.install.d5a146bc67"))); }
    if (r.backup) this.result.append(el("p", "muted", uiText("ui.setup.ui.src.scenes.install.11166bb8cb", {p0: String(r.backup)})));
    // Предупреждение службы печатаем ТЕКСТОМ здесь: сама служба сказать этого
    // не может — её ставят скрытым поднятым процессом.
    if (r.warning) this.result.append(el("p", "err", r.warning));
    const fails = r.steps.filter((s) => !s.ok);
    if (fails.length) {
      const list = el("ul", "result-notes");
      for (const s of fails) list.append(el("li", "", `${s.label}: ${s.note ?? uiText("ui.setup.ui.src.scenes.install.8735f485c3")}`));
      this.result.append(list);
    }
    // Промис нельзя терять: если helene.exe не стартует, кнопка «Открыть»
    // молча не делала ничего — ни окна, ни сообщения.
    const open = button(uiText("ui.setup.ui.src.scenes.install.2fd1a2e652", {p0: String(PRODUCT_NAME)}), "primary", () => {
      open.disabled = true;
      open.textContent = setup.service ? uiText("ui.setup.ui.src.scenes.install.ad342db83f") : uiText("ui.setup.ui.src.scenes.install.8cee042d13");
      openFrame(r.exe).catch((err) => {
        const how = isMac() ? uiText("ui.setup.ui.src.scenes.install.77ed9e9bbb") : uiText("ui.setup.ui.src.scenes.install.bb3eac27d2");
        this.result.append(el("p", "err", uiText("ui.setup.ui.src.scenes.install.e939683ffb", {p0: String(err), p1: String(r.dir), p2: String(how)})));
      });
    });
    this.result.append(open);
    this.reveal(this.result);
    open.focus({ preventScroll: true });
  }
}
