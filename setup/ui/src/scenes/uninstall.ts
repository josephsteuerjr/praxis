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
    const pick = choice<"keep" | "purge">({
      value: "keep",
      items: [
        { value: "keep", title: "Оставить данные", text: "память, конституция, ключ модели, вход в Telegram и ChatGPT остаются в папке data" },
        { value: "purge", title: "Удалить всё", text: "папка данных исчезает вместе с программой, восстановить будет нечего" },
      ],
      onChange: (v) => (this.purge = v === "purge"),
    });
    this.actions = el("div", "install-actions");
    this.actions.append(button("Снять", "primary", () => void this.run()));
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.mount(head, lead, pick, explain("Что останется",
      "Если данные оставить, следующая установка в ту же папку подхватит агента с его памятью. В той же папке останутся секреты: " +
      "ключ модели, вход в аккаунт ChatGPT и сессия твоего Telegram. Удалить их можно и позже, убрав папку целиком; " +
      "рядом с ней будет записка КАК-ВЕРНУТЬСЯ.md."), this.actions, this.result);
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
    const note = el("p", "muted", text);
    const close = button("Закрыть", "primary", () => void getCurrentWindow().close());
    this.result.replaceChildren(title, note, close);
  }
}
