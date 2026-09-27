// Ключевой список: каждый элемент ленты создаётся ОДИН раз и дальше только правится.
//
// Корень дёрганья окна до 28.09 — `container.innerHTML = …` на каждое обновление: новый
// DOM целиком, прокрутка прыгает, анимации вставки нет. Здесь новый элемент вставляется
// на своё место и вырастает из нуля; прежние остаются теми же узлами.

export interface KeyedSpec<T> {
  key(item: T): string;
  create(item: T): HTMLElement;
  /** Править узел на месте; вызывается, только если `same` сказал «изменилось». */
  update(el: HTMLElement, item: T, prev: T): void;
  same?(a: T, b: T): boolean;
}

export interface SetOptions {
  /** Новые элементы вырастают плавно (первая отрисовка — без анимации). */
  animate?: boolean;
  /** Сколько новых появилось — для «новые ↓». */
  onAdded?(els: HTMLElement[]): void;
}

/** Время появления нового элемента — настраивается (кастомизация окна, 28.09). */
export const growTiming = { ms: 560 };

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export class KeyedList<T> {
  private nodes = new Map<string, { el: HTMLElement; item: T }>();

  constructor(
    readonly container: HTMLElement,
    private readonly spec: KeyedSpec<T>,
  ) {}

  get size(): number {
    return this.nodes.size;
  }

  element(key: string): HTMLElement | undefined {
    return this.nodes.get(key)?.el;
  }

  set(items: T[], opts: SetOptions = {}) {
    const same = this.spec.same ?? ((a: T, b: T) => a === b);
    const next = new Map<string, { el: HTMLElement; item: T }>();
    const order: HTMLElement[] = [];
    const added: HTMLElement[] = [];
    for (const item of items) {
      const k = this.spec.key(item);
      if (next.has(k)) continue; // дубль ключа — берём первый
      const had = this.nodes.get(k);
      if (had) {
        if (!same(had.item, item)) this.spec.update(had.el, item, had.item);
        next.set(k, { el: had.el, item });
        order.push(had.el);
      } else {
        const el = this.spec.create(item);
        el.dataset.key = k;
        next.set(k, { el, item });
        order.push(el);
        added.push(el);
      }
    }
    for (const [k, v] of this.nodes) if (!next.has(k)) v.el.remove();
    // Расставить по порядку, двигая только то, что стоит не на месте.
    let cur: ChildNode | null = this.container.firstChild;
    for (const el of order) {
      while (cur && !(cur instanceof HTMLElement && cur.dataset.key !== undefined)) cur = cur.nextSibling;
      if (cur === el) cur = el.nextSibling;
      else this.container.insertBefore(el, cur);
    }
    this.nodes = next;
    if (opts.animate && added.length) for (const el of added) grow(el);
    if (added.length) opts.onAdded?.(added);
  }
}

/** Вырасти из нуля: высота, прозрачность, лёгкий подъём. Соседи сверху едут плавно. */
export function grow(el: HTMLElement, ms = growTiming.ms) {
  if (ms <= 0 || reduced() || typeof el.animate !== "function") return;
  const h = el.offsetHeight;
  if (!h) return;
  el.style.overflow = "clip";
  const a = el.animate(
    [
      { height: "0px", opacity: 0, transform: "translateY(18px) scale(0.98)" },
      { height: h + "px", opacity: 0.7, transform: "translateY(5px) scale(0.995)", offset: 0.5 },
      { height: h + "px", opacity: 1, transform: "none" },
    ],
    { duration: ms, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
  );
  const done = () => { el.style.overflow = ""; };
  a.onfinish = done;
  a.oncancel = done;
}
