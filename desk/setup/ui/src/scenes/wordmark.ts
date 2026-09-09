// Сцена 1: надпись. Проявляется из размытия, держится, потом ветер срывает
// буквы по одной — слева направо, с подъёмом, наклоном и размытием — и гасит.
import { animate } from "motion";
import { EASE, T, rand, settle, splitGraphemes, type Dir } from "../wind";

type Controls = ReturnType<typeof animate>;
type State = "idle" | "appearing" | "settled" | "blowing" | "gone";

export class WordmarkScene {
  readonly root: HTMLElement;
  state: State = "idle";
  private glyphs: HTMLElement[];
  private live: Controls[] = [];

  constructor(root: HTMLElement, text: string) {
    this.root = root;
    const h1 = root.querySelector<HTMLElement>(".wordmark");
    if (!h1) throw new Error("нет .wordmark");
    h1.setAttribute("aria-label", text);
    h1.replaceChildren(
      ...splitGraphemes(text).map((ch) => {
        const s = document.createElement("span");
        s.className = "g";
        s.textContent = ch;
        s.setAttribute("aria-hidden", "true");
        return s;
      }),
    );
    this.glyphs = [...h1.querySelectorAll<HTMLElement>(".g")];
  }

  private track(c: Controls): Promise<unknown> {
    this.live.push(c);
    return c.finished;
  }

  private cancelLive() {
    for (const c of this.live) c.cancel();
    this.live = [];
  }

  /** Все буквы на месте, резко, без остаточных стилей. */
  settle() {
    for (const g of this.glyphs) settle(g, true);
  }

  /** Первый показ: буквы проявляются слева направо. true — досидели до конца. */
  async appear(): Promise<boolean> {
    this.root.hidden = false;
    this.state = "appearing";
    await Promise.all(
      this.glyphs.map((g, i) =>
        this.track(
          animate(
            g,
            {
              opacity: [0, 1],
              y: [22, 0],
              scale: [0.98, 1],
              filter: ["blur(16px)", "blur(0px)"],
            },
            { duration: 1.7 * T, delay: (0.35 + i * 0.085) * T, ease: EASE.out },
          ),
        ),
      ),
    );
    if (this.state !== "appearing") return false;
    this.state = "settled";
    this.settle();
    return true;
  }

  /** Ветер: срывает с той стороны, откуда дует, уносит туда, куда дует. */
  async leave(dir: Dir): Promise<void> {
    this.cancelLive();
    this.settle();
    this.state = "blowing";
    const n = this.glyphs.length;
    await Promise.all(
      this.glyphs.map((g, i) => {
        const k = dir > 0 ? i : n - 1 - i;
        const drift = dir * rand(260, 520);
        const lift = -rand(24, 96);
        const rot = rand(-16, 16);
        const skew = -dir * rand(6, 14);
        return this.track(
          animate(
            g,
            {
              x: [0, drift * 0.1, drift],
              y: [0, lift * 0.3, lift],
              rotate: [0, rot * 0.35, rot],
              skewX: [0, skew, skew * 0.25],
              scale: [1, 1, 0.93],
              opacity: [1, 0.92, 0],
              filter: ["blur(0px)", "blur(1.5px)", "blur(14px)"],
            },
            {
              duration: 1.75 * T,
              delay: (k * 0.06 + rand(0, 0.07)) * T,
              ease: EASE.in,
              times: [0, 0.32, 1],
            },
          ),
        );
      }),
    );
    this.state = "gone";
    this.root.hidden = true;
  }

  /** Возврат: буквы слетаются против ветра и успокаиваются. Дальше — руками. */
  async enter(dir: Dir): Promise<void> {
    this.cancelLive();
    this.root.hidden = false;
    this.state = "appearing";
    const n = this.glyphs.length;
    await Promise.all(
      this.glyphs.map((g, i) => {
        const k = dir > 0 ? i : n - 1 - i;
        const from = -dir * rand(220, 420);
        const lift = -rand(20, 70);
        const rot = rand(-10, 10);
        return this.track(
          animate(
            g,
            {
              x: [from, 0],
              y: [lift, 0],
              rotate: [rot, 0],
              opacity: [0, 1],
              filter: ["blur(12px)", "blur(0px)"],
            },
            { duration: 1.5 * T, delay: (0.1 + k * 0.05) * T, ease: EASE.out },
          ),
        );
      }),
    );
    if (this.state === "appearing") {
      this.state = "settled";
      this.settle();
    }
  }
}
