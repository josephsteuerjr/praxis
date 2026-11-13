// Телефон: то же приложение, что мини-апп (ui-kit/phone.ts), со входом по QR.
// Ключ устройства приходит по ссылке /m/?pair=<токен> и живёт в localStorage;
// на iPhone страница в Safari и значок на «Домой» — разные хранилища, а с
// Firefox по умолчанию браузер и «Установить» съедают заходы ещё до значка —
// поэтому токен пары годится трижды (06.10), а манифест несёт его в start_url.
import "./styles.css";
import { setAudioPlayer } from '../../ui-kit/paper-audio';
setAudioPlayer(() => import('wavesurfer.js').then(module => module.default));
import { setAnimationPlayer } from '../../ui-kit/paper-media';
setAnimationPlayer(() => import('lottie-web/build/player/lottie_light').then(module => module.default));
import { mountPhone, remember, remembered, type Redeem } from "../../ui-kit/phone";
import { toast } from "../../ui-kit/dom";
import { watchShellVersion } from "../../ui-kit/version";
import type { Cfg } from '../../ui-kit/window/api';

declare global {
  interface Window {
    DESK_CONFIG_OVERRIDE?: Cfg;
    DESK_CONFIG?: Cfg | null;
  }
}

const params = new URLSearchParams(location.search);
const standalone = matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const isApple = /iPhone|iPad|iPod/.test(navigator.userAgent);
// Приоритет: подстановка dev-сервера, потом config.js хостинга.
const override = window.DESK_CONFIG_OVERRIDE || window.DESK_CONFIG || undefined;
const base = override?.base || "";
const STORAGE = "frame.device";

// A fixed manifest identity and the existing device session survive shell
// upgrades. Enrollment itself stays short-lived and never goes in storage.
const pairToken = params.get("pair") || "";
const manifest = document.createElement("link");
manifest.rel = "manifest";
manifest.href = "/m/manifest.webmanifest" + (pairToken ? "?pair=" + encodeURIComponent(pairToken) : "");
document.head.append(manifest);

// Ключ прямо в адресе — только для проверки с компьютера (?key=…): так же
// входит веб-версия окна. Обычный путь телефона — QR.
const directKey = params.get("key") || override?.key || "";
if (directKey) remember(STORAGE, directKey);

function keepDevice(key: string) {
  remember(STORAGE,key);
  // Safari and an installed iPhone PWA have separate stores. The fragment
  // bootstraps the same revocable device without a second enrollment.
  const fragment='#device='+encodeURIComponent(key);
  history.replaceState(null,'','/m/'+(standalone && remembered(STORAGE)===key ? '' : fragment));
  manifest.href='/m/manifest.webmanifest?session=1';
}
const fragmentDevice=new URLSearchParams(location.hash.slice(1)).get('device') || '';
if (/^dk-[A-Za-z0-9_-]{20,128}$/.test(fragmentDevice)) {
  remember(STORAGE,fragmentDevice);
  if(standalone && remembered(STORAGE)===fragmentDevice)history.replaceState(null,'','/m/');
  // Put back only this device's HttpOnly cookie so its install manifest can
  // retain the session too. No owner key, new device, or URL credential.
  void fetch(base+'/pair/redeem',{headers:{Authorization:'Bearer '+fragmentDevice},cache:'no-store'})
    .then(response=>{if(response.ok)keepDevice(fragmentDevice)}).catch(()=>{});
}

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
    let code = '';
    try { code = JSON.parse(body).code || ''; } catch { /* older channel */ }
    if (code === 'pair_unknown' || /не выдавал|другого адреса/i.test(body)) return { result: "foreign" };
    if (code === 'pair_expired') return { result: 'spent' };
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
  keepDevice(d.key);
  return { result: "ok", key: d.key, agent: d.agent };
}

function pairScreen(pair: Redeem | null): { title: string; text: string; retry: boolean } {
  switch (pair) {
    case "closed":
      return { title: "Компьютер не пускает", text: "Этот телефон не принимают. Открой Праксис и получи новую ссылку подключения телефона.", retry: true };
    case "broke":
      return { title: "Компьютер ответил ошибкой", text: "Обмен кода на ключ не удался на стороне компьютера. Покажи QR заново и попробуй ещё раз.", retry: true };
    case "offline":
      return { title: "Нет связи", text: "Компьютер с агентом не отвечает. Дождись подключения у Праксис и попробуй снова. Привязка устройства сохраняется при обновлении.", retry: true };
    case "spent":
      return { title: "Код устарел", text: "Ссылка из QR живёт десять минут и годится трижды. Получи новую ссылку подключения и открой его снова.", retry: true };
    case "foreign":
      return {
        title: "Код не найден",
        text: "Ссылка могла истечь или канал перезапустился. Получи новую ссылку подключения Праксис — он приведёт к текущему подключению.",
        retry: true,
      };
    default:
      return { title: "Нужен ключ", text: "Открой миниапп Праксис в Telegram или используй ссылку подключения телефона.", retry: true };
  }
}

// Оболочка сверяет свою сборку с серверной и сама перезагружается на новую:
// PWA на «Домой» иначе живёт старой версией неделями.
watchShellVersion();

mountPhone(document.getElementById("app")!, {
  platform: "web",
  base,
  agentFallback: override?.agent || "Праксис",
  sign: "Праксис",
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
  toast(location.hostname.endsWith('.trycloudflare.com')
    ? "Для входа после перезапуска открывай миниапп через своего Telegram-бота или новый QR у Праксис. Адрес этой страницы может измениться."
    : "Чтобы открывать как приложение: «Поделиться» → «На экран „Домой“». Первое открытие из значка допишет ключ само.");
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

import { installLegacyVoiceGroup } from "../../ui-kit/praxis-voice";
import "../../ui-kit/praxis-voice.css";
installLegacyVoiceGroup();
import { installXTTSServiceControls } from '../../ui-kit/praxis-service';
installXTTSServiceControls();
