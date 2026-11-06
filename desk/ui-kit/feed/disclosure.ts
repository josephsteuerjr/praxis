/** A disclosure owns one animation, including reversals and newly loaded content. */
type SlideOptions = { animate?: boolean; mutate?: () => void; done?: () => void };
type SlideState = { animation: Animation; overflow: string };
const active = new WeakMap<HTMLElement, SlideState>();
const dimensions = ['height', 'paddingTop', 'paddingBottom', 'marginTop', 'marginBottom',
  'borderTopWidth', 'borderBottomWidth'] as const;

function frame(el: HTMLElement): Record<string, string> {
  const style = getComputedStyle(el);
  return Object.fromEntries([...dimensions.map(key => [key, style[key]]), ['opacity', style.opacity]]);
}
const collapsed = Object.fromEntries([...dimensions.map(key => [key, '0px']), ['opacity', '0']]);

export function slide(el: HTMLElement, open: boolean, options: SlideOptions = {}) {
  const wasHidden = el.hidden;
  // Computed CSS sizes stay in local coordinates even inside a zoomed ancestor.
  const from = wasHidden ? collapsed : frame(el);
  const previous = active.get(el);
  const overflow = previous?.overflow ?? el.style.overflow;
  if (previous) {
    previous.animation.onfinish = previous.animation.oncancel = null;
    previous.animation.cancel();
    active.delete(el);
  }
  el.style.overflow = overflow;
  options.mutate?.();
  const still = options.animate === false || matchMedia('(prefers-reduced-motion: reduce)').matches
    || typeof el.animate !== 'function';
  if (still || (!open && wasHidden)) {
    el.hidden = !open;
    options.done?.();
    return;
  }
  el.hidden = false;
  const to = open ? frame(el) : collapsed;
  if (Object.keys(to).every(key => from[key] === to[key])) {
    el.hidden = !open;
    options.done?.();
    return;
  }
  el.style.overflow = 'clip';
  const animation = el.animate([from, to], {
    duration: open ? 380 : 260, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'both',
  });
  active.set(el, { animation, overflow });
  animation.onfinish = () => {
    if (active.get(el)?.animation !== animation) return;
    active.delete(el);
    // Hide before removing the collapsed keyframe; no full-height flash on close.
    el.hidden = !open;
    animation.onfinish = animation.oncancel = null;
    animation.cancel();
    el.style.overflow = overflow;
    options.done?.();
  };
  animation.oncancel = () => {
    if (active.get(el)?.animation !== animation) return;
    active.delete(el);
    el.style.overflow = overflow;
  };
}
