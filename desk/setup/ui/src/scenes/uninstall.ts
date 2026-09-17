// Сцена «Снять»: нейтральная, продукт, не агент. Выбор судьбы данных — явный,
// снятие начинается только нажатием кнопки; закрыть окно — тоже кнопкой.
import { getCurrentWindow } from "@tauri-apps/api/window";
import { FormScene } from "./base";
import { button, choice, el, explain } from "./form";
import { PRODUCT_NAME } from "../config";
import { runUninstall } from "../setup";

export class UninstallScene extends FormScene {
  private started = false;
  private purge = false;
  private actions: HTMLElement;
  private result: HTMLElement;

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", `Снять ${PRODUCT_NAME}`));
    const lead = el("p", "form-lead",
      "Программа, служба, ярлыки и запись в «Приложениях» будут удалены. Что делать с данными агента — решать тебе.");
    // ⚠ Кнопку и пояснение объявляем ДО choice: его onChange замыкается на них, и при
    // обратном порядке замыкание поймало бы undefined.
    this.actions = el("div", "install-actions");
    const act = button("Снять", "primary", () => void this.run());
    this.actions.append(act);
    // ⚠ 17.09. Пояснение ВРАЛО при выбранном «Удалить всё»: обещало, что следующая
    // установка подхватит агента с его памятью и что рядом будет записка КАК-ВЕРНУТЬСЯ.md.
    // При удалении не будет ни того, ни другого — а стояло оно выше и крупнее выбора.
    const KEEP = "Если данные оставить, следующая установка в ту же папку подхватит агента "
      + "с его памятью. В той же папке останутся секреты: ключ модели, вход в аккаунт "
      + "ChatGPT и сессия твоего Telegram. Удалить их можно и позже, убрав папку целиком; "
      + "рядом с ней будет записка КАК-ВЕРНУТЬСЯ.md.";
    const PURGE = "Папка data исчезнет вместе с программой: память, дневник, конституция и "
      + "всё, что агент написал о себе и о людях, — безвозвратно, без корзины. Записки "
      + "КАК-ВЕРНУТЬСЯ.md не будет: возвращаться будет некуда. Если хочешь забрать память "
      + "с собой, закрой это окно и выполни рядом с программой "
      + "`helene-setup.exe --export` — он соберёт архив переноса, и только потом снимай.";
    const why = explain("Что останется", KEEP);
    const whyText = why.querySelector("p");
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
        if (whyText) whyText.textContent = this.purge ? PURGE : KEEP;
        why.classList.toggle("explain-danger", this.purge);
      },
    });
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.mount(head, lead, pick, why, this.actions, this.result);
  }

  get locked(): boolean {
    return this.started;
  }

  private async run() {
    if (this.started) return;
    this.started = true;
    this.actions.hidden = true;
    this.result.hidden = false;
    this.result.replaceChildren(el("p", "muted", "Снимаю…"));
    let text: string;
    let failed = false;
    try {
      text = await runUninstall(this.purge);
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
    this.result.replaceChildren(title, note, close);
  }
}
