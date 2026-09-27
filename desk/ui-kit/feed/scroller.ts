// Прокрутка с физикой: колесо плавно, «тянуть и водить» мышью с инерцией, резинка на
// краях, прилипание к низу. Слово Егора 28.09: «плавно ничего не двигается (а могло бы,
// чтобы можно было просто оттягивать и водить туда-сюда, как в гугловских приложениях)».
//
// Прокрутка остаётся НАТИВНОЙ (scrollTop у контейнера): клавиатура, полоса прокрутки,
// поиск по странице, scrollIntoView и чтение с экрана работают как прежде. Физика только
// ведёт scrollTop кадр за кадром и рисует перетяг за край трансформом содержимого.
//
// Один цикл requestAnimationFrame, и только пока что-то движется: в покое кадров нет.

export type Overscroll = "stretch" | "rubber" | "none";

/**
 * Характер движения. Егор 28.09: «ещё более тягучим всё и плавным», выбрал «тягуче».
 * Пропорции перетяга — «бережно»: предел ~треть окна, обычная оттяжка пальцами — 40–70 px
 * на экране; тягучесть — во времени (мягкий долгий возврат), а не в размахе.
 */
export interface Feel {
  wheelTau: number; // мс: сглаживание щелчка колеса
  padTau: number; // мс: сглаживание тачпада (и оттяжки пальцами)
  followTau: number; // мс: езда за низом и к точке
  flingTau: number; // мс: затухание броска — чем больше, тем дальше катится
  maxFling: number; // px/мс
  springW: number; // 1/мс: пружина возврата с края (меньше — мягче и дольше)
  rubberC: number; // сколько перетяга отдаёт рука (меньше — туже)
  rubberD: number; // предел перетяга, доля высоты окна
}

export const FEELS: Record<"brisk" | "smooth" | "syrup", Feel> = {
  brisk: { wheelTau: 95, padTau: 18, followTau: 110, flingTau: 325, maxFling: 7, springW: 0.012, rubberC: 0.5, rubberD: 0.24 },
  smooth: { wheelTau: 150, padTau: 30, followTau: 170, flingTau: 460, maxFling: 9, springW: 0.0078, rubberC: 0.5, rubberD: 0.28 },
  // Возврат 660 мс — значение, которое Егор сам выставил ползунком 28.09.
  syrup: { wheelTau: 210, padTau: 45, followTau: 240, flingTau: 620, maxFling: 10, springW: 4 / 660, rubberC: 0.5, rubberD: 0.32 },
};
export type FeelName = keyof typeof FEELS;

export interface ScrollerOptions {
  /** Как выглядит перетяг за край: резинка (iOS), растяжка (Android 12+) или никак. */
  overscroll?: Overscroll;
  feel?: FeelName;
  /** Можно ли начать «тянуть» с этого места. По умолчанию — не с управления и не с текста. */
  canGrab?: (target: Element, x: number, y: number) => boolean;
  /** Ехать за низом, когда прилипли (лента чата). У остальных разделов — нет. */
  stick?: boolean;
  /** Ближе к низу, чем это, — лента прилипает и сама едет за новыми сообщениями. */
  pinSlack?: number;
  onPinnedChange?: (pinned: boolean) => void;
}

/** С управления не тянем: там клик и своё поведение. */
export const NO_GRAB =
  "a,button,input,textarea,select,option,summary,audio,video,label,img,canvas,iframe,[contenteditable],[draggable=true],[data-nograb],pre,code";

/**
 * Под указателем — сами буквы текста (а не поле между строками и абзацами)?
 * С текста тянуть нельзя: там выделение. Тянется всё остальное — поля, промежутки, пустое.
 */
export function overText(x: number, y: number): boolean {
  const doc = document as Document & { caretRangeFromPoint?(x: number, y: number): Range | null };
  const hit = doc.caretRangeFromPoint?.(x, y);
  const node = hit?.startContainer;
  if (!node || node.nodeType !== Node.TEXT_NODE || !(node.textContent || "").trim()) return false;
  const r = document.createRange();
  r.selectNodeContents(node);
  for (const box of Array.from(r.getClientRects())) {
    if (x >= box.left - 2 && x <= box.right + 2 && y >= box.top && y <= box.bottom) return true;
  }
  return false;
}

type Mode = "idle" | "wheel" | "drag" | "fling";

