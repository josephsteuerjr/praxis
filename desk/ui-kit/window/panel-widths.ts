// User-owned panel geometry. No task state or scroll writes belong here.
export function boundedWidth(value: number, available: number): number {
  return Math.round(Math.min(Math.max(220, available), Math.max(220, value)));
}

export function mountPanelWidths(app: HTMLElement): () => void {
  const cleanup: Array<() => void> = [];
  for (const side of ["rail", "panel"] as const) {
    const pane = app.querySelector<HTMLElement>(`#${side}`);
    if (!pane) continue;
    const key = `frame.${side}.width`;
    const variable = `--${side}-w`;
    const handle = document.createElement("div");
    handle.className = `panel-resize panel-resize-${side}`;
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", side === "rail" ? "Ширина списка чатов" : "Ширина истории ходов");
    app.append(handle);
    let desired = parseFloat(getComputedStyle(app).getPropertyValue(variable));
    if (!Number.isFinite(desired)) desired = side === "rail" ? 300 : 380;
    try {
      const saved = Number(localStorage.getItem(key));
      if (Number.isFinite(saved) && saved >= 220) desired = saved;
    } catch { /* Browser storage may be unavailable. Geometry still works. */ }
    const apply = () => {
      const other = app.querySelector<HTMLElement>(side === "rail" ? "#panel" : "#rail");
      const otherSide = side === "rail" ? "panel" : "rail";
      const occupied = app.classList.contains(`${otherSide}-collapsed`) || (otherSide === "panel" && !app.classList.contains("with-panel")) ? 0 : (other?.getBoundingClientRect().width || 0);
      const maximum = Math.max(220, Math.min(700, app.clientWidth - occupied - 320));
      const width = boundedWidth(desired, maximum);
      app.style.setProperty(variable, `${width}px`);
      handle.setAttribute("aria-valuemin", "220");
      handle.setAttribute("aria-valuemax", String(maximum));
      handle.setAttribute("aria-valuenow", String(width));
    };
    const save = () => { try { localStorage.setItem(key, String(desired)); } catch { /* transient only */ } };
    let drag: { id: number; x: number; width: number } | null = null;
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      drag = { id: e.pointerId, x: e.clientX, width: pane.getBoundingClientRect().width };
      handle.setPointerCapture(e.pointerId);
      app.classList.add("resizing-panels");
    };
    const move = (e: PointerEvent) => {
      if (!drag || drag.id !== e.pointerId) return;
      desired = Math.min(700, Math.max(220, drag.width + (e.clientX - drag.x) * (side === "rail" ? 1 : -1)));
      apply();
    };
    const end = () => {
      if (!drag) return;
      const id = drag.id;
      drag = null;
      if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
      app.classList.remove("resizing-panels");
      desired = Number(handle.getAttribute("aria-valuenow"));
      save();
    };
    const keydown = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      apply(); // The opposite pane may have changed since this handle was last used.
      const current = Number(handle.getAttribute("aria-valuenow"));
      const maximum = Number(handle.getAttribute("aria-valuemax"));
      desired = e.key === "Home" ? 220 : e.key === "End" ? maximum : boundedWidth(current + (e.key === "ArrowRight" ? 20 : -20) * (side === "rail" ? 1 : -1), maximum);
      apply(); save();
    };
    handle.addEventListener("pointerdown", down);
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    handle.addEventListener("lostpointercapture", end);
    handle.addEventListener("keydown", keydown);
    handle.addEventListener("focus", apply);
    window.addEventListener("resize", apply);
    const observer = new MutationObserver(apply);
    observer.observe(app, { attributes: true, attributeFilter: ["class"] });
    cleanup.push(() => observer.disconnect());
    apply();
    cleanup.push(() => {
      end(); window.removeEventListener("resize", apply);
      handle.removeEventListener("pointerdown", down);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      handle.removeEventListener("lostpointercapture", end);
      handle.removeEventListener("keydown", keydown);
      handle.removeEventListener("focus", apply);
      handle.remove();
    });
  }
  return () => cleanup.forEach(fn => fn());
}
