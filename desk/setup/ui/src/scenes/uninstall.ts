import { t as uiText } from "../../../../lang/lang";
// Сцена «Снять»: нейтральная, продукт, не агент. Выбор судьбы данных — явный,
// снятие начинается только нажатием кнопки; закрыть окно — тоже кнопкой.
import { getCurrentWindow } from "@tauri-apps/api/window";
import { FormScene } from "./base";
import { button, choice, el, explain } from "./form";
import { PRODUCT_NAME, isPraxis } from "../config";
import { isMac, runUninstall, type Progress } from "../setup";

/** Фазы снятия на листе (1.2): быстрые, но видимые — полоса больше не «стоит». */
const PHASE_WORDS: Record<string, string> = {
  get service() { return uiText("ui.setup.ui.src.scenes.uninstall.dc43cb0ba6"); },
  get stop() { return uiText("ui.setup.ui.src.scenes.uninstall.caae8e14db"); },
  get fence() { return uiText("ui.setup.ui.src.scenes.uninstall.3fd413fa0c"); },
  get files() { return uiText("ui.setup.ui.src.scenes.uninstall.00c5fc8590"); },
  get done() { return uiText("ui.setup.ui.src.scenes.uninstall.ef05d57959"); },
};

/** Что будет удалено. На macOS ни службы, ни ярлыков, ни записи в
 *  «Приложениях» нет — есть бандл и его автозапуск (LaunchAgent). */
const LEAD = () => uiText("ui.setup.ui.src.scenes.uninstall.43bd2de8a5");
const LEAD_MACOS = () => uiText("ui.setup.ui.src.scenes.uninstall.73713aaa56");

// ⚠ 17.09. Пояснение ВРАЛО при выбранном «Удалить всё»: обещало, что следующая
// установка подхватит агента с его памятью и что рядом будет записка КАК-ВЕРНУТЬСЯ.md.
// При удалении не будет ни того, ни другого — а стояло оно выше и крупнее выбора.
const KEEP = () => uiText("ui.setup.ui.src.scenes.uninstall.26e302f4ff");
/** Команда экспорта — своя на каждой системе: на Mac установщик лежит внутри бандла. */
function purgeText(): string {
  const cmd = isMac() ? "`\"Helene Setup.app/Contents/MacOS/helene-setup\" --export`" : "`helene-setup.exe --export`";
  // ⚠ 27.09 (1.2): копии памяти лежат в backups рядом с программой и уходят вместе с ней —
  // без этой оговорки владелец рассчитывал бы на копии, которых после снятия не будет.
  return uiText("ui.setup.ui.src.scenes.uninstall.7df56e42b9")
    + cmd + uiText("ui.setup.ui.src.scenes.uninstall.fb68c058d0");
}

