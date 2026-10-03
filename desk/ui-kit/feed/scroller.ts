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
  // Возврат и подхват — одна критическая пружина в ВИДИМЫХ пикселях.
  // springW задаёт время возврата, padTau — время следования за рукой.
  // Переключение цели не меняет положение или скорость мгновенно.
  // Из покоя критическая пружина проходит 95% за 4.74/w вместо 3/w у
  // прежней экспоненты: множитель 1.6 сохраняет длительность без резкого старта.
  brisk: { wheelTau: 95, padTau: 18, followTau: 110, flingTau: 325, maxFling: 7, springW: 6.4 / 320, rubberC: 0.5, rubberD: 0.24 },
  smooth: { wheelTau: 150, padTau: 30, followTau: 170, flingTau: 460, maxFling: 9, springW: 6.4 / 420, rubberC: 0.5, rubberD: 0.28 },
  syrup: { wheelTau: 210, padTau: 45, followTau: 240, flingTau: 620, maxFling: 10, springW: 6.4 / 520, rubberC: 0.5, rubberD: 0.32 },
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

type Mode = "idle" | "spring" | "drag" | "fling";
// wheel не сообщает контакт пальцев. Ни ноль, ни скорость не дают права
// отбрасывать следующий ввод или держать ленту восемь секунд.
const WHEEL_QUIET = 160;
const ZERO_QUIET = 90;
const EDGE_MEMORY = 420; // мс: слабый хвост не может хранить большую оттяжку
const IMPACT_SPEED = 1.1; // px/мс: быстрый вход из ленты в край
const IMPACT_INPUT = 0.12; // остаточная податливость края после удара
const IMPACT_MEMORY = 60; // мс: хвост не накачивает удар обратно в ручную тягу

export class Scroller {
  readonly el: HTMLElement;
  readonly inner: HTMLElement;
  feel: Feel;
  stick: boolean;
  private over: Overscroll;
  private readonly canGrab: (t: Element, x: number, y: number) => boolean;
  private readonly pinSlack: number;
  private readonly onPinnedChange?: (p: boolean) => void;

  // ОДНА координата и скорость в видимых px. Внутри [0,max] это scrollTop,
  // снаружи — scrollTop + видимая оттяжка. Переход через край непрерывен.
  private pos = 0;
  private target = 0;
  private vel = 0;
  private tau = 150;
  private returning = false;
  private impact = 0; // сторона поглощения: -1 верх, +1 низ; это физика, не фаза пальцев
  private mode: Mode = "idle";
  private raf = 0;
  private last = 0;
  private written = -1;
  private contentHeight = 0;
  private layoutMax = 0;
  private lastWheel = 0;
  private wheelDir = 0;
  private wheelActive = false;
  private notch = false;
  private releaseTimer = 0;
  private releaseAt = 0;
  private pinnedState = true;
  private follow = true;
  private drag: null | {
    id: number; y0: number; start: number; moved: boolean;
    samples: Array<{ t: number; y: number }>;
  } = null;
  private ro: ResizeObserver;

