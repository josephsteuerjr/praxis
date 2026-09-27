// Масштаб ленты: Ctrl+колесо, щипок тачпада, Ctrl+= / Ctrl+− / Ctrl+0.
// Слово Егора 28.09: «масштаб не меняется».
//
// Как у карт Google: во время жеста содержимое увеличивается трансформом вокруг курсора
// (это делает видеокарта, кадр дешёвый), а когда жест затих — один раз меняется размер
// шрифта контейнера (всё внутри в em): текст переносится заново, трансформ снимается, точка
// под курсором остаётся под курсором. Первый вариант (шрифт каждый кадр) пересчитывал
// раскладку всей ленты на кадр: 250 сообщений в Electron — 50 мс, пропуски 28% (стенд 28.09).
import type { Scroller } from "./scroller";

export interface ZoomOptions {
  min?: number;
  max?: number;
  /** Ключ в localStorage: масштаб переживает перезапуск окна. */
  storageKey?: string;
  onChange?: (z: number) => void;
}

const SETTLE_MS = 160; // тишина жеста, после которой раскладка пересчитывается

export class Zoom {
  /** мс: плавность масштаба (кастомизация окна). */
  tau = 95;
  private committed = 1; // масштаб, под который разложен текст
  private z = 1; // показанный сейчас
  private goal = 1;
  private raf = 0;
  private last = 0;
  private lastInput = 0;
  private origin: { x: number; y: number; clientY: number; bottom: boolean } | null = null;
  private readonly min: number;
  private readonly max: number;
  private readonly key?: string;
  private readonly onChange?: (z: number) => void;

  /**
   * `box` — узел, который масштабируется: ему ставится --zoom и трансформ жеста. Он должен
   * лежать ВНУТРИ `scroller.inner` (тот носит свой трансформ — перетяг края).
   */
  constructor(
    private readonly scroller: Scroller,
    private readonly box: HTMLElement,
    opts: ZoomOptions = {},
  ) {
    this.min = opts.min ?? 0.7;
    this.max = opts.max ?? 2.2;
    this.key = opts.storageKey;
    this.onChange = opts.onChange;
    if (this.key) {
      try {
        const saved = parseFloat(localStorage.getItem(this.key) || "");
        if (saved >= this.min && saved <= this.max) this.committed = this.z = this.goal = saved;
      } catch { /* нет хранилища — начинаем со 100% */ }
    }
    box.style.setProperty("--zoom", this.committed.toFixed(4));
    this.onChange?.(this.committed);
    scroller.el.addEventListener("wheel", this.onWheel, { passive: false });
    window.addEventListener("keydown", this.onKey);
  }

  get value(): number {
    return this.goal;
  }

  set(z: number, clientY?: number) {
    const box = this.scroller.el.getBoundingClientRect();
    this.aim(z, box.left + box.width / 2, clientY ?? box.top + box.height / 2);
  }

  private onWheel = (e: WheelEvent) => {
    if (!e.ctrlKey) return;
    e.preventDefault(); // иначе движок масштабирует всю страницу рывком
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 40;
    // Щелчок колеса — шаг 10%; щипок тачпада — мелкие дельты, плавно.
    const f = Math.abs(dy) >= 50 ? (dy < 0 ? 1.1 : 1 / 1.1) : Math.exp(-dy * 0.012);
    this.aim(this.goal * f, e.clientX, e.clientY);
  };

  private onKey = (e: KeyboardEvent) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const box = this.scroller.el.getBoundingClientRect();
    const x = box.left + box.width / 2, y = box.top + box.height / 2;
    if (e.key === "=" || e.key === "+") { e.preventDefault(); this.aim(this.goal * 1.1, x, y); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); this.aim(this.goal / 1.1, x, y); }
    else if (e.key === "0") { e.preventDefault(); this.aim(1, x, y); }
  };

  private aim(z: number, clientX: number, clientY: number) {
    const next = Math.min(this.max, Math.max(this.min, z));
    // Привязать к 100%, если прошли рядом: щипком ровно в 1.0 не попасть.
    this.goal = Math.abs(next - 1) < 0.03 ? 1 : next;
    this.lastInput = performance.now();
    if (!this.origin) {
      // Опора жеста — точка под курсором в координатах узла (трансформа ещё нет).
      const r = this.box.getBoundingClientRect();
      const bottom = this.scroller.pinned;
      const view = this.scroller.el.getBoundingClientRect();
      const y = bottom ? Math.min(r.height, view.bottom - r.top) : clientY - r.top;
      this.origin = { x: clientX - r.left, y, clientY: bottom ? view.bottom : clientY, bottom };
      this.box.style.transformOrigin = `${this.origin.x.toFixed(1)}px ${this.origin.y.toFixed(1)}px`;
      this.box.style.willChange = "transform";
    }
    if (!this.raf) {
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.frame);
    }
  }

  private frame = (now: number) => {
    this.raf = 0;
    const dt = Math.min(48, now - this.last);
    this.last = now;
    this.z += (this.goal - this.z) * (1 - Math.exp(-dt / Math.max(1, this.tau)));
    if (Math.abs(this.goal - this.z) < 0.001) this.z = this.goal;
    this.box.style.transform = `scale(${(this.z / this.committed).toFixed(4)})`;
    this.onChange?.(this.z);
    if (this.z === this.goal && now - this.lastInput >= SETTLE_MS) {
      this.commit();
      return;
    }
    this.raf = requestAnimationFrame(this.frame);
  };

  /** Жест затих: разложить текст под новый масштаб и снять трансформ без сдвига. */
  private commit() {
    const o = this.origin;
    this.origin = null;
    // Что сейчас под опорой: элемент-строка и доля его высоты.
    let pin: { el: Element; frac: number } | null = null;
    if (o && !o.bottom) {
      for (const el of Array.from(this.box.children)) {
        const r = el.getBoundingClientRect();
        if (r.height && r.top <= o.clientY && r.bottom >= o.clientY) { pin = { el, frac: (o.clientY - r.top) / r.height }; break; }
      }
    }
    this.committed = this.goal;
    this.box.style.setProperty("--zoom", this.committed.toFixed(4));
    this.box.style.transform = "";
    this.box.style.transformOrigin = "";
    this.box.style.willChange = "";
    if (o?.bottom) this.scroller.toBottom(false);
    else if (pin && o) {
      const r = pin.el.getBoundingClientRect();
      this.scroller.shiftBy(r.top + pin.frac * r.height - o.clientY);
    }
    this.onChange?.(this.committed);
    if (this.key) {
      try { localStorage.setItem(this.key, String(this.committed)); } catch { /* не сохранится — не беда */ }
    }
  }
}
