export const SETUP_TIPS = [
  'После установки открой «Онбординг» внизу боковой панели: там первые шаги, примеры поручений и настройка подключений.',
  'Горячая память — свежая часть разговора. Счётчик и «Как работает память» находятся под лентой чата; весь архив сохраняется.',
  'Закрытие окна оставляет агента работать. Чтобы остановить его полностью, используй «Аварийный стоп» или аварийный ярлык.',
  'Телефон и Telegram Mini App обращаются к тому же агенту. Подключение и QR можно настроить в «Онбординге».',
];

export function startSetupTips(parent: HTMLElement, paused: () => boolean): () => void {
  const box = document.createElement("p");
  box.id = "setup-tips";
  box.setAttribute("aria-live", "off");
  box.textContent = SETUP_TIPS[0];
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
      if (!paused()) box.textContent = SETUP_TIPS[++index % SETUP_TIPS.length];
      box.classList.remove("changing");
      schedule();
    }, 300);
  };
  schedule();
  return () => { stopped = true; clearTimeout(timer); box.remove(); };
}