/**
 * Жест тачпада. Проверено по живому журналу тачпада Егора (28.09, 17 жестов из 17):
 * Chromium на Windows шлёт wheel с НУЛЕВОЙ дельтой в миг, когда пальцы поднялись (конец
 * фазы; дальше — инерция системы теми же событиями ровно раз в кадр), и ещё раз, когда
 * инерция кончилась. Даже после полутора секунд неподвижных пальцев ноль приходит.
 *  fingers — пальцы на тачпаде: за краем оттяжка держится сколько угодно;
 *  inertia — пальцы подняли: у края смахивание упирается, оттяжка мягко возвращается;
 *  none    — жеста нет.
 * Новое касание посреди инерции узнаётся сразу — дельта растёт, меняет знак или сбивается
 * ровный шаг кадра, — поэтому после «парковки» у края тянуть можно немедленно.
 */
type Pad = "none" | "fingers" | "inertia";

const STREAM_GAP = 140; // мс тишины колеса — новый жест
const INERTIA_GAP = 45; // мс: инерция идёт каждый кадр; дольше — это уже пальцы
const HOLD_FALLBACK = 260; // мс: устройство без нулевых событий — держим так
const HOLD_SAFETY = 8000; // мс: ноль потерялся — всё равно отпустить

export class Scroller {
  readonly el: HTMLElement;
  readonly inner: HTMLElement;
  overscroll: Overscroll;
  feel: Feel;
  private readonly canGrab: (t: Element, x: number, y: number) => boolean;
  /** Ехать за низом, когда прилипли. Окно включает это только в чате. */
  stick: boolean;
  private readonly pinSlack: number;
  private readonly onPinnedChange?: (p: boolean) => void;

  private pos = 0; // логический scrollTop, дробный
  private target = 0; // куда ведёт колесо
  private tau = 150;
  private notch = true; // последнее колесо — щелчок мыши (не тачпад)
  private vel = 0; // px/мс — бросок
  private raw = 0; // перетяг, показанный сейчас («как тянули»: −верх, +низ)
  private rawVel = 0;
  private pull = 0; // перетяг, который держат пальцы тачпада (к нему плавно идёт raw)
  private mode: Mode = "idle";
  private raf = 0;
  private last = 0;
  private written = -1;
  private lastWheel = 0;
  private pad: Pad = "none";
  private padPrev = 0; // |dy| прошлого события инерции
  private padDir = 0;
  private sawLift = false; // устройство присылает нулевое «пальцы поднялись»
  private holdTimer = 0;
  private pinnedState = true;
  private follow = true; // ехать за низом, пока прилипли
  private drag: null | {
    id: number;
    y0: number;
    pos0: number;
    raw0: number;
    moved: boolean;
    samples: Array<{ t: number; y: number }>;
  } = null;
  private ro: ResizeObserver;

  constructor(el: HTMLElement, inner: HTMLElement, opts: ScrollerOptions = {}) {
    this.el = el;
    this.inner = inner;
    this.overscroll = opts.overscroll ?? "rubber";
    this.feel = { ...FEELS[opts.feel ?? "syrup"] };
    this.canGrab = opts.canGrab ?? ((t, x, y) => !t.closest(NO_GRAB) && !overText(x, y));
    this.stick = opts.stick ?? true;
    this.pinSlack = opts.pinSlack ?? 28;
    this.onPinnedChange = opts.onPinnedChange;
    // Своя привязка прокрутки (preserve) — нативная в Chromium дёргала бы второй раз,
    // а в WebKit её нет вовсе.
    el.style.overflowAnchor = "none";
    this.pos = this.target = el.scrollTop;
    el.addEventListener("wheel", this.onWheel, { passive: false });
    el.addEventListener("scroll", this.onScroll, { passive: true });
    el.addEventListener("pointerdown", this.onDown);
    el.addEventListener("pointermove", this.onMove);
    el.addEventListener("pointerup", this.onUp);
    el.addEventListener("pointercancel", this.onUp);
    el.addEventListener("keydown", this.onKey);
    this.ro = new ResizeObserver(() => this.contentChanged());
    this.ro.observe(inner);
    this.ro.observe(el);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    clearTimeout(this.holdTimer);
    this.ro.disconnect();
    this.el.removeEventListener("wheel", this.onWheel);
    this.el.removeEventListener("scroll", this.onScroll);
    this.el.removeEventListener("pointerdown", this.onDown);
    this.el.removeEventListener("pointermove", this.onMove);
    this.el.removeEventListener("pointerup", this.onUp);
    this.el.removeEventListener("pointercancel", this.onUp);
    this.el.removeEventListener("keydown", this.onKey);
  }

