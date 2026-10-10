// Меню текста принадлежит окну: WebView не должен рисовать поверх бумаги
// собственный список «Печать / Другие инструменты». Поля редактирования
// сохраняют своё меню (вставка, проверка орфографии и отмена).
import { copyText, el, toast } from "../dom";
import { clientIsMac, kbdLabel } from "../platform";

export function mountTextMenu(root: HTMLElement): () => void {
  const abort = new AbortController();
  const { signal } = abort;
  const menu = el("div", "menu text-menu");
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", "Действия с текстом");
  document.body.append(menu);
  let previousFocus: HTMLElement | null = null;
  let ranges: Range[] = [];
  const copyKey = kbdLabel("Ctrl+C", clientIsMac(navigator));

  function restoreSelection(): void {
    const selection = window.getSelection();
    if (!selection || !ranges.length) return;
    selection.removeAllRanges();
    for (const range of ranges) {
      if (range.startContainer.isConnected && range.endContainer.isConnected) selection.addRange(range);
    }
  }

  function close(refocus = false): void {
    if (menu.hidden) return;
    menu.hidden = true;
    restoreSelection();
    if (refocus && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    ranges = [];
  }

  function item(label: string, key: string, act: () => void): void {
    const button = el("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.append(el("span", "", label));
    if (key) button.append(el("span", "text-menu-key", key));
    button.addEventListener("click", act, { signal });
    menu.append(button);
  }

  document.addEventListener("contextmenu", (event) => {
    let target = event.target instanceof Element ? event.target : null;
    if (!target || target.closest("input, textarea, [contenteditable]:not([contenteditable='false']), .menu")) return;
    const selection = window.getSelection();
    const selected = selection?.toString() || "";
    const inRoot = !!selection?.anchorNode && !!selection.focusNode
      && root.contains(selection.anchorNode) && root.contains(selection.focusNode);
    // Shift+F10 может прийти на body: выделение остаётся в переписке.
    if (!root.contains(target)) {
      if (event.clientX || event.clientY || !inRoot) return;
      target = selection!.focusNode instanceof Element ? selection!.focusNode : selection!.focusNode!.parentElement;
      if (!target) return;
    }
    const message = target.closest<HTMLElement>(".msg-body");
    const text = inRoot && selected.trim() ? selected : message?.innerText || "";
    if (!text.trim()) return;
    event.preventDefault();
    event.stopPropagation();
    close();
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ranges = inRoot && selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
    menu.replaceChildren();
    item(inRoot && selected.trim() ? "Копировать" : "Копировать сообщение", copyKey, () => {
      close(true);
      void copyText(text).then(ok => toast(ok ? "Скопировано" : "Скопировать не вышло — нажми " + copyKey));
    });
    if (message) item("Выделить сообщение", "", () => {
      close(true);
      const range = document.createRange();
      range.selectNodeContents(message);
      const selected = window.getSelection();
      selected?.removeAllRanges();
      selected?.addRange(range);
    });
    menu.hidden = false;
    const rect = selection?.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : target.getBoundingClientRect();
    const x = event.clientX || rect.left;
    const y = event.clientY || rect.bottom;
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - menu.offsetHeight - 8)) + "px";
    menu.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
  }, { signal });

  menu.addEventListener("pointerdown", event => event.preventDefault(), { signal });
  menu.addEventListener("keydown", (event) => {
    const buttons = Array.from(menu.querySelectorAll<HTMLButtonElement>("button"));
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (event.key.toLowerCase() === "c" && (event.ctrlKey || event.metaKey) && !event.altKey) {
      event.preventDefault();
      buttons[0]?.click();
      return;
    }
    if (event.key === "Escape") close(true);
    else if (event.key === "ArrowDown") next = (at + 1) % buttons.length;
    else if (event.key === "ArrowUp") next = (at + buttons.length - 1) % buttons.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = buttons.length - 1;
    else if (event.key === "Tab") close(true);
    else return;
    event.preventDefault();
    if (next >= 0) buttons[next].focus({ preventScroll: true });
  }, { signal });
  document.addEventListener("pointerdown", event => {
    if (!menu.contains(event.target as Node)) close();
  }, { signal, capture: true });
  document.addEventListener("scroll", () => close(), { signal, capture: true });
  document.addEventListener("selectionchange", () => {
    // Перерисовка сообщения или новое выделение не оставляет устаревшее меню.
    if (!menu.hidden && ranges.length && (!window.getSelection()?.toString().trim()
      || ranges.some(range => !range.startContainer.isConnected))) close();
  }, { signal });
  window.addEventListener("blur", () => close(), { signal });
  window.addEventListener("resize", () => close(), { signal });
  return () => { close(); abort.abort(); menu.remove(); };
}
