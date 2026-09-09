// Сцена 2: о программе. Заголовок, абзац и три факта приходят по ветру
// один за другим; уходят так же, только быстрее.
import { animate, stagger } from "motion";
import { EASE, T, enterWithWind, leaveWithWind, settle, type Dir } from "../wind";
import type { COPY } from "../config";

type AboutCopy = typeof COPY.about;

export class AboutScene {
  readonly root: HTMLElement;
  private blocks: HTMLElement[];

  constructor(root: HTMLElement, copy: AboutCopy) {
    this.root = root;
    const h2 = document.createElement("h2");
    h2.className = "headline about-block";
    for (const line of copy.headline) {
      const span = document.createElement("span");
      span.className = "line";
      span.textContent = line;
      h2.append(span);
    }
    const lead = document.createElement("p");
    lead.className = "lead about-block";
    lead.textContent = copy.lead;
    const facts = document.createElement("div");
    facts.className = "facts";
    for (const f of copy.facts) {
      const box = document.createElement("div");
      box.className = "fact about-block";
      const h3 = document.createElement("h3");
      h3.textContent = f.title;
      const p = document.createElement("p");
      p.textContent = f.text;
      box.append(h3, p);
      facts.append(box);
    }
    root.replaceChildren(h2, lead, facts);
    this.blocks = [...root.querySelectorAll<HTMLElement>(".about-block")];
  }

  async enter(dir: Dir): Promise<void> {
    this.root.hidden = false;
    for (const b of this.blocks) settle(b, false);
    await enterWithWind(this.blocks, dir, { step: 0.13 }).finished;
    for (const b of this.blocks) settle(b, true);
  }

  async leave(dir: Dir): Promise<void> {
    await leaveWithWind(this.blocks, dir, { step: 0.06 }).finished;
    for (const b of this.blocks) settle(b, false);
    this.root.hidden = true;
  }

  /** Конец прототипа: мягкий толчок вперёд и назад, дальше сцен нет. */
  async nudge(): Promise<void> {
    await animate(
      this.blocks,
      { x: [0, 16, 0] },
      { duration: 0.7 * T, ease: EASE.soft, delay: stagger(0.03 * T) },
    ).finished;
  }

  /** Для статичных снимков: всё на месте, без анимации. */
  setStatic() {
    this.root.hidden = false;
    for (const b of this.blocks) settle(b, true);
  }
}
