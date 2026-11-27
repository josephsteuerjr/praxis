import { t as uiText } from "../../../../lang/lang";
// Сцена «Уже установлена»: Hélène на этой машине уже стоит, и мастер обязан
// сказать это первым делом — а не вести человека через имя, конституцию и
// ключи заново, как при первой установке.
//
// ⚠ 26.09, слово владельца: «его можно ставить бесконечное количество раз — это
// просто папка, полная дичь». Нормальная программа поверх себя предлагает
// обновить или удалить. Решения обновления берутся из самой установки (те же,
// что у `helene-setup.exe --update`): память, конституция и настройки остаются.
// «Настроить заново» — прежний маршрут мастера, для тех, кому нужно сменить
// имя или модель; он и раньше был обновлением поверх, а не второй копией.
import { FormScene } from "./base";
import { button, el, explain } from "./form";
import { PRODUCT_NAME, isPraxis } from "../config";
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
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.installed.17e1375130", {p0: String(PRODUCT_NAME), p1: String(isPraxis() ? uiText("ui.setup.ui.src.scenes.installed.6a265992da") : uiText("ui.setup.ui.src.scenes.installed.e842608049"))})));
    this.lead = el("p", "form-lead", "");
    const buttons = el("div", "install-actions");
    this.updateButton = button(uiText("ui.setup.ui.src.scenes.installed.603e460bf5"), "primary", () => this.actions?.update());
    buttons.append(this.updateButton, button(uiText("ui.setup.ui.src.scenes.installed.be99b13612"), "quiet", () => this.actions?.remove()));
    if (!isPraxis()) buttons.append(button(uiText("ui.setup.ui.src.scenes.installed.50f247b205"), "quiet", () => this.actions?.fresh()));
    const why = explain(
      uiText("ui.setup.ui.src.scenes.installed.e8bfb6440f"),
      isPraxis()
        ? uiText("ui.setup.ui.src.scenes.installed.0148066319")
        : uiText("ui.setup.ui.src.scenes.installed.d0015c2b4f"),
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
    const was = inst?.version ? uiText("ui.setup.ui.src.scenes.installed.0295e8b3df", {p0: String(inst.version)}) : uiText("ui.setup.ui.src.scenes.installed.26b7f8d973");
    const who = inst?.agent ? uiText("ui.setup.ui.src.scenes.installed.ac9614b48c", {p0: String(inst.agent)}) : "";
    const where = inst?.dir ? uiText("ui.setup.ui.src.scenes.installed.0fa5225e7a", {p0: String(inst.dir)}) : "";
    const now = this.version && this.version !== uiText("ui.setup.ui.src.scenes.installed.7f8130d74a") ? uiText("ui.setup.ui.src.scenes.installed.b7486c3fd1", {p0: String(this.version)}) : "";
    this.lead.textContent = `${was}${who}.${where}${now}`;
    this.updateButton.textContent =
      this.version && this.version !== uiText("ui.setup.ui.src.scenes.installed.7f8130d74a") ? uiText("ui.setup.ui.src.scenes.installed.56a0c1be66", {p0: String(this.version)}) : uiText("ui.setup.ui.src.scenes.installed.603e460bf5");
  }
}