  /** Состояние для журнала жестов лаборатории. */
  debug(): { pos: number; max: number; raw: number; pull: number; pad: Pad; notch: boolean } {
    const r = (v: number) => Math.round(v * 10) / 10;
    return { pos: Math.round(this.pos), max: Math.round(this.max), raw: r(this.raw), pull: r(this.pull), pad: this.pad, notch: this.notch };
  }

  get pinned(): boolean {
    return this.pinnedState;
  }

  private get max(): number {
    return Math.max(0, this.el.scrollHeight - this.el.clientHeight);
  }

  /** Плавно к низу (кнопка «новые ↓», своё отправленное). */
  toBottom(smooth = true) {
    this.setPinned(true);
    this.follow = true;
    if (!smooth) {
      this.pos = this.target = this.max;
      this.write();
      return;
    }
    this.vel = 0;
    this.mode = "wheel";
    this.tau = this.feel.followTau * 1.2;
    this.target = this.max;
    this.kick();
  }

  /** Плавно к точке (переход к сообщению). */
  scrollTo(y: number, smooth = true) {
    const t = clamp(y, 0, this.max);
    this.setPinned(this.max - t <= this.pinSlack);
    this.follow = this.pinnedState;
    if (!smooth) {
      this.pos = this.target = t;
      this.write();
      return;
    }
    this.vel = 0;
    this.mode = "wheel";
    this.tau = this.feel.followTau * 1.2;
    this.target = t;
    this.kick();
  }

  /** Бросок со скоростью v px/мс (стенд лаборатории; жест мыши делает то же самое). */
  fling(v: number) {
    this.vel = clamp(v, -this.feel.maxFling, this.feel.maxFling);
    this.mode = "fling";
    if (v < 0) {
      this.follow = false;
      this.setPinned(false);
    }
    this.kick();
  }

  /** Сдвинуть без анимации (масштаб держит точку под курсором). */
  shiftBy(dy: number) {
    if (!dy) return;
    this.pos = clamp(this.pos + dy, 0, this.max);
    this.target = clamp(this.target + dy, 0, this.max);
    this.write();
  }

  /**
   * Правка содержимого без прыжка: то, что человек читает, остаётся на месте.
   * Прилипшую ленту не держим — она сама поедет за низом.
   */
  preserve(mutate: () => void) {
    if (this.pinnedState) {
      mutate();
      this.contentChanged();
      return;
    }
    const anchor = this.anchor();
    mutate();
    if (anchor && anchor.el.isConnected) {
      const now = anchor.el.getBoundingClientRect().top;
      this.shiftBy(now - anchor.top);
    }
  }

  /** Первый видимый элемент содержимого и где он сейчас. */
  private anchor(): { el: Element; top: number } | null {
    const box = this.el.getBoundingClientRect();
    const walk = (parent: Element): { el: Element; top: number } | null => {
      for (const child of Array.from(parent.children)) {
        const r = child.getBoundingClientRect();
        if (r.bottom > box.top + 1) {
          // Спуститься на уровень ниже, если элемент выше окна (длинный блок).
          if (r.top < box.top && child.children.length) {
            const deeper = walk(child);
            if (deeper) return deeper;
          }
          return { el: child, top: r.top };
        }
      }
      return null;
    };
    return walk(this.inner);
  }

  private setPinned(p: boolean) {
    if (p === this.pinnedState) return;
    this.pinnedState = p;
    this.onPinnedChange?.(p);
  }

  private contentChanged() {
    const max = this.max;
    if (this.stick && this.pinnedState && this.follow && this.mode !== "drag") {
      // Ехать за низом плавно: новое сообщение вырастает, лента едет вслед.
      if (this.mode === "idle" || this.mode === "wheel") {
        this.mode = "wheel";
        this.tau = this.feel.followTau;
        this.target = max;
        this.kick();
      }
    } else if (this.pos > max) {
      this.pos = this.target = max;
      this.write();
    }
  }

  // ------------------------------------------------------------------ ввод

