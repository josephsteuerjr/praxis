// Прокрутка окна: одна физика на общий #view (все разделы) и отдельная на панель ходов.
// Движок — ui-kit/feed (28.09): плавное колесо, «тянуть и водить» мышью, резинка тачпада
// по фазе жеста, прилипание к низу только в чате, масштаб ленты Ctrl+колесом.
import { Scroller, type FeelName, type Overscroll } from "../feed/scroller";
import { Zoom } from "../feed/zoom";

let viewScroller: Scroller | null = null;
let viewZoom: Zoom | null = null;

/** Характер и край — из выбора владельца (настройки окна), по умолчанию то, что одобрил Егор. */
function prefs(): { feel: FeelName; over: Overscroll } {
  let feel: FeelName = "syrup";
  let over: Overscroll = "rubber";
  try {
    const f = localStorage.getItem("helene.feel");
    const o = localStorage.getItem("helene.overscroll");
    if (f === "brisk" || f === "smooth" || f === "syrup") feel = f;
    if (o === "rubber" || o === "stretch" || o === "none") over = o;
  } catch { /* хранилища нет — по умолчанию */ }
  return { feel, over };
}

/** Поставить физику на #view; страницы разделов живут внутри `inner`. */
export function mountView(view: HTMLElement, inner: HTMLElement): Scroller {
  const p = prefs();
  viewScroller = new Scroller(view, inner, { feel: p.feel, overscroll: p.over, stick: false });
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
  return new Scroller(el, inner, { feel: p.feel, overscroll: p.over, stick: false });
}
