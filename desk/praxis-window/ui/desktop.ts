import "./desktop.css";

type MenuItem = { name: string; id: number; enabled: boolean };
type HostEvent = { kind: string; maximized?: boolean; menu?: string; items?: MenuItem[]; x?: number; y?: number };
interface Channel {
  postMessage(value: string): void;
  addEventListener(name: "message", callback: (event: MessageEvent<HostEvent>) => void): void;
}

export function mountDesktop() {
  const host: Channel | undefined = (window as Window & { praxisWindow?: Channel }).praxisWindow;
  if (!host) return;
  const doc = document.documentElement;
  doc.classList.add("native", "electron", "praxis-desktop");
  doc.style.setProperty("--praxis-mark", 'url("' + new URL("./praxis-mark.png", location.href).href + '")');
  const send = (action: string) => host.postMessage(action);
  for (const button of document.querySelectorAll<HTMLButtonElement>("#chrome .win[data-win]")) {
    const action = button.dataset.win!;
    if (action === "close") { button.ariaLabel = "Закрыть окно"; button.title = "Закрыть окно"; }
    button.onclick = () => send(action);
  }

  const labels: Record<string, string> = { cut: "Вырезать", copy: "Копировать", paste: "Вставить",
    selectAll: "Выделить всё", undo: "Отменить", redo: "Повторить", copyImage: "Копировать изображение",
    copyLinkAddress: "Копировать ссылку", copyLinkLocation: "Копировать ссылку" };
  const menu = document.createElement("div");
  menu.className = "menu text-menu";
  menu.setAttribute("role", "menu");
  menu.ariaLabel = "Действия";
  menu.hidden = true;
  document.body.append(menu);
  let ticket = "";
  let previousFocus: HTMLElement | null = null;
  function dismiss(command = -1) {
    if (!ticket) return;
    menu.hidden = true;
    previousFocus?.focus({ preventScroll: true });
    send("menu:" + ticket + ":" + command);
    ticket = "";
  }
  function item(text: string, enabled: boolean, act: () => void) {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.textContent = text;
    b.disabled = !enabled;
    b.onclick = act;
    menu.append(b);
  }
  host.addEventListener("message", ({ data }) => {
    if (data.kind === "state") {
      doc.classList.toggle("window-maximized", !!data.maximized);
      return;
    }
    if (data.kind !== "menu" || !data.menu) return;
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ticket = data.menu;
    menu.replaceChildren();
    for (const entry of data.items || []) {
      if (labels[entry.name]) item(labels[entry.name], entry.enabled, () => dismiss(entry.id));
    }
    item("Обновить окно", true, () => { dismiss(); location.reload(); });
    menu.hidden = false;
    menu.style.left = Math.max(4, Math.min(innerWidth * (data.x || 0), innerWidth - menu.offsetWidth - 5)) + "px";
    menu.style.top = Math.max(4, Math.min(innerHeight * (data.y || 0), innerHeight - menu.offsetHeight - 5)) + "px";
    menu.querySelector<HTMLButtonElement>("button:enabled")?.focus({ preventScroll: true });
  });
  menu.onpointerdown = e => e.preventDefault();
  menu.oncontextmenu = e => e.preventDefault();
  menu.onkeydown = e => {
    const buttons = Array.from(menu.querySelectorAll<HTMLButtonElement>("button:enabled"));
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape" || e.key === "Tab") dismiss();
    else if (e.key === "ArrowDown" || e.key === "ArrowUp")
      buttons[(i + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    else return;
    e.preventDefault();
  };
  document.addEventListener("pointerdown", e => { if (!menu.contains(e.target as Node)) dismiss(); }, true);
  window.addEventListener("blur", () => dismiss());
  window.addEventListener("resize", () => dismiss());
  send("state");
}
