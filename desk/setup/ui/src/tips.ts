import { t as uiText } from "../../../lang/lang";
export function setupTips(): string[] { return [
  uiText("ui.setup.ui.src.tips.d30888a630"),
  uiText("ui.setup.ui.src.tips.b0a1aada10"),
  uiText("ui.setup.ui.src.tips.b891c64037"),
  uiText("ui.setup.ui.src.tips.e6ca3ef9bb"),
]; }

export function startSetupTips(parent: HTMLElement, paused: () => boolean): () => void {
  const box = document.createElement("p");
  box.id = "setup-tips";
  box.setAttribute("aria-live", "off");
  box.textContent = setupTips()[0];
  parent.append(box);
  let index = 0, stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const schedule = () => { timer = setTimeout(next, 8000); };
  const next = () => {
    if (stopped || !box.isConnected) return;
    if (paused()) { schedule(); return; }
    box.classList.add("changing");
    timer = setTimeout(() => {
      if (stopped || !box.isConnected) return;
      if (!paused()) box.textContent = setupTips()[++index % setupTips().length];
      box.classList.remove("changing");
      schedule();
    }, 300);
  };
  schedule();
  return () => { stopped = true; clearTimeout(timer); box.remove(); };
}
