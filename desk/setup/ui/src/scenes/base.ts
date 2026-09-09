// Общая механика сцен с вводом: блоки приходят и уходят по ветру, у сцены
// есть проверка перед шагом вперёд и мягкий толчок, когда идти нельзя.
import { animate } from "motion";
import { EASE, T, enterWithWind, leaveWithWind, settle, type Dir } from "../wind";

export abstract class FormScene {
  readonly root: HTMLElement;
  protected blocks: HTMLElement[] = [];

  constructor(root: HTMLElement) {
    this.root = root;
  }

  protected mount(...blocks: HTMLElement[]) {
    for (const b of blocks) b.classList.add("about-block");
    this.root.replaceChildren(...blocks);
    this.blocks = blocks;
  }

  /** null — можно идти дальше; строка — что мешает (покажется подсказкой). */
  validate(): string | null {
    return null;
  }

  /** Сцена показана и готова: подставить свежие значения, поставить фокус. */
  protected onEntered(): void {}

  async enter(dir: Dir): Promise<void> {
    this.root.hidden = false;
    for (const b of this.blocks) settle(b, false);
    this.beforeEnter();
    await enterWithWind(this.blocks, dir, { step: 0.11 }).finished;
    for (const b of this.blocks) settle(b, true);
    this.onEntered();
  }

  /** Перед появлением: обновить содержимое по состоянию установки. */
  protected beforeEnter(): void {}

  async leave(dir: Dir): Promise<void> {
    await leaveWithWind(this.blocks, dir, { step: 0.05 }).finished;
    for (const b of this.blocks) settle(b, false);
    this.root.hidden = true;
  }

  async nudge(): Promise<void> {
    await animate(this.blocks, { x: [0, 14, 0] }, { duration: 0.6 * T, ease: EASE.soft }).finished;
  }

  setStatic() {
    this.beforeEnter();
    this.root.hidden = false;
    for (const b of this.blocks) settle(b, true);
    this.onEntered();
  }
}
