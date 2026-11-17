/**
 * Edge and corner resizing for the frameless pywebview window on Windows and Linux.
 *
 * pywebview's WinForms and Qt frameless windows have no native resize border, and WebView2
 * covers the whole client area, so the edges could not be dragged (reported on Windows 10 at
 * 2560×1080, 100%). Like pywebview's own window drag, these handles measure the pointer and
 * ask the shell for a new size; the opposite side stays fixed. macOS keeps its native border.
 *
 * A maximized window has no edges to drag: the handles step aside (MAXIMIZED_CLASS on <html>),
 * so the scrollbar, the header and the close corner reach the very edge of the screen. The
 * owner found the 5px strips still eating the screen edge of a maximized window in 2.8.2.
 */
export type Edge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export const EDGES: readonly Edge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
/**
 * Platform names exactly as the python shell's bridge reports them
 * (`localharness/python_platform.desktop_identity`: "windows", not sys.platform's "win32").
 * 2.8.0/2.8.1 compared against "win32", so the handles were never installed on Windows.
 */
export const NATIVE_RESIZE_PLATFORMS: readonly string[] = ["windows", "linux"];
/**
 * Where the shell hands drags to the window manager (python_app.begin_native_drag): the
 * header and the edges start the system's own move/size loop instead of the renderer
 * streaming positions. 2.8.7, after "окно не успевает за курсором" (Tim, Windows 10).
 */
export const NATIVE_DRAG_PLATFORMS: readonly string[] = ["windows"];
/** Two presses on the header within this time and distance are a double click. */
export const DOUBLE_PRESS_MS = 500;
export const DOUBLE_PRESS_PX = 4;
export const MIN_WIDTH = 640;
export const MIN_HEIGHT = 420;
/** Set on <html> while the shell reports the window maximized. */
export const MAXIMIZED_CLASS = "window-maximized";

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

/**
 * Ask the shell whether the window is maximized and mark <html> with the answer.
 * Only an explicit `maximized: true` counts; a shell without the action leaves the handles.
 */
export async function syncMaximized(invoke: Invoke, doc: Document = document): Promise<boolean> {
  const state = await invoke("python_window", { action: "state" });
  const maximized = typeof state === "object" && state !== null
    && (state as { maximized?: unknown }).maximized === true;
  doc.documentElement.classList.toggle(MAXIMIZED_CLASS, maximized);
  return maximized;
}

/**
 * Adds the handles once. Requests are coalesced to one per animation frame. With `system`
 * (NATIVE_DRAG_PLATFORMS) a press hands the whole resize to the system's size loop instead.
 */
export function installNativeResize(invoke: Invoke, doc: Document = document, system = false): void {
  if (doc.querySelector(".native-resize")) return;
  const view = doc.defaultView || window;
  for (const edge of EDGES) {
    const handle = doc.createElement("div");
    handle.className = `native-resize native-resize-${edge}`;
    handle.dataset.edge = edge;
    handle.setAttribute("aria-hidden", "true");
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      if (doc.documentElement.classList.contains(MAXIMIZED_CLASS)) return;
      event.preventDefault();
      if (system) {
        // No pointer capture: the system loop takes the mouse from here.
        void invoke("python_window", { action: "begin_drag", edge }).catch((error) => console.error(error));
        return;
      }
      handle.setPointerCapture(event.pointerId);
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
  // Maximize and restore change the viewport, whichever way they happen (button, double
  // click, Win+Up/Down, the tray). The shell's own answer decides, not a size guess.
  const refresh = () => void syncMaximized(invoke, doc).catch((error) => console.error(error));
  let timer = 0;
  view.addEventListener("resize", () => {
    view.clearTimeout(timer);
    timer = view.setTimeout(refresh, 80);
  });
  refresh();
}

/**
 * Double click on the header's drag area maximizes or restores, as a native title bar does.
 * `selector` is the shell's drag region; only a direct hit counts, like the drag itself.
 */
export function installHeaderToggle(toggle: () => void, selector: string, doc: Document = document): void {
  doc.addEventListener("dblclick", (event) => {
    const target = event.target as Element | null;
    if (event.button !== 0 || !target || typeof target.matches !== "function" || !target.matches(selector)) return;
    event.preventDefault();
    toggle();
  });
}

/**
 * Windows: a press on the header's drag area starts the system move loop (the window follows
 * the cursor like any titled window). That loop takes the release, so the page never sees a
 * dblclick: a second press within DOUBLE_PRESS_MS / DOUBLE_PRESS_PX toggles maximize instead.
 * `selector` is the header's drag area; only a direct hit counts, as with the renderer drag.
 */
export function installNativeDrag(invoke: Invoke, toggle: () => void, selector: string,
                                  doc: Document = document, now: () => number = () => Date.now()): void {
  let last: { at: number; x: number; y: number } | null = null;
  doc.addEventListener("mousedown", (event) => {
    const target = event.target as Element | null;
    if (event.button !== 0 || !target || typeof target.matches !== "function" || !target.matches(selector)) return;
    event.preventDefault();
    const at = now();
    if (last && at - last.at <= DOUBLE_PRESS_MS
        && Math.abs(event.screenX - last.x) <= DOUBLE_PRESS_PX && Math.abs(event.screenY - last.y) <= DOUBLE_PRESS_PX) {
      last = null;
      toggle();
      return;
    }
    last = { at, x: event.screenX, y: event.screenY };
    void invoke("python_window", { action: "begin_drag", edge: "move" }).catch((error) => console.error(error));
  });
}
