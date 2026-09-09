// Словарь движения установщика. Одна метафора на всё: ветер.
//
// Вперёд — ветер дует слева направо: то, что уходит, уносит вправо, то, что
// приходит, приносит слева. Назад — ветер меняет сторону. Расстояние
// передаётся размытием: далёкое размыто, близкое резко.
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
  const from = -dir * (opts.distance ?? 72);
  return animate(
    els,
    { x: [from, 0], opacity: [0, 1], filter: ["blur(10px)", "blur(0px)"] },
    {
      duration: 1.35 * T,
      delay: stagger((opts.step ?? 0.12) * T, { startDelay: (opts.delay ?? 0) * T }),
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
  const to = dir * (opts.distance ?? 96);
  return animate(
    els,
    { x: [0, to], opacity: [1, 0], filter: ["blur(0px)", "blur(8px)"] },
    {
      duration: 0.85 * T,
      delay: stagger((opts.step ?? 0.06) * T, { startDelay: (opts.delay ?? 0) * T }),
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
