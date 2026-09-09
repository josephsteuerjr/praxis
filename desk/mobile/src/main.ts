// Телефон: то же приложение, что мини-апп (ui-kit/phone.ts), со входом по QR.
// Ключ устройства приходит по ссылке /m/?pair=<токен> и живёт в localStorage;
// на iPhone страница в Safari и значок на «Домой» — разные хранилища, поэтому
// токен пары годится дважды, а манифест несёт его в start_url.
import "./styles.css";
import { mountPhone, remember, type Redeem } from "../../ui-kit/phone";
import { toast } from "../../ui-kit/dom";
import { watchShellVersion } from "../../ui-kit/version";

declare global {
  interface Window {
    PULT_CONFIG_OVERRIDE?: { base: string; key: string; agent?: string };
    PULT_CONFIG?: { base: string; key: string; agent?: string } | null;
  }
}

const params = new URLSearchParams(location.search);
const standalone = matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const isApple = /iPhone|iPad|iPod/.test(navigator.userAgent);
// Приоритет: подстановка dev-сервера, потом config.js хостинга.
const override = window.PULT_CONFIG_OVERRIDE || window.PULT_CONFIG || undefined;
const base = override?.base || "";
const STORAGE = "frame.device";

// Манифест с токеном: чтобы установленное приложение открылось с ним же.
const pairToken = params.get("pair") || "";
const manifest = document.createElement("link");
manifest.rel = "manifest";
manifest.href = "/m/manifest.webmanifest" + (pairToken ? "?pair=" + encodeURIComponent(pairToken) : "");
document.head.append(manifest);

// Ключ прямо в адресе — только для проверки с компьютера (?key=…): так же
// входит веб-версия окна. Обычный путь телефона — QR.
const directKey = params.get("key") || override?.key || "";
if (directKey) remember(STORAGE, directKey);

/** Обмен токена пары на ключ. Раньше любой не-2xx звался «код устарел». */
async function redeem(token: string): Promise<{ result: Redeem; key?: string; agent?: string }> {
  let r: Response;
  try {
    r = await fetch(base + "/pair/redeem?token=" + encodeURIComponent(token));
  } catch {
    return { result: "offline" };
  }
  if (r.status === 403) {
    // Канал отвечает 403 и «код израсходован/протух», и «телефон не пускают
    // ворота»: диагноз разный, различаем по телу.
    const body = await r.text().catch(() => "");
    return { result: /устарел|использован/i.test(body) ? "spent" : "closed" };
  }
  if (r.status === 404 || r.status === 410) return { result: "spent" };
  if (!r.ok) return { result: "broke" };
  let d: { key?: string; agent?: string };
  try {
    d = (await r.json()) as { key?: string; agent?: string };
  } catch {
    return { result: "broke" };
  }
  if (!d.key) return { result: "broke" };
  // Токен из адреса убираем ТОЛЬКО в установленном приложении: в Safari он
  // должен остаться — «На экран „Домой“» берёт текущий адрес.
  if (standalone) history.replaceState(null, "", "/m/");
  return { result: "ok", key: d.key, agent: d.agent };
}

function pairScreen(pair: Redeem | null): { title: string; text: string; retry: boolean } {
  switch (pair) {
    case "closed":
      return { title: "Компьютер не пускает", text: "Этот телефон не принимают. На компьютере открой Настройки → Телефон, включи тумблер, перезапусти программу и покажи QR заново.", retry: true };
    case "broke":
      return { title: "Компьютер ответил ошибкой", text: "Обмен кода на ключ не удался на стороне компьютера. Покажи QR заново и попробуй ещё раз.", retry: true };
    case "offline":
      return { title: "Нет связи", text: "Компьютер с агентом не отвечает. Телефон должен быть в той же Wi-Fi, что и компьютер, или подключён к Tailscale.", retry: true };
    case "spent":
      return { title: "Код устарел", text: "Ссылка из QR живёт десять минут и годится дважды. Покажи QR на компьютере заново и открой его снова.", retry: true };
    default:
      return { title: "Нужен ключ", text: "Открой на компьютере Настройки → Телефон → «Показать QR» и наведи камеру. Ссылка подключит этот телефон.", retry: true };
  }
}

// Оболочка сверяет свою сборку с серверной и сама перезагружается на новую:
// PWA на «Домой» иначе живёт старой версией неделями.
watchShellVersion();

mountPhone(document.getElementById("app")!, {
  platform: "web",
  base,
  agentFallback: override?.agent || "",
  sign: "Hélène",
  auth: {
    storageKey: STORAGE,
    hasCredential: () => !!pairToken,
    redeem: () => redeem(pairToken),
    pairScreen,
  },
});

// ------------------------------------------------------------ подсказки

/** Страница отдана по http и не через Tailscale — связь не шифруется. */
function insecureLink(): boolean {
  if (location.protocol !== "http:") return false;
  const host = location.hostname;
  if (/^100\./.test(host)) return false; // Tailscale: WireGuard шифрует сам
  return host !== "localhost" && host !== "::1" && !/^127\./.test(host);
}

let warned = "";
try {
  warned = localStorage.getItem("frame.netwarn") || "";
} catch {
  // без хранилища — предупредим снова
}
if (insecureLink() && !warned) {
  try {
    localStorage.setItem("frame.netwarn", "1");
  } catch {
    // ок
  }
  toast("Связь с компьютером не шифруется — это обычный Wi-Fi. В чужой сети сосед может прочитать переписку; надёжно — через Tailscale.");
} else if (isApple && !standalone && pairToken) {
  toast("Чтобы открывать как приложение: «Поделиться» → «На экран „Домой“». Первое открытие из значка допишет ключ само.");
}

// ------------------------------------------------------------ service worker
//
// Оболочка приложения — офлайн, данные — живьём. Регистрируем только если
// канал отдаёт /m/sw.js как скрипт (КОНТРАКТ-B→A §5): иначе браузер пишет
// ошибку в консоль, а без SW страница и так работает.
if ("serviceWorker" in navigator && location.protocol === "https:" && !base) {
  void (async () => {
    try {
      const head = await fetch("/m/sw.js", { method: "GET", cache: "no-store" });
      const type = head.headers.get("content-type") || "";
      if (head.ok && /javascript/.test(type)) await navigator.serviceWorker.register("/m/sw.js", { scope: "/m/" });
    } catch {
      // без SW — как обычная страница
    }
  })();
}
