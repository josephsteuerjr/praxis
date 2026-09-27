// Общая механика сцен с вводом: блоки приходят и уходят по ветру, у сцены
// есть проверка перед шагом вперёд и мягкий толчок, когда идти нельзя.
import { animate } from "motion";
import { EASE, T, enterWithWind, leaveWithWind, reduced, settle, type Dir } from "../wind";

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
    // Длинная сцена листается (styles.css, .scene-form): приходит всегда с начала.
    this.root.scrollTop = 0;
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

  /** Итог появился ниже края листа — подвести лист к нему. Живой проклик 1.2.0: «Снято»
   *  и «Закрыть» вставали под нижний край 1080p-экрана, и человек видел одни галочки.
   *  Листаем только сцену: scrollIntoView прокрутил бы и #viewport, а кадр стоит в нём
   *  по центру через transform. */
  protected reveal(target: HTMLElement) {
    const scene = this.root;
    requestAnimationFrame(() => {
      const box = scene.getBoundingClientRect();
      const k = box.height / (scene.clientHeight || box.height) || 1;
      const below = (target.getBoundingClientRect().bottom - box.bottom) / k + 48;
      if (below > 0) scene.scrollBy({ top: below, behavior: reduced ? "auto" : "smooth" });
    });
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