  constructor(el: HTMLElement, inner: HTMLElement, opts: ScrollerOptions = {}) {
    this.el = el; this.inner = inner;
    this.over = opts.overscroll ?? "rubber";
    this.feel = { ...FEELS[opts.feel ?? "syrup"] };
    this.stick = opts.stick ?? true;
    this.pinSlack = opts.pinSlack ?? 28;
    this.canGrab = opts.canGrab ?? ((t,x,y) => !t.closest(NO_GRAB) && !overText(x,y));
    this.onPinnedChange = opts.onPinnedChange;
    el.style.overflowAnchor = "none";
    this.measureContent();
    this.pos = this.target = el.scrollTop;
    el.addEventListener("wheel", this.onWheel, { passive: false });
    el.addEventListener("scroll", this.onScroll, { passive: true });
    el.addEventListener("pointerdown", this.onDown);
    el.addEventListener("pointermove", this.onMove);
    el.addEventListener("pointerup", this.onUp);
    el.addEventListener("pointercancel", this.onUp);
    el.addEventListener("lostpointercapture", this.onUp);
    window.addEventListener?.("pointerup", this.onUp, true);
    window.addEventListener?.("pointercancel", this.onUp, true);
    window.addEventListener?.("blur", this.onBlur);
    el.addEventListener("keydown", this.onKey);
    this.ro = new ResizeObserver(() => this.contentChanged());
    this.ro.observe(inner); this.ro.observe(el);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.clearRelease();
    this.endDrag();
    this.ro.disconnect();
    this.el.removeEventListener("wheel", this.onWheel);
    this.el.removeEventListener("scroll", this.onScroll);
    this.el.removeEventListener("pointerdown", this.onDown);
    this.el.removeEventListener("pointermove", this.onMove);
    this.el.removeEventListener("pointerup", this.onUp);
    this.el.removeEventListener("pointercancel", this.onUp);
    this.el.removeEventListener("lostpointercapture", this.onUp);
    window.removeEventListener?.("pointerup", this.onUp, true);
    window.removeEventListener?.("pointercancel", this.onUp, true);
    window.removeEventListener?.("blur", this.onBlur);
    this.el.removeEventListener("keydown", this.onKey);
  }

  get overscroll(): Overscroll { return this.over; }
  set overscroll(value: Overscroll) {
    this.over = value;
    if (value === "none") {
      this.impact = 0;
      this.pos = clamp(this.pos,0,this.max);
      this.target = clamp(this.target,0,this.max);
    }
    this.write();
  }
  get pinned(): boolean { return this.pinnedState; }
  private get max(): number {
    if (!this.inner.style.transform) this.contentHeight = this.el.scrollHeight;
    return Math.max(0,this.contentHeight-this.el.clientHeight);
  }
  private measureContent() {
    const transform = this.inner.style.transform;
    if (transform) this.inner.style.transform = "";
    this.contentHeight = this.el.scrollHeight;
    this.layoutMax = Math.max(0,this.contentHeight-this.el.clientHeight);
    if (transform) this.inner.style.transform = transform;
  }
  debug() {
    const round = (v: number) => Math.round(v*10)/10;
    const top = clamp(this.pos,0,this.max);
    const over = this.pos-top;
    return { pos: Math.round(top), max: Math.round(this.max), raw: round(over), shown: round(over),
      pull: round(this.unrubber(this.target-clamp(this.target,0,this.max))),
      // Совместимость журнала: это активный ввод/возврат, НЕ оценка контакта.
      pad: this.wheelActive ? "fingers" : this.returning ? "inertia" : "none", notch: this.notch };
  }

  toBottom(smooth = true) {
    this.measureContent();
    this.scrollTo(this.max,smooth);
  }
  scrollTo(y: number, smooth = true) {
    this.measureContent();
    this.clearRelease();
    this.endDrag();
    this.wheelActive = false; this.wheelDir = 0; this.lastWheel = 0;
    this.returning = false; this.impact = 0;
    this.target = clamp(y,0,this.max);
    this.setPinned(this.max-this.target <= this.pinSlack);
    this.follow = this.pinnedState;
    if (!smooth) {
      cancelAnimationFrame(this.raf); this.raf = 0;
      this.pos = this.target; this.vel = 0; this.mode = "idle";
      this.write();
    } else {
      this.mode = "spring"; this.tau = this.feel.followTau*1.2; this.kick();
    }
  }
  fling(v: number) {
    this.clearRelease(); this.wheelActive = false; this.returning = false; this.impact = 0;
    this.vel = clamp(v,-this.feel.maxFling,this.feel.maxFling);
    this.mode = "fling";
    if (this.pos < 0 || this.pos > this.max) {
      this.target = clamp(this.pos,0,this.max); this.returning = true; this.mode = "spring";
      this.impact = Math.sign(this.pos-this.target);
    }
    if (v < 0) { this.follow = false; this.setPinned(false); }
    this.kick();
  }
  shiftBy(dy: number) {
    if (!dy) return;
    this.pos = clamp(this.pos+dy,0,this.max);
    this.target = clamp(this.target+dy,0,this.max);
    this.write();
  }
  preserve(mutate: () => void) {
    if (this.stick && this.pinnedState) {
      mutate(); this.contentChanged(); return;
    }
    const anchor = this.anchor();
    const previousMax = this.layoutMax;
    mutate();
    this.measureContent();
    if (anchor && anchor.el.isConnected) this.shiftBy(anchor.el.getBoundingClientRect().top-anchor.top);
    this.contentChanged(previousMax);
  }
  private anchor(): { el: Element; top: number } | null {
    const box = this.el.getBoundingClientRect();
    const walk = (parent: Element): { el: Element; top: number } | null => {
      for (const child of Array.from(parent.children)) {
        const r = child.getBoundingClientRect();
        if (r.bottom > box.top+1) {
          if (r.top < box.top && child.children.length) {
            const deeper = walk(child); if (deeper) return deeper;
          }
          return {el:child,top:r.top};
        }
      }
      return null;
    };
    return walk(this.inner);
  }
  private setPinned(p: boolean) {
    if (p === this.pinnedState) return;
    this.pinnedState = p; this.onPinnedChange?.(p);
  }
  private contentChanged(oldMax = this.layoutMax) {
    this.measureContent();
    const max = this.max;
    if (this.stick && this.pinnedState && this.follow && !this.drag && !this.wheelActive) {
      this.target = max; this.mode = "spring"; this.tau = this.feel.followTau; this.kick();
    } else if (max !== oldMax && !this.drag) {
      // Геометрия сменилась: старый низ больше не может быть целью пружины.
      this.target = clamp(this.target,0,max);
      if (this.pos > max && !this.wheelActive) this.pos = max;
      this.write(); this.kick();
    }
  }

