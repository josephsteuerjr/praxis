// Словарь движения установщика. Была метафора «ветер» — долгие уносы с размытием;
// 28.09 Егор: «визарды остопиздели ужасно». Осталось направление (вперёд — слева
// направо), но коротко и тихо: полсекунды, без размытия, небольшой сдвиг.
import { animate, stagger } from "motion";

export type Dir = 1 | -1;

export const EASE = {
  // Проявление: быстро из размытия, потом долго успокаивается.
  out: [0.16, 1, 0.3, 1] as [number, number, number, number],
  // Отрыв: медленно начинается, потом уносит.
  in: [0.55, 0, 0.9, 0.35] as [number, number, number, number],
  soft: [0.4, 0, 0.2, 1] as [number, number, number, number],
};

export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
// Множитель длительностей: при «уменьшить движение» сцена не исчезает,
// а становится короче.
export const T = reduced ? 0.4 : 1;

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export const rand = (a: number, b: number) => a + Math.random() * (b - a);

export function splitGraphemes(text: string): string[] {
  const seg = new Intl.Segmenter("ru", { granularity: "grapheme" });
  return [...seg.segment(text)].map((s) => s.segment);
}

type Controls = ReturnType<typeof animate>;

/** Блоки приходят с той стороны, откуда дует. */
export function enterWithWind(
  els: Element[],
  dir: Dir,
  opts: { delay?: number; step?: number; distance?: number } = {},
): Controls {
  const from = -dir * Math.min(opts.distance ?? 24, 24);
  return animate(
    els,
    { x: [from, 0], opacity: [0, 1] },
    {
      duration: 0.42 * T,
      delay: stagger(Math.min(opts.step ?? 0.04, 0.04) * T, { startDelay: (opts.delay ?? 0) * T * 0.4 }),
      ease: EASE.out,
    },
  );
}

/** Блоки уносит туда, куда дует. */
export function leaveWithWind(
  els: Element[],
  dir: Dir,
  opts: { delay?: number; step?: number; distance?: number } = {},
): Controls {
  const to = dir * Math.min(opts.distance ?? 20, 20);
  return animate(
    els,
    { x: [0, to], opacity: [1, 0] },
    {
      duration: 0.22 * T,
      delay: stagger(Math.min(opts.step ?? 0.02, 0.02) * T, { startDelay: (opts.delay ?? 0) * T * 0.4 }),
      ease: EASE.in,
    },
  );
}

/** Снять анимации и зафиксировать конечное состояние блока. */
export function settle(el: HTMLElement, visible: boolean) {
  el.style.opacity = visible ? "1" : "0";
  el.style.transform = "none";
  el.style.filter = "none";
}