export class UninstallScene extends FormScene {
  private started = false;
  private purge = false;
  private actions: HTMLElement;
  private result: HTMLElement;
  private lead: HTMLElement;
  private whyText: HTMLParagraphElement | null;
  private sheet: HTMLElement;
  /** Какую установку снимаем (1.2): папка из находок; пусто — папка самого мастера. */
  private dir = "";

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.uninstall.77b38f883b", {p0: String(PRODUCT_NAME)})));
    const lead = el("p", "form-lead", LEAD());
    this.lead = lead;
    // ⚠ Кнопку и пояснение объявляем ДО choice: его onChange замыкается на них, и при
    // обратном порядке замыкание поймало бы undefined.
    this.actions = el("div", "install-actions");
    const act = button(uiText("ui.setup.ui.src.scenes.uninstall.d4b51f7a04"), "primary", () => void this.run());
    this.actions.append(act);
    const why = explain(uiText("ui.setup.ui.src.scenes.uninstall.39db928b61"), KEEP());
    const whyText = why.querySelector("p");
    this.whyText = whyText;
    const pick = choice<"keep" | "purge">({
      value: "keep",
      items: [
        { value: "keep", title: uiText("ui.setup.ui.src.scenes.uninstall.54b158df02"), text: uiText("ui.setup.ui.src.scenes.uninstall.b624c77b73") },
        { value: "purge", title: uiText("ui.setup.ui.src.scenes.uninstall.d760b4bacf"), text: uiText("ui.setup.ui.src.scenes.uninstall.204ee08b50") },
      ],
      onChange: (v) => {
        this.purge = v === "purge";
        // Кнопка обязана называть то, что делает: между серой карточкой и безвозвратным
        // сносом памяти стояла кнопка с неизменной подписью «Снять».
        act.textContent = this.purge ? uiText("ui.setup.ui.src.scenes.uninstall.74a93b2d04") : uiText("ui.setup.ui.src.scenes.uninstall.d4b51f7a04");
        act.classList.toggle("btn-danger", this.purge);
        act.classList.toggle("btn-primary", !this.purge);
        if (whyText) whyText.textContent = this.purge ? purgeText() : KEEP();
        if (isPraxis()) this.praxisWords();
        why.classList.toggle("explain-danger", this.purge);
      },
    });
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.sheet = el("ol", "sheet");
    this.sheet.hidden = true;
    this.mount(head, lead, pick, why, this.actions, this.sheet, this.result);
  }

  /** Praxis: не «данные агента» (их здесь нет), а настройки подключения. */
  private praxisWords() {
    const items = this.root.querySelectorAll<HTMLElement>(".choice-item");
    const texts = [
      [uiText("ui.setup.ui.src.scenes.uninstall.3c5fc5f711"), uiText("ui.setup.ui.src.scenes.uninstall.4d2427676e")],
      [uiText("ui.setup.ui.src.scenes.uninstall.d760b4bacf"), uiText("ui.setup.ui.src.scenes.uninstall.b2ecc23d18")],
    ];
    items.forEach((item, i) => {
      const t = texts[i];
      if (!t) return;
      const title = item.querySelector(".choice-title");
      const text = item.querySelector(".choice-text");
      if (title) title.textContent = t[0];
      if (text) text.textContent = t[1];
    });
    if (this.whyText) {
      this.whyText.textContent = this.purge
        ? uiText("ui.setup.ui.src.scenes.uninstall.9315d8b1e9")
        : uiText("ui.setup.ui.src.scenes.uninstall.e3602f960d");
    }
  }

  /** Снимать не папку мастера, а эту (кнопка «Удалить» нового установщика). */
  setDir(dir: string) {
    this.dir = dir;
  }

  private onProgress(p: Progress) {
    const phase = p.phase || "";
    let found = false;
    for (const li of this.sheet.querySelectorAll<HTMLElement>(".sheet-row")) {
      if (li.dataset.phase === phase) {
        li.dataset.state = phase === "done" ? "done" : "active";
        found = true;
      } else if (!found) {
        li.dataset.state = "done";
      }
    }
  }

  /** Слова по системе — здесь, а не в конструкторе: `platform` приезжает
   *  ответом `defaults` уже после того, как сцены построены. */
  protected beforeEnter() {
    this.lead.textContent = isPraxis()
      ? uiText("ui.setup.ui.src.scenes.uninstall.6d890a98a8")
      : isMac() ? LEAD_MACOS() : LEAD();
    if (isPraxis()) this.praxisWords();
    if (this.whyText && this.purge) this.whyText.textContent = purgeText();
  }

  get locked(): boolean {
    return this.started;
  }

  private async run() {
    if (this.started) return;
    this.started = true;
    this.actions.hidden = true;
    this.sheet.hidden = false;
    this.sheet.replaceChildren();
    for (const phase of ["service", "stop", "fence", "files", "done"]) {
      const li = el("li", "sheet-row");
      li.dataset.phase = phase;
      li.dataset.state = "pending";
      li.append(el("span", "sheet-dot"), el("span", "sheet-label", PHASE_WORDS[phase]), el("span", "sheet-detail", ""));
      this.sheet.append(li);
    }
    let text: string;
    let failed = false;
    try {
      text = await runUninstall(this.purge, this.dir, (p) => this.onProgress(p));
    } catch (e) {
      text = uiText("ui.setup.ui.src.scenes.uninstall.7709559c8b") + String(e);
      failed = true;
    }
    // Заголовок раньше сверялся со строкой, которой снятие не возвращает никогда,
    // — «Снято» стояло и над «служба осталась», и над «не удалось удалить».
    const partial = /ВНИМАНИЕ|Не всё получилось/.test(text);
    const title = el("h3", "", failed ? uiText("ui.setup.ui.src.scenes.uninstall.2f7d0e9398") : partial ? uiText("ui.setup.ui.src.scenes.uninstall.573bfebf2c") : uiText("ui.setup.ui.src.scenes.uninstall.23fd88551b"));
    const note = el("p", failed ? "err" : partial ? "warn" : "muted", text);
    const close = button(uiText("ui.setup.ui.src.scenes.uninstall.a7a4033657"), "primary", () => void getCurrentWindow().close());
    this.result.hidden = false;
    this.result.replaceChildren(title, note, close);
    this.reveal(this.result);
  }
}