  private rubber(x: number): number {
    const d = Math.max(1,this.el.clientHeight)*this.feel.rubberD;
    const a = Math.abs(x);
    return a ? d*a*this.feel.rubberC/(d+a*this.feel.rubberC)*Math.sign(x) : 0;
  }
  private unrubber(y: number): number {
    const d = Math.max(1,this.el.clientHeight)*this.feel.rubberD;
    const a = Math.min(Math.abs(y),d*0.75);
    return a ? a*d/((d-a)*this.feel.rubberC)*Math.sign(y) : 0;
  }
  private limitOver(y: number): number {
    const max = this.max, edge = clamp(y,0,max);
    if (this.over === "none") return edge;
    const limit = Math.max(1,this.el.clientHeight)*this.feel.rubberD*0.75;
    return edge+clamp(y-edge,-limit,limit);
  }
  private fromInput(y: number): number {
    const edge = clamp(y,0,this.max);
    return this.limitOver(edge+this.rubber(y-edge));
  }

  private onWheel = (e: WheelEvent) => {
    if (e.ctrlKey || e.defaultPrevented || !Number.isFinite(e.deltaY) || !Number.isFinite(e.deltaX)) return;
    if (!e.deltaY && !e.deltaX) {
      if (this.wheelActive) this.armRelease(ZERO_QUIET,false);
      return;
    }
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 40;
    else if (e.deltaMode === 2) dy *= this.el.clientHeight;
    if (!dy || Math.abs(e.deltaX) > Math.abs(dy)) return;
    if (innerScrollable(e.target as Element,this.el,dy)) return;
    e.preventDefault();
    const now = performance.now(), gap = now-this.lastWheel, dir = Math.sign(dy);
    this.lastWheel = now;
    this.notch = e.deltaMode !== 0 || (gap > WHEEL_QUIET && (Math.abs(dy)%120 === 0 || Math.abs(dy)%100 === 0));
    this.endDrag();
    // Новое движение берёт текущую координату. Смена направления не должна
    // отрабатывать невидимую очередь старой поездки; скорость меняется пружиной.
    // Край уже тянет ленту к своей цели, а новое усилие направлено обратно:
    // это подхват текущего положения даже внутри непрерывного wheel-потока.
    // Не ждать тишины/смены знака/окончания возврата и не менять скорость.
    const pickup = this.impact && dir*(this.target-this.pos) < 0;
    if (!this.wheelActive || (this.wheelDir && dir !== this.wheelDir) || pickup) {
      this.target = this.pos; this.impact = 0;
    }
    this.wheelDir = dir; this.wheelActive = true; this.returning = false;
    this.mode = "spring";
    this.tau = this.notch ? this.feel.wheelTau : this.feel.padTau;
    const max = this.max;
    const previousTarget = this.target;
    let base = this.target;
    const edge = clamp(base,0,max), over = base-edge;
    if (over && dir === Math.sign(over)) {
      // Наружу — упругая кривая; обратно — видимые пиксели один к одному.
      base = edge+this.unrubber(over);
      this.target = this.fromInput(base+dy);
    } else {
      const next = base+dy;
      this.target = over && Math.sign(next-edge) === Math.sign(over)
        ? next : this.fromInput(next);
    }
    // Вход не выбрасывается. При ударе та же сила вызывает лишь небольшую
    // деформацию. Обратный/новый жест выше сразу возвращает обычную податливость.
    if (this.impact === dir) this.target = previousTarget+(this.target-previousTarget)*IMPACT_INPUT;
    const pending = Math.max(1,this.el.clientHeight)*1.5;
    this.target = this.limitOver(clamp(this.target,this.pos-pending,this.pos+pending));
    if (dir < 0) { this.follow = false; this.setPinned(max-clamp(this.target,0,max) <= this.pinSlack); }
    else { this.setPinned(max-clamp(this.target,0,max) <= this.pinSlack); this.follow = this.pinnedState; }
    this.armRelease(WHEEL_QUIET,true);
    this.kick();
  };
  private clearRelease() {
    clearTimeout(this.releaseTimer); this.releaseTimer = 0; this.releaseAt = 0;
  }
  private armRelease(delay: number, replace: boolean) {
    const at = performance.now()+delay;
    if (!replace && this.releaseTimer && this.releaseAt <= at) return;
    this.clearRelease(); this.releaseAt = at;
    this.releaseTimer = window.setTimeout(() => {
      this.releaseTimer = 0; this.releaseAt = 0; this.wheelActive = false; this.release();
    },delay);
  }
  private release() {
    this.clearRelease();
    this.wheelActive = false;
    const bounded = clamp(this.target,0,this.max);
    this.returning = bounded !== this.target || this.pos !== clamp(this.pos,0,this.max);
    this.target = bounded;
    this.mode = "spring";
    this.kick();
  }
  private onScroll = () => {
    const top = this.el.scrollTop;
    if (Math.abs(top-this.written) <= 1.5) return;
    if (this.drag) return;
    // Полоса, поиск, native touch: новый явный источник позиции отменяет очередь.
    this.clearRelease(); this.wheelActive = false; this.returning = false; this.impact = 0;
    this.pos = this.target = top; this.vel = 0; this.mode = "idle";
    this.written = top; this.paintOver();
    this.setPinned(this.max-top <= this.pinSlack); this.follow = this.pinnedState;
  };
  private onKey = (e: KeyboardEvent) => {
    if (e.target !== this.el) return;
    if (e.key === "End") {e.preventDefault();this.toBottom();return;}
    if (e.key === "Home") {e.preventDefault();this.scrollTo(0);return;}
    const page = this.el.clientHeight*0.85;
    const steps: Record<string,number> = {ArrowDown:64,ArrowUp:-64,PageDown:page,PageUp:-page," ":e.shiftKey?-page:page};
    const dy = steps[e.key]; if (dy == null) return;
    e.preventDefault(); this.scrollTo(this.target+dy);
  };
  private endDrag() {
    const d = this.drag; this.drag = null; this.el.classList.remove("grabbing");
    if (d && this.el.hasPointerCapture(d.id)) this.el.releasePointerCapture(d.id);
  }
  private onDown = (e: PointerEvent) => {
    if (e.pointerType === "touch" || (e.button !== 0 && e.button !== 1)) return;
    if (e.button !== 1 && !e.altKey && !this.canGrab(e.target as Element,e.clientX,e.clientY)) return;
    if (e.clientX > this.el.getBoundingClientRect().left+this.el.clientWidth) return;
    e.preventDefault(); this.clearRelease(); this.wheelActive = false;
    const edge = clamp(this.pos,0,this.max);
    this.drag = {id:e.pointerId,y0:e.clientY,start:edge+this.unrubber(this.pos-edge),moved:false,samples:[{t:e.timeStamp,y:e.clientY}]};
    this.target = this.pos; this.returning = false; this.impact = 0; this.mode = "drag";
    this.el.setPointerCapture(e.pointerId);
    this.el.focus({preventScroll:true});
    this.kick();
  };
  private onMove = (e: PointerEvent) => {
    const d = this.drag; if (!d || e.pointerId !== d.id) return;
    // Потерянный pointerup нельзя превращать в вечный захват.
    if (e.buttons === 0) {this.onUp(e);return;}
    const dy = e.clientY-d.y0;
    if (!d.moved && Math.abs(dy) < 4) return;
    d.moved = true; this.el.classList.add("grabbing");
    this.target = this.fromInput(d.start-dy);
    if (dy > 0) {this.follow = false;this.setPinned(this.max-clamp(this.target,0,this.max) <= this.pinSlack);}
    d.samples.push({t:e.timeStamp,y:e.clientY});
    if (d.samples.length > 12) d.samples.shift();
    this.kick();
  };
  private onUp = (e: PointerEvent) => {
    const d = this.drag; if (!d || e.pointerId !== d.id) return;
    this.endDrag();
    let v = 0;
    const recent = d.samples.filter(s=>e.timeStamp-s.t <= 90);
    if (d.moved && e.type === "pointerup" && recent.length >= 2) {
      const a = recent[0], b = recent[recent.length-1];
      if (b.t > a.t && e.timeStamp-b.t < 50) v = -(b.y-a.y)/(b.t-a.t);
    }
    if (d.moved) {
      const eat = (ev: Event) => {ev.stopPropagation();ev.preventDefault();};
      window.addEventListener("click",eat,{capture:true,once:true});
      setTimeout(()=>window.removeEventListener("click",eat,{capture:true}),0);
    }
    if (Math.abs(v) > 0.08 && this.pos >= 0 && this.pos <= this.max && this.target >= 0 && this.target <= this.max) this.fling(v);
    else this.release();
    this.setPinned(this.max-clamp(this.pos,0,this.max) <= this.pinSlack && v >= 0);
    this.follow = this.pinnedState;
  };
  private onBlur = () => {this.endDrag();this.release();};

