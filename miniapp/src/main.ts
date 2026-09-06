// Мини-апп Telegram: то же приложение, что телефон (ui-kit/phone.ts), со
// входом по initData Telegram. Открывается из старого служебного бота
// владельца; подпись initData проверяет канал (`POST /pair/telegram`,
// КОНТРАКТ-B→A §6) и выдаёт ключ устройства той же природы, что у QR.
import "./styles.css";
import { mountPhone, remember, type Redeem } from "../../ui-kit/phone";
import { applyTheme, type Theme } from "../../ui-kit/dom";

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  onEvent(name: string, fn: () => void): void;
  BackButton: { show(): void; hide(): void; onClick(fn: () => void): void };
  initDataUnsafe?: { user?: { first_name?: string } };
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
    PULT_CONFIG_OVERRIDE?: { base: string; key: string; agent?: string };
  }
}

const tg = window.Telegram?.WebApp;
const params = new URLSearchParams(location.search);
const override = window.PULT_CONFIG_OVERRIDE;
const base = override?.base || "";
const STORAGE = "frame.tg.device";

// Ключ прямо в адресе — только для проверки из браузера (?key=…).
const directKey = params.get("key") || override?.key || "";
if (directKey) remember(STORAGE, directKey);

tg?.ready();
tg?.expand();

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
      return { title: "Канал ещё не умеет вход из Telegram", text: "Ручка /pair/telegram появится в следующей версии канала (контракт 0.3.3). Пока сюда можно войти ключом устройства из окна.", retry: true };
    case "closed":
      return { title: "Это не твой агент", text: "Мини-апп открывает переписку только владельцу: канал сверил подпись Telegram и id — и не совпало.", retry: false };
    case "offline":
      return { title: "Нет связи", text: "Канал агента не отвечает. Попробуй через минуту.", retry: true };
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
    hasCredential: () => !!tg?.initData && !directKey,
    redeem,
    pairScreen,
  },
});

tg?.BackButton.onClick(() => app.closeSheet());
