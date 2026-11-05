// Мини-апп Telegram: то же приложение, что телефон (ui-kit/phone.ts), со
// входом по initData Telegram. Открывается из старого служебного бота
// владельца; подпись initData проверяет канал (`POST /pair/telegram`,
// КОНТРАКТ-B→A §6) и выдаёт ключ устройства той же природы, что у QR.
import "./styles.css";
import { setAudioPlayer } from '../../ui-kit/paper-audio';
setAudioPlayer(() => import('wavesurfer.js').then(module => module.default));
import { setAnimationPlayer } from '../../ui-kit/paper-media';
setAnimationPlayer(() => import('lottie-web/build/player/lottie_light').then(module => module.default));
import { mountPhone, remember, type Redeem } from "../../ui-kit/phone";
import { mountTelegramShell, type TelegramWebApp } from './telegram-shell';
import { applyTheme, type Theme } from "../../ui-kit/dom";
import { watchShellVersion } from "../../ui-kit/version";

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
    DESK_CONFIG_OVERRIDE?: import('../../ui-kit/window/api').Cfg;
    DESK_CONFIG?: import('../../ui-kit/window/api').Cfg | null;
  }
}

const tg = window.Telegram?.WebApp;
// Приоритет: подстановка dev-сервера, потом config.js хостинга.
const override = window.DESK_CONFIG_OVERRIDE || window.DESK_CONFIG || undefined;
const base = override?.base || "";
const STORAGE = "frame.tg.device";
if (!tg?.initData) remember(STORAGE, '');

// initData is the only enrollment path. Strip legacy URL credentials before
// any API traffic; public bot URLs never carry channel/device credentials.
if (location.search || location.hash) history.replaceState(null, '', location.pathname);

// WKWebView Telegram держит старую сборку по адресу кнопки — страница сама
// сверяет сборку с сервером и перезагружается на новую.
watchShellVersion();

mountTelegramShell(tg);

// Тема — от Telegram: пользователь выбирает её там, а не у нас.
const theme = (): Theme => (tg ? (tg.colorScheme === "dark" ? "dark" : "light") : "system");
tg?.onEvent("themeChanged", () => applyTheme(theme()));

async function redeem(): Promise<{ result: Redeem; key?: string; agent?: string }> {
  const initData = tg?.initData || "";
  if (!initData) return { result: "none" };
  let r: Response;
  try {
    r = await fetch(base + "/pair/telegram", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData }) });
  } catch {
    return { result: "offline" };
  }
  if (r.status === 404 || r.status === 405) return { result: "foreign" }; // канал ещё не умеет
  if (r.status === 403) return { result: "closed" };
  if (!r.ok) return { result: "broke" };
  let d: { key?: string; agent?: string };
  try {
    d = (await r.json()) as { key?: string; agent?: string };
  } catch {
    return { result: "broke" };
  }
  if (!d.key) return { result: "broke" };
  return { result: "ok", key: d.key, agent: d.agent };
}

function pairScreen(pair: Redeem | null): { title: string; text: string; retry: boolean } {
  switch (pair) {
    case "foreign":
      return { title: "Открой свежую кнопку", text: "Вернись в своего бота и отправь /app или открой его меню. Hélène обновляет вход при смене адреса; кнопки в старых сообщениях сохраняют прежний адрес.", retry: true };
    case "closed":
      return { title: "Это не твой агент", text: "Мини-апп открывает переписку только владельцу: канал сверил подпись Telegram и id — и не совпало.", retry: false };
    case "offline":
      return { title: "Нет связи", text: "Проверь, что Hélène запущена и подключена к интернету. Затем вернись в своего бота и открой меню или отправь /app для свежей кнопки.", retry: true };
    case "broke":
      return { title: "Канал ответил ошибкой", text: "Вход не удался на стороне канала. Попробуй ещё раз; если повторится — смотри журнал канала.", retry: true };
    case "none":
      return { title: "Открой из Telegram", text: "Эта страница ждёт данных Telegram Web App: открой её кнопкой в боте, а не по ссылке в браузере.", retry: true };
    default:
      return { title: "Нужен ключ", text: "Открой мини-апп из бота: Telegram подпишет вход, канал выдаст ключ.", retry: true };
  }
}

const app = mountPhone(document.getElementById("app")!, {
  platform: "telegram",
  base,
  agentFallback: override?.agent || "",
  sign: "Hélène",
  theme: tg ? theme : undefined,
  onSheet: (open) => {
    if (!tg) return;
    if (open) tg.BackButton.show();
    else tg.BackButton.hide();
  },
  auth: {
    storageKey: STORAGE,
    requireCredential: true,
    hasCredential: () => !!tg?.initData,
    redeem,
    pairScreen,
  },
});

tg?.BackButton.onClick(() => app.back());
