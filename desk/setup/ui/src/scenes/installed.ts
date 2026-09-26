// Сцена «Уже установлена»: Hélène на этой машине уже стоит, и мастер обязан
// сказать это первым делом — а не вести человека через имя, конституцию и
// ключи заново, как при первой установке.
//
// ⚠ 26.09, слово Егора: «его можно ставить бесконечное количество раз — это
// просто папка, полная дичь». Нормальная программа поверх себя предлагает
// обновить или удалить. Решения обновления берутся из самой установки (те же,
// что у `helene-setup.exe --update`): память, конституция и настройки остаются.
// «Настроить заново» — прежний маршрут мастера, для тех, кому нужно сменить
// имя или модель; он и раньше был обновлением поверх, а не второй копией.
import { FormScene } from "./base";
import { button, el, explain } from "./form";
import { PRODUCT_NAME } from "../config";
import { machine, type Installed } from "../setup";

export interface InstalledActions {
  /** Обновить поверх: решения из установки, дальше — экран установки. */
  update: () => void;
  /** Снять программу: сцена снятия с выбором судьбы данных. */
  remove: () => void;
  /** Пройти мастер заново (имя, конституция, модель) — тоже поверх. */
  fresh: () => void;
}

export class InstalledScene extends FormScene {
  private lead: HTMLElement;
  private actions: InstalledActions | null = null;
  private updateButton: HTMLButtonElement;
  private version = "";

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", `${PRODUCT_NAME} уже установлена`));
    this.lead = el("p", "form-lead", "");
    const buttons = el("div", "install-actions");
    this.updateButton = button("Обновить", "primary", () => this.actions?.update());
    buttons.append(
      this.updateButton,
      button("Удалить", "quiet", () => this.actions?.remove()),
      button("Настроить заново", "quiet", () => this.actions?.fresh()),
    );
    const why = explain(
      "Что будет",
      "«Обновить» ставит новую версию поверх: память, дневник, конституция, ключ модели и "
        + "настройки остаются, вопросов не будет. «Удалить» снимает программу и спрашивает, "
        + "что делать с данными. «Настроить заново» — прежний мастер: имя, конституция, "
        + "модель; это тоже установка поверх, а не вторая копия.",
    );
    this.mount(head, this.lead, buttons, why);
  }

  /** Куда идти по кнопкам — решает main.ts, у сцены маршрута нет. */
  bind(actions: InstalledActions, version: string) {
    this.actions = actions;
    this.version = version;
  }

  protected beforeEnter(): void {
    const inst: Installed | null = machine.installed;
    const was = inst?.version ? `версия ${inst.version}` : "прежняя версия";
    const who = inst?.agent ? `, агент ${inst.agent}` : "";
    const where = inst?.dir ? ` Папка: ${inst.dir}.` : "";
    const now = this.version && this.version !== "превью" ? ` Эта поставка — ${this.version}.` : "";
    this.lead.textContent = `${was}${who}.${where}${now}`;
    this.updateButton.textContent =
      this.version && this.version !== "превью" ? `Обновить до ${this.version}` : "Обновить";
  }
}
