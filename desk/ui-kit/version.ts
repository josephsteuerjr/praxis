// Принудительное обновление оболочки (слово владельца 07.09: «с айфоном не
// так-то просто»). WKWebView в Telegram и PWA на экране «Домой» держат старую
// сборку, что бы ни говорил Cache-Control. Поэтому страница сама сверяет свою
// сборку с той, что лежит на сервере: имя главного скрипта в index.html несёт
// хэш содержимого (Vite), и если на сервере оно другое — чистим кэши, будим
// service worker и перезагружаемся на адрес с новым `v=`, который для WebView
// — новый документ. Серверу ничего нового не нужно: index.html и так отдаётся
// с no-cache и окном, и телефоном, и мини-аппом.

const FORCED_KEY = "helene.shell.forced";

/** Имя главного скрипта загруженной сборки (`index-<хэш>.js`). */
function currentBundle(): string {
  const s = document.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/index-"]');
  const src = s?.getAttribute("src") || "";
  return src.split("/").pop() || "";
}

/** Имя главного скрипта в index.html на сервере; "" — не узнали. */
async function serverBundle(): Promise<string> {
  try {
    const path = location.pathname.endsWith(".html") ? location.pathname : location.pathname || "/";
    const r = await fetch(path + "?_=" + Date.now(), { cache: "no-store", credentials: "same-origin" });
    if (!r.ok) return "";
    const html = await r.text();
    const m = html.match(/assets\/(index-[A-Za-z0-9_-]+\.js)/);
    return m ? m[1] : "";
  } catch {
    return "";
  }
}

/**
 * Сверить сборку и, если сервер ушёл вперёд, перезагрузиться на новую.
 * Возвращает true, если перезагрузка запущена. Одна сборка — одна попытка за
 * сеанс: сервер, который отдаёт старый index.html из своего кэша, не должен
 * гонять страницу по кругу.
 */
export async function checkShellVersion(): Promise<boolean> {
  const mine = currentBundle();
  if (!mine) return false;
  const theirs = await serverBundle();
  if (!theirs || theirs === mine) return false;
  try {
    if (sessionStorage.getItem(FORCED_KEY) === theirs) return false;
    sessionStorage.setItem(FORCED_KEY, theirs);
  } catch {
    // без хранилища — рискуем одной лишней перезагрузкой
  }
  try {
    if ("caches" in window) for (const k of await caches.keys()) await caches.delete(k);
  } catch {
    // кэша нет — нечего чистить
  }
  try {
    const regs = (await navigator.serviceWorker?.getRegistrations?.()) || [];
    for (const reg of regs) await reg.update().catch(() => undefined);
  } catch {
    // service worker не при делах
  }
  const u = new URL(location.href);
  u.searchParams.set("v", theirs.replace(/^index-|\.js$/g, ""));
  location.replace(u.toString());
  return true;
}

declare global {
  interface Window {
    /** Ручная сверка из консоли или кнопки «Обновить оболочку». */
    heleneCheckShell?: () => Promise<boolean>;
  }
}

/** Сверять при старте, при возврате на экран и раз в несколько минут. */
export function watchShellVersion(everyMs = 5 * 60 * 1000): void {
  window.heleneCheckShell = checkShellVersion;
  void checkShellVersion();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void checkShellVersion();
  });
  setInterval(() => {
    if (!document.hidden) void checkShellVersion();
  }, everyMs);
}
