/**
 * Edge and corner resizing for the frameless pywebview window on Windows and Linux.
 *
 * pywebview's WinForms and Qt frameless windows have no native resize border, and WebView2
 * covers the whole client area, so the edges could not be dragged (reported on Windows 10 at
 * 2560×1080, 100%). Like pywebview's own window drag, these handles measure the pointer and
 * ask the shell for a new size; the opposite side stays fixed. macOS keeps its native border.
 */
export type Edge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export const EDGES: readonly Edge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
export const MIN_WIDTH = 640;
export const MIN_HEIGHT = 420;

export interface Start { width: number; height: number }

/** New outer size for a drag of (dx, dy) screen pixels from the start size. */
export function nextSize(edge: Edge, start: Start, dx: number, dy: number): { width: number; height: number } {
  let width = start.width;
  let height = start.height;
  if (edge.includes("e")) width += dx;
  if (edge.includes("w")) width -= dx;
  if (edge.includes("s")) height += dy;
  if (edge.includes("n")) height -= dy;
  return { width: Math.max(MIN_WIDTH, Math.round(width)), height: Math.max(MIN_HEIGHT, Math.round(height)) };
}

type Invoke = (command: string, args: Record<string, unknown>) => Promise<unknown>;

/** Adds the handles once. Requests are coalesced to one per animation frame. */
export function installNativeResize(invoke: Invoke, doc: Document = document): void {
  if (doc.querySelector(".native-resize")) return;
  for (const edge of EDGES) {
    const handle = doc.createElement("div");
    handle.className = `native-resize native-resize-${edge}`;
    handle.dataset.edge = edge;
    handle.setAttribute("aria-hidden", "true");
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      const view = doc.defaultView || window;
      const start: Start = { width: view.outerWidth, height: view.outerHeight };
      const origin = { x: event.screenX, y: event.screenY };
      let pending: { width: number; height: number } | null = null;
      let busy = false;
      let frame = 0;
      const flush = async () => {
        frame = 0;
        if (busy || !pending) return;
        const next = pending;
        pending = null;
        busy = true;
        try {
          await invoke("python_window", { action: "resize_edge", edge, width: next.width, height: next.height });
        } catch (error) {
          console.error(error);
        } finally {
          busy = false;
          if (pending && !frame) frame = view.requestAnimationFrame(() => void flush());
        }
      };
      const move = (moved: PointerEvent) => {
        pending = nextSize(edge, start, moved.screenX - origin.x, moved.screenY - origin.y);
        if (!frame) frame = view.requestAnimationFrame(() => void flush());
      };
      const end = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", end);
        handle.removeEventListener("pointercancel", end);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", end);
      handle.addEventListener("pointercancel", end);
    });
    doc.body.appendChild(handle);
  }
}