  private onWheel = (e: WheelEvent) => {
    if (e.ctrlKey || e.defaultPrevented) return; // масштаб — у zoom.ts
    const now = performance.now();
    const gap = now - this.lastWheel;
    this.lastWheel = now;

    // Нулевое событие — граница фазы жеста: пальцы поднялись или кончилась инерция.
    if (e.deltaY === 0 && e.deltaX === 0) {
      this.sawLift = true;
      this.pad = this.pad === "fingers" ? "inertia" : "none";
      this.padPrev = Infinity;
      this.release();
      return;
    }

    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 40;
    else if (e.deltaMode === 2) dy *= this.el.clientHeight;
    if (Math.abs(e.deltaX) > Math.abs(dy)) return;
    if (innerScrollable(e.target as Element, this.el, dy)) return;
    e.preventDefault();

    const notch = isNotch(e, dy, gap, this.pad);
    this.notch = notch;
    if (notch) {
      this.pad = "none";
    } else {
      const a = Math.abs(dy);
      const dir = Math.sign(dy);
      if (gap > STREAM_GAP || this.pad === "none") this.pad = "fingers";
      else if (this.pad === "inertia" && (dir !== this.padDir || gap > INERTIA_GAP || a > this.padPrev * 1.25 + 2)) this.pad = "fingers";
      this.padPrev = a;
      this.padDir = dir;
    }
    this.tau = notch ? this.feel.wheelTau : this.feel.padTau;
    if (this.mode !== "wheel") this.target = this.pos;
    this.mode = "wheel";
    this.vel = 0;
    const max = this.max;

    // Пальцы повели обратно из оттяжки — сначала съесть её, потом прокручивать.
    if (this.pull && Math.sign(dy) === -Math.sign(this.pull)) {
      const r = this.pull + dy;
      if (Math.sign(r) === Math.sign(this.pull)) { this.pull = r; dy = 0; }
      else { dy = r; this.pull = 0; }
    }

    const want = this.target + dy;
    const t = clamp(want, 0, max);
    const excess = want - t;
    if (dy < 0) {
      this.follow = false;
      if (max - t > this.pinSlack) this.setPinned(false);
    }
    this.target = t;

    if (excess && this.overscroll !== "none") {
      if (notch) {
        // Щелчок мыши в край — едва заметный мягкий толчок, не прыжок.
        if (Math.abs(this.raw) < 4) this.rawVel += Math.sign(excess) * 0.35;
      } else if (this.pad === "fingers") {
        // Оттягивание пальцами: держится, пока пальцы на тачпаде.
        this.pull += excess;
        this.armHold();
      }
      // Инерция доехала до края — упирается: остаток глотаем.
    }
    this.kick();
  };

  /** Пальцы подняли (или устройство молчит): оттяжка мягко возвращается. */
  private release() {
    clearTimeout(this.holdTimer);
    if (this.pull) {
      this.pull = 0;
      this.kick();
    }
  }

  /** Страховка удержания: без нулевых событий устройство держит коротко, с ними — долго. */
  private armHold() {
    clearTimeout(this.holdTimer);
    this.holdTimer = window.setTimeout(() => {
      this.pad = "none";
      this.release();
    }, this.sawLift ? HOLD_SAFETY : HOLD_FALLBACK);
  }

  private onScroll = () => {
    const top = this.el.scrollTop;
    if (Math.abs(top - this.written) <= 1.5) return;
    // Чужая прокрутка: клавиатура, полоса, поиск, scrollIntoView.
    if (this.mode !== "drag") {
      this.pos = this.target = top;
      this.vel = 0;
      this.mode = "idle";
    }
    this.written = top;
    const bottom = this.max - top <= this.pinSlack;
    this.setPinned(bottom);
    this.follow = bottom;
  };

  private onKey = (e: KeyboardEvent) => {
    if (e.target !== this.el) return;
    const page = this.el.clientHeight * 0.85;
    const step: Record<string, number> = { ArrowDown: 64, ArrowUp: -64, PageDown: page, PageUp: -page, " ": e.shiftKey ? -page : page };
    if (e.key === "End") { e.preventDefault(); this.toBottom(); return; }
    if (e.key === "Home") { e.preventDefault(); this.scrollTo(0); return; }
    const d = step[e.key];
    if (d == null) return;
    e.preventDefault();
    if (this.mode !== "wheel") this.target = this.pos;
    this.mode = "wheel";
    this.tau = this.feel.followTau;
    this.target = clamp(this.target + d, 0, this.max);
    if (d < 0) { this.follow = false; this.setPinned(this.max - this.target <= this.pinSlack); }
    this.kick();
  };

