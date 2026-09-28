// Прокрутка окна: одна физика на общий #view (все разделы) и отдельная на панель ходов.
// Движок — ui-kit/feed (28.09): плавное колесо, «тянуть и водить» мышью, резинка тачпада
// по фазе жеста, прилипание к низу только в чате, масштаб ленты Ctrl+колесом.
import { FEELS, Scroller, type FeelName, type Overscroll } from "../feed/scroller";
import { Zoom } from "../feed/zoom";

let viewScroller: Scroller | null = null;
let viewZoom: Zoom | null = null;
/** Все живые физики окна: #view и колонки — выбор «Движение»/«Край» в «Виде» меняет их разом. */
const all = new Set<Scroller>();

/** Характер и край — выбор владельца (look.ts ставит его до первой отрисовки). */
let physics: { feel: FeelName; over: Overscroll } = { feel: "syrup", over: "rubber" };

function prefs(): { feel: FeelName; over: Overscroll } {
  return physics;
}

/** Выбор в «Виде»: характер движения и край ленты — всем физикам окна сразу. */
export function setPhysics(feel: FeelName, over: Overscroll) {
  physics = { feel, over };
  for (const s of all) {
    if (!s.el.isConnected) { all.delete(s); continue; }
    s.feel = { ...FEELS[feel] };
    s.overscroll = over;
  }
}

/** Поставить физику на #view; страницы разделов живут внутри `inner`. */
export function mountView(view: HTMLElement, inner: HTMLElement): Scroller {
  const p = prefs();
  viewScroller = new Scroller(view, inner, { feel: p.feel, overscroll: p.over, stick: false });
  all.add(viewScroller);
  viewZoom = new Zoom(viewScroller, null, { storageKey: "helene.zoom" });
  return viewScroller;
}

/** Прокрутка #view (null — до mountView, например в стендах). */
export function view(): Scroller | null {
  return viewScroller;
}

/** Раздел сменился: лента чата липнет к низу и масштабируется, остальные — нет. */
export function sectionShown(talk: boolean, zoomBox: HTMLElement | null) {
  if (viewScroller) viewScroller.stick = talk;
  viewZoom?.setBox(talk ? zoomBox : null);
}

/** Правка содержимого #view без прыжка (или сразу, если физики нет). */
export function preserve(mutate: () => void) {
  if (viewScroller) viewScroller.preserve(mutate);
  else mutate();
}

/** Отдельная физика для прокручиваемой колонки (панель ходов). */
export function mountColumn(el: HTMLElement, inner: HTMLElement): Scroller {
  const p = prefs();
  const s = new Scroller(el, inner, { feel: p.feel, overscroll: p.over, stick: false });
  all.add(s);
  return s;
}
