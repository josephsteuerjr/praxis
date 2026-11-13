import "./paper-files.css";

let serial = 0;
export function paperDialog(title: string, wide = false) {
  const previous = document.activeElement as HTMLElement | null;
  const dialog = document.createElement("dialog");
  dialog.className = "paper-dialog" + (wide ? " paper-dialog-wide" : "");
  const heading = document.createElement("h2");
  heading.id = "paper-dialog-" + ++serial;
  heading.textContent = title;
  dialog.setAttribute("aria-labelledby", heading.id);
  const header = document.createElement("header");
  const close = paperButton("Закрыть", () => dialog.close());
  close.className += " paper-close";
  header.append(heading, close);
  const body = document.createElement("div"); body.className = "paper-dialog-body";
  const footer = document.createElement("footer");
  dialog.append(header, body, footer);
  document.body.append(dialog);
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (previous?.isConnected) previous.focus({ preventScroll: true });
  }, { once: true });
  // Native dialog supplies inert siblings and a focus trap; all visible controls are ours.
  dialog.showModal();
  return { dialog, body, footer, heading, close };
}

export function paperButton(label: string, action: () => void, primary = false) {
  const button = document.createElement("button");
  button.type = "button"; button.className = "paper-button" + (primary ? " paper-primary" : "");
  button.textContent = label; button.addEventListener("click", action); return button;
}

export function fileSize(bytes: number) {
  if (!Number.isFinite(bytes)) return "";
  return bytes >= 1048576 ? (bytes / 1048576).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " МБ"
    : bytes >= 1024 ? Math.ceil(bytes / 1024) + " КБ" : bytes + " Б";
}

export function fileError(error: unknown) {
  const text = error instanceof Error ? error.message : String(error);
  if (/failed to fetch|networkerror|load failed|aborterror|timeout|timed out/i.test(text)) return "Связь прервалась. Проверь подключение и повтори.";
  return text.replace(/https?:\/\/[^\s]+/gi, "адрес файла").slice(0, 400);
}
