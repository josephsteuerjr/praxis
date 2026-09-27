// Сцена «Снять»: нейтральная, продукт, не агент. Выбор судьбы данных — явный,
// снятие начинается только нажатием кнопки; закрыть окно — тоже кнопкой.
import { getCurrentWindow } from "@tauri-apps/api/window";
import { FormScene } from "./base";
import { button, choice, el, explain } from "./form";
import { PRODUCT_NAME, isPraxis } from "../config";
import { isMac, runUninstall, type Progress } from "../setup";

/** Фазы снятия на листе (1.2): быстрые, но видимые — полоса больше не «стоит». */
const PHASE_WORDS: Record<string, string> = {
  service: "Служба",
  stop: "Программа и её процессы",
  fence: "Ограда песочницы",
  files: "Файлы программы",
  done: "Готово",
};

/** Что будет удалено. На macOS ни службы, ни ярлыков, ни записи в
 *  «Приложениях» нет — есть бандл и его автозапуск (LaunchAgent). */
const LEAD = "Программа, служба, ярлыки и запись в «Приложениях» будут удалены. Что делать с данными агента — решать тебе.";
const LEAD_MACOS = "Программа, служба и её автозапуск будут удалены. Что делать с данными агента — решать тебе.";

// ⚠ 17.09. Пояснение ВРАЛО при выбранном «Удалить всё»: обещало, что следующая
// установка подхватит агента с его памятью и что рядом будет записка КАК-ВЕРНУТЬСЯ.md.
// При удалении не будет ни того, ни другого — а стояло оно выше и крупнее выбора.
const KEEP = "Если данные оставить, следующая установка в ту же папку подхватит агента "
  + "с его памятью. В той же папке останутся секреты: ключ модели, вход в аккаунт "
  + "ChatGPT и сессия твоего Telegram. Удалить их можно и позже, убрав папку целиком; "
  + "рядом с ней будет записка КАК-ВЕРНУТЬСЯ.md.";
/** Команда экспорта — своя на каждой системе: на Mac установщик лежит внутри бандла. */
function purgeText(): string {
  const cmd = isMac() ? "`\"Helene Setup.app/Contents/MacOS/helene-setup\" --export`" : "`helene-setup.exe --export`";
  // ⚠ 27.09 (1.2): копии памяти лежат в backups рядом с программой и уходят вместе с ней —
  // без этой оговорки владелец рассчитывал бы на копии, которых после снятия не будет.
  return "Папка data исчезнет вместе с программой: память, дневник, конституция и "
    + "всё, что агент написал о себе и о людях, — безвозвратно, без корзины; копии "
    + "памяти в backups рядом с программой — тоже. Записки "
    + "КАК-ВЕРНУТЬСЯ.md не будет: возвращаться будет некуда. Если хочешь забрать память "
    + "с собой, закрой это окно и выполни рядом с программой "
    + cmd + " — он соберёт архив переноса, и только потом снимай.";
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
    head.append(el("span", "line", `Снять ${PRODUCT_NAME}`));
    const lead = el("p", "form-lead", LEAD);
    this.lead = lead;
    // ⚠ Кнопку и пояснение объявляем ДО choice: его onChange замыкается на них, и при
    // обратном порядке замыкание поймало бы undefined.
    this.actions = el("div", "install-actions");
    const act = button("Снять", "primary", () => void this.run());
    this.actions.append(act);
    const why = explain("Что останется", KEEP);
    const whyText = why.querySelector("p");
    this.whyText = whyText;
    const pick = choice<"keep" | "purge">({
      value: "keep",
      items: [
        { value: "keep", title: "Оставить данные", text: "память, конституция, ключ модели, вход в Telegram и ChatGPT остаются в папке data" },
        { value: "purge", title: "Удалить всё", text: "папка данных исчезает вместе с программой, восстановить будет нечего" },
      ],
      onChange: (v) => {
        this.purge = v === "purge";
        // Кнопка обязана называть то, что делает: между серой карточкой и безвозвратным
        // сносом памяти стояла кнопка с неизменной подписью «Снять».
        act.textContent = this.purge ? "Снять и стереть данные" : "Снять";
        act.classList.toggle("btn-danger", this.purge);
        act.classList.toggle("btn-primary", !this.purge);
        if (whyText) whyText.textContent = this.purge ? purgeText() : KEEP;
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
      ["Оставить настройки", "адрес сервера и ключ канала останутся в папке — следующая установка подхватит их сама"],
      ["Удалить всё", "вместе с программой уйдут и настройки подключения"],
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
        ? "Уйдут и адрес сервера, и ключ канала: при следующей установке окно спросит их заново."
        : "В папке останется helene.json — адрес сервера и ключ канала. Ключ — секрет: если отдаёшь компьютер, удали папку целиком.";
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
      ? "Программа, ярлыки и запись в «Приложениях» будут удалены. Настройки подключения — решать тебе."
      : isMac() ? LEAD_MACOS : LEAD;
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
      text = "Снятие не удалось: " + String(e);
      failed = true;
    }
    // Заголовок раньше сверялся со строкой, которой снятие не возвращает никогда,
    // — «Снято» стояло и над «служба осталась», и над «не удалось удалить».
    const partial = /ВНИМАНИЕ|Не всё получилось/.test(text);
    const title = el("h3", "", failed ? "Не вышло" : partial ? "Снято не до конца" : "Снято");
    const note = el("p", failed ? "err" : partial ? "warn" : "muted", text);
    const close = button("Закрыть", "primary", () => void getCurrentWindow().close());
    this.result.hidden = false;
    this.result.replaceChildren(title, note, close);
    this.reveal(this.result);
  }
}