  private onDown = (e: PointerEvent) => {
    if (e.pointerType === "touch") return; // палец листает нативно, с инерцией системы
    const middle = e.button === 1;
    if (e.button !== 0 && !middle) return;
    const t = e.target as Element;
    if (!middle && !e.altKey && !this.canGrab(t, e.clientX, e.clientY)) return;
    // Полоса прокрутки: клик правее содержимого.
    if (e.clientX > this.el.getBoundingClientRect().left + this.el.clientWidth) return;
    e.preventDefault(); // не начинать выделение и не уводить фокус
    this.drag = { id: e.pointerId, y0: e.clientY, pos0: this.pos, raw0: this.raw, moved: false, samples: [{ t: e.timeStamp, y: e.clientY }] };
    this.vel = 0;
    this.rawVel = 0;
    this.pull = 0;
    this.mode = "drag";
    this.el.focus({ preventScroll: true });
  };

  private onMove = (e: PointerEvent) => {
    const d = this.drag;
    if (!d || e.pointerId !== d.id) return;
    const dy = e.clientY - d.y0;
    if (!d.moved) {
      if (Math.abs(dy) < 4) return;
      d.moved = true;
      this.el.setPointerCapture(e.pointerId);
      this.el.classList.add("grabbing");
    }
    const max = this.max;
    const want = d.pos0 + d.raw0 - dy;
    const p = clamp(want, 0, max);
    this.pos = this.target = p;
    this.raw = this.overscroll === "none" ? 0 : want - p;
    if (dy > 0) {
      this.follow = false;
      if (max - p > this.pinSlack) this.setPinned(false);
    }
    d.samples.push({ t: e.timeStamp, y: e.clientY });
    if (d.samples.length > 12) d.samples.shift();
    this.kick();
  };

  private onUp = (e: PointerEvent) => {
    const d = this.drag;
    if (!d || e.pointerId !== d.id) return;
    this.drag = null;
    this.el.classList.remove("grabbing");
    if (this.el.hasPointerCapture(e.pointerId)) this.el.releasePointerCapture(e.pointerId);
    if (!d.moved) {
      this.mode = "idle";
      return;
    }
    // Клик после «протащил» — не клик.
    const eat = (ev: Event) => { ev.stopPropagation(); ev.preventDefault(); };
    window.addEventListener("click", eat, { capture: true, once: true });
    setTimeout(() => window.removeEventListener("click", eat, { capture: true }), 0);
    // Скорость — по последним 90 мс; стоял перед отпусканием — броска нет.
    const now = e.timeStamp;
    const recent = d.samples.filter((s) => now - s.t <= 90);
    let v = 0;
    if (recent.length >= 2) {
      const a = recent[0], b = recent[recent.length - 1];
      if (b.t > a.t && now - b.t < 50) v = -(b.y - a.y) / (b.t - a.t);
    }
    v = clamp(v, -this.feel.maxFling, this.feel.maxFling);
    if (Math.abs(v) > 0.08 && !this.raw) {
      this.vel = v;
      this.mode = "fling";
    } else {
      this.mode = "idle";
    }
    this.setPinned(this.max - this.pos <= this.pinSlack && v >= 0);
    this.follow = this.pinnedState;
    this.kick();
  };

  // ------------------------------------------------------------------ кадр

