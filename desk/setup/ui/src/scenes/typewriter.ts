// Сцена «печатная машинка»: объёмный текст о том, что такое Frame, набирается
// на глазах — с живым ритмом (паузы на знаках препинания, редкие запинки),
// курсором-кареткой и возможностью дописать всё сразу нажатием справа.
import { EASE, T, enterWithWind, leaveWithWind, rand, settle, type Dir } from "../wind";
import { animate } from "motion";

type TypewriterCopy = { title: string; paragraphs: readonly string[] };

type Op = { p: number; ch: string };

/** Пауза после символа, мс: ритм машинистки, а не равномерный тикер. */
function pauseAfter(ch: string): number {
  if (ch === "\n") return 560;
  if (".!?…".includes(ch)) return 240 + rand(0, 90);
  if (",;:".includes(ch)) return 110 + rand(0, 50);
  if (ch === " ") return 30 + rand(0, 18);
  return 20 + rand(0, 18) + (Math.random() < 0.035 ? 110 : 0);
}

export class TypewriterScene {
  readonly root: HTMLElement;
  private title: HTMLElement;
  private body: HTMLElement;
  private cursor: HTMLElement;
  private copy: TypewriterCopy;
  private ops: Op[] = [];
  private at = 0;
  private raf = 0;
  private nextAt = 0;
  private paragraphs: HTMLElement[] = [];
  typing = false;
  done = false;

  constructor(root: HTMLElement, copy: TypewriterCopy) {
    this.root = root;
    this.copy = copy;
    const title = document.createElement("h2");
    title.className = "tw-title about-block";
    title.textContent = copy.title;
    const body = document.createElement("div");
    body.className = "tw-body about-block";
    body.setAttribute("aria-label", copy.paragraphs.join("\n\n"));
    const cursor = document.createElement("span");
    cursor.className = "tw-cursor";
    cursor.setAttribute("aria-hidden", "true");
    root.replaceChildren(title, body);
    this.title = title;
    this.body = body;
    this.cursor = cursor;
  }

  private reset() {
    this.stop();
    this.body.replaceChildren();
    this.paragraphs = [];
    this.ops = [];
    for (const [i, text] of this.copy.paragraphs.entries()) {
      for (const ch of text) this.ops.push({ p: i, ch });
      if (i < this.copy.paragraphs.length - 1) this.ops.push({ p: i, ch: "\n" });
    }
    this.at = 0;
    this.done = false;
    this.cursor.classList.remove("typing", "gone");
  }

  private paragraph(i: number): HTMLElement {
    while (this.paragraphs.length <= i) {
      const p = document.createElement("p");
      this.body.append(p);
      this.paragraphs.push(p);
    }
    return this.paragraphs[i];
  }

  private put(op: Op) {
    const p = this.paragraph(op.p);
    if (op.ch === "\n") {
      this.paragraph(op.p + 1).append(this.cursor);
      return;
    }
    p.insertBefore(document.createTextNode(op.ch), this.cursor.parentElement === p ? this.cursor : null);
    if (this.cursor.parentElement !== p) p.append(this.cursor);
  }

  private start() {
    if (this.ops.length === 0) return;
    this.typing = true;
    this.cursor.classList.add("typing");
    this.paragraph(0).append(this.cursor);
    this.nextAt = performance.now() + 420 * T;
    const tick = (now: number) => {
      // На одном кадре можно набрать несколько символов, если вкладка отставала.
      let guard = 0;
      while (this.at < this.ops.length && now >= this.nextAt && guard++ < 40) {
        const op = this.ops[this.at++];
        this.put(op);
        this.nextAt += pauseAfter(op.ch) * (T < 1 ? 0.3 : 1);
      }
      if (this.at >= this.ops.length) {
        this.finish();
        return;
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  private stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.typing = false;
  }

  private finish() {
    this.stop();
    this.done = true;
    this.cursor.classList.remove("typing");
    // Каретка ещё немного мигает и уходит: текст набран, машинка замолчала.
    window.setTimeout(() => this.cursor.classList.add("gone"), 2600 * T);
  }

  /** Дописать всё сразу (нажатие «дальше» во время набора). */
  finishNow(): boolean {
    if (!this.typing) return false;
    this.stop();
    while (this.at < this.ops.length) this.put(this.ops[this.at++]);
    this.finish();
    return true;
  }

  nextLabel(): string {
    return this.typing ? "Дописать" : "Далее";
  }

  async enter(dir: Dir): Promise<void> {
    this.reset();
    this.root.hidden = false;
    settle(this.title, false);
    settle(this.body, false);
    await enterWithWind([this.title, this.body], dir, { step: 0.16 }).finished;
    settle(this.title, true);
    settle(this.body, true);
    this.start();
  }

  async leave(dir: Dir): Promise<void> {
    this.stop();
    await leaveWithWind([this.title, this.body], dir, { step: 0.08 }).finished;
    settle(this.title, false);
    settle(this.body, false);
    this.root.hidden = true;
  }

  async nudge(): Promise<void> {
    await animate([this.title, this.body], { x: [0, 16, 0] }, { duration: 0.7 * T, ease: EASE.soft })
      .finished;
  }

  /** Для статичных снимков: всё набрано, каретка ушла. */
  setStatic() {
    this.reset();
    this.root.hidden = false;
    settle(this.title, true);
    settle(this.body, true);
    while (this.at < this.ops.length) this.put(this.ops[this.at++]);
    this.done = true;
    this.cursor.classList.add("gone");
  }
}
