// Service worker телефона: оболочка приложения живёт в кэше, данные — только
// с канала. Ассеты Vite носят хэш в имени — их можно хранить долго; страница
// /m/ берётся из сети, а из кэша — только когда сети нет.
const CACHE = "helene-m-__BUILD__";
const SHELL = ["/m/", "/m/icon-192.png", "/m/icon-512.png", "/m/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => undefined)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const p = url.pathname;
  // Живое — мимо кэша: API, спаривание, события, манифест с токеном пары.
  if (p.startsWith("/api") || p.startsWith("/pair") || p === "/events" || p === "/tunnel" || p.endsWith("manifest.webmanifest")) return;
  if (p.startsWith("/m/assets/") || /\/m\/(icon-\d+|apple-touch-icon)\.png$/.test(p)) {
    e.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
            return res;
          }),
      ),
    );
    return;
  }
  if (p === "/m/" || p === "/m") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) caches.open(CACHE).then((c) => c.put("/m/", res.clone()));
          return res;
        })
        .catch(() => caches.match("/m/").then((hit) => hit || Response.error())),
    );
  }
});