  private kick() {
    if (this.raf) return;
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.frame);
  }

  private frame = (now: number) => {
    this.raf = 0;
    const dt = Math.min(48, Math.max(1, now - this.last));
    this.last = now;
    const max = this.max;
    let busy = false;

    if (this.mode === "wheel") {
      if (this.stick && this.follow && this.pinnedState) this.target = max;
      const k = 1 - Math.exp(-dt / Math.max(1, this.tau));
      this.pos += (this.target - this.pos) * k;
      if (Math.abs(this.target - this.pos) < 0.35) {
        this.pos = this.target;
        this.mode = "idle";
      } else busy = true;
    } else if (this.mode === "fling") {
      this.pos += this.vel * dt;
      this.vel *= Math.exp(-dt / this.feel.flingTau);
      if (this.pos < 0 || this.pos > max) {
        // Бросок доехал до края — упирается (Егор 28.09); лишь мягкая подушка, не отскок.
        if (this.overscroll !== "none") this.rawVel = this.vel * 0.12;
        this.pos = clamp(this.pos, 0, max);
        this.vel = 0;
        this.mode = "idle";
        if (this.pos >= max - 0.5) { this.setPinned(true); this.follow = true; }
      } else if (Math.abs(this.vel) < 0.015) {
        this.vel = 0;
        this.mode = "idle";
      } else busy = true;
    }

    if (this.mode === "drag") {
      // Перетяг ведёт указатель напрямую.
    } else if (this.pull) {
      // Пальцы держат оттяжку: показанный перетяг плавно идёт за ними; скорость
      // запоминается, чтобы возврат после подъёма пальцев начался без рывка.
      const prev = this.raw;
      this.raw += (this.pull - this.raw) * (1 - Math.exp(-dt / Math.max(1, this.feel.padTau)));
      this.rawVel = (this.raw - prev) / dt;
      if (Math.abs(this.pull - this.raw) > 0.2) busy = true;
    } else if (this.raw || this.rawVel) {
      // Возврат: критически затухающая пружина — без перелёта и без рывка.
      let t = dt;
      const w = this.feel.springW;
      while (t > 0) {
        const h = Math.min(4, t);
        const a = -w * w * this.raw - 2 * w * this.rawVel;
        this.rawVel += a * h;
        this.raw += this.rawVel * h;
        t -= h;
      }
      if (Math.abs(this.raw) < 0.3 && Math.abs(this.rawVel) < 0.005) {
        this.raw = 0;
        this.rawVel = 0;
      } else busy = true;
    }

    this.write();
    if (busy || this.mode === "wheel" || this.mode === "fling") this.kick();
  };

  private write() {
    const top = clamp(this.pos, 0, this.max);
    if (Math.abs(this.el.scrollTop - top) >= 0.5) this.el.scrollTop = top;
    this.written = this.el.scrollTop;
    this.paintOver();
  }

  /** Перетяг: резиновая кривая iOS — чем дальше тянешь, тем туже. */
  private paintOver() {
    const s = this.inner.style;
    const h = this.el.clientHeight || 1;
    const d = h * this.feel.rubberD;
    const x = Math.abs(this.raw);
    const shown = x ? (1 - 1 / ((x * this.feel.rubberC) / d + 1)) * d * Math.sign(this.raw) : 0;
    if (!shown || Math.abs(shown) < 0.2 || this.overscroll === "none") {
      if (s.transform) { s.transform = ""; s.transformOrigin = ""; }
      return;
    }
    if (this.overscroll === "rubber") {
      s.transformOrigin = "";
      s.transform = `translate3d(0, ${(-shown).toFixed(2)}px, 0)`;
    } else {
      // Растяжка: край окна неподвижен, содержимое вытягивается от него.
      const edge = shown < 0 ? this.el.scrollTop : this.el.scrollTop + h;
      const k = 1 + (Math.abs(shown) / h) * 0.35;
      s.transformOrigin = `50% ${edge.toFixed(1)}px`;
      s.transform = `scaleY(${k.toFixed(4)})`;
    }
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Щелчок колеса мыши, а не тачпад. Посреди жеста тачпада — всегда тачпад: первое событие
 * нового жеста у Егора бывало и −49 (журнал 28.09), это не щелчок.
 */
function isNotch(e: WheelEvent, dy: number, gap: number, pad: Pad): boolean {
  if (e.deltaMode !== 0) return true;
  if (pad !== "none" && gap <= STREAM_GAP) return false;
  const a = Math.abs(dy);
  if (a < 50) return false;
  const legacy = (e as WheelEvent & { wheelDeltaY?: number }).wheelDeltaY;
  if (typeof legacy === "number" && legacy !== 0 && Math.abs(legacy) % 120 === 0) return true;
  return a % 100 === 0 || a % 120 === 0;
}

/** Внутри есть свой прокручиваемый блок (код, таблица), и ему есть куда ехать? */
function innerScrollable(t: Element | null, root: HTMLElement, dy: number): boolean {
  for (let n = t; n && n !== root; n = n.parentElement) {
    if (!(n instanceof HTMLElement)) continue;
    if (n.scrollHeight <= n.clientHeight + 1) continue;
    const oy = getComputedStyle(n).overflowY;
    if (oy !== "auto" && oy !== "scroll") continue;
    if (dy < 0 ? n.scrollTop > 0 : n.scrollTop + n.clientHeight < n.scrollHeight - 1) return true;
  }
  return false;
}