  private kick() {
    if (this.raf) return;
    this.last = performance.now(); this.raf = requestAnimationFrame(this.frame);
  }
  private frame = (now: number) => {
    this.raf = 0;
    const dt = Math.min(48,Math.max(1,now-this.last)); this.last = now;
    const max = this.max;
    // Короткие подшаги разрешают непрерывный вход в вязкий край при 60/120 Гц.
    // Внутри ленты пружина по-прежнему решается аналитически.
    for (let remaining=dt;remaining>0;) {
      const step = Math.min(2,remaining); remaining -= step;
      this.advanceMotion(step,max);
    }
    if (this.mode !== "fling" && Math.abs(this.target-this.pos) < 0.35 && Math.abs(this.vel) < 0.005) {
      this.pos = this.target; this.vel = 0; this.returning = false;
      if (this.pos >= 0 && this.pos <= max) this.impact = 0;
      if (!this.drag) this.mode = "idle";
    }
    this.write();
    if (this.mode === "fling" || (this.mode !== "idle" && (this.pos !== this.target || this.vel))) this.kick();
  };
  private advanceMotion(step: number, max: number) {
    if (this.wheelActive && !this.drag) {
      // Wheel несёт приращения, а не абсолютное положение руки. За границей
      // накопленная цель теряет энергию: поток микродельт не удержит большой
      // перетяг. Настоящий pointer drag задаёт положение и этой утечки не имеет.
      const edge = clamp(this.target,0,max);
      this.target = edge+(this.target-edge)*Math.exp(-step/(this.impact ? IMPACT_MEMORY : EDGE_MEMORY));
    }
    if (this.mode === "fling") {
      const decay = Math.exp(-step/this.feel.flingTau);
      const next = this.pos+this.vel*this.feel.flingTau*(1-decay);
      this.vel *= decay;
      if (next < 0 || next > max) {
        this.pos = this.limitOver(next); this.target = clamp(next,0,max);
        this.returning = true; this.mode = "spring";
        this.impact = this.over === "none" ? 0 : Math.sign(next-this.target);
        if (this.over === "none") {this.vel=0;this.mode="idle";}
        if (next > max) {this.setPinned(true);this.follow=true;}
      } else {
        this.pos = this.target = next;
        if (Math.abs(this.vel) < 0.015) {this.vel = 0;this.mode = "idle";}
      }
    } else if (this.mode !== "idle") {
      const w = this.returning || this.impact ? this.feel.springW : 2/Math.max(1,this.drag ? this.feel.padTau : this.tau);
      const before = this.pos;
      // Вязкость растёт гладко в первых 24px продавливания. Она снимает
      // исходящий импульс за время, а не присваивает скорости долю на границе.
      // При расправлении остаётся критическая пружина: остаточный ход тихий.
      const depth = Math.abs(this.pos-clamp(this.pos,0,max));
      const u = clamp(depth/24,0,1);
      const viscosity = this.impact*this.vel > 0 ? 0.16*u*u*(3-2*u) : 0;
      const loss = Math.exp(-viscosity*step/2);
      const [next,speed] = damp(this.pos,this.vel*loss,this.target,w,step);
      const travel = this.feel.maxFling*step;
      this.pos = this.limitOver(this.pos+clamp(next-this.pos,-travel,travel));
      this.vel = clamp(speed*loss,-this.feel.maxFling,this.feel.maxFling);
      if (!this.drag && !this.returning && !this.impact && before >= 0 && before <= max && this.over !== "none") {
        const side = Math.sign(this.pos-clamp(this.pos,0,max));
        if (side*this.vel > IMPACT_SPEED) {
          this.impact = side;
          const edge = side < 0 ? 0 : max;
          this.target = edge+(this.target-edge)*IMPACT_INPUT;
        }
      }
      // Возврат к границе не перескакивает через неё из-за остаточной скорости.
      if (this.returning && (before-this.target)*(this.pos-this.target) < 0) {this.pos=this.target;this.vel=0;}
    }
  }
  private write() {
    const top = clamp(this.pos,0,this.max);
    if (this.el.scrollTop !== top) this.el.scrollTop = top;
    this.written = this.el.scrollTop; this.paintOver();
  }
  private paintOver() {
    const s = this.inner.style, h = this.el.clientHeight || 1;
    const shown = this.pos-clamp(this.pos,0,this.max);
    if (!shown || Math.abs(shown) < 0.2 || this.over === "none") {
      if (s.transform) {s.transform = "";s.transformOrigin = "";}
    } else if (this.over === "rubber") {
      s.transformOrigin = "";
      s.transform = `translate3d(0, ${(-shown).toFixed(2)}px, 0)`;
    } else {
      const edge = shown < 0 ? this.el.scrollTop : this.el.scrollTop+h;
      s.transformOrigin = `50% ${edge.toFixed(1)}px`;
      s.transform = `scaleY(${(1+Math.abs(shown)/h*0.35).toFixed(4)})`;
    }
  }
}
function clamp(v: number, lo: number, hi: number): number {return v < lo ? lo : v > hi ? hi : v;}
function damp(x: number, v: number, goal: number, w: number, dt: number): [number,number] {
  const offset=x-goal,c=v+w*offset,decay=Math.exp(-w*dt);
  return [goal+(offset+c*dt)*decay,(v-w*c*dt)*decay];
}
/** Вложенная прокрутка получает событие, пока ей есть куда двигаться. */
function innerScrollable(t: Element | null, root: HTMLElement, dy: number): boolean {
  for (let n=t;n && n !== root;n=n.parentElement) {
    if (!(n instanceof HTMLElement) || n.scrollHeight <= n.clientHeight+1) continue;
    const oy=getComputedStyle(n).overflowY;
    if (oy !== "auto" && oy !== "scroll") continue;
    if (dy < 0 ? n.scrollTop > 0 : n.scrollTop+n.clientHeight < n.scrollHeight-1) return true;
  }
  return false;
}
