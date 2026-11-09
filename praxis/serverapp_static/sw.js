/* Атланта: service worker. Оболочка работает без сети, данные всегда живые. */
const SHELL = "atlanta-shell-v9";
const ASSETS = ["/", "/s/atlanta.css?v=9", "/s/atlanta.js?v=9", "/manifest.webmanifest",
  "/s/icon-192.png", "/s/icon-512.png",
  "/s/unbounded-cyrillic.woff2", "/s/unbounded-latin.woff2",
  "/s/spectral-cyrillic-500-italic.woff2", "/s/spectral-latin-500-italic.woff2",
  "/s/jetbrains-mono-cyrillic.woff2", "/s/jetbrains-mono-latin.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    await Promise.allSettled(ASSETS.map(async (p) => {
      try { const r = await fetch(p, { cache: "reload" }); if (r.ok) await c.put(p, r); } catch (_) {}
    }));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => (k.startsWith("atlas-shell-") || k.startsWith("atlanta-shell-")) && k !== SHELL).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // API и вход никогда не кэшируем: только живые данные
  if (url.pathname.startsWith("/api/")) return;
  // оболочка: сначала сеть, без сети — кэш (установленная иконка всё равно откроется)
  e.respondWith((async () => {
    try {
      const r = await fetch(req);
      if (r.ok && (url.pathname === "/" || url.pathname.startsWith("/s/") || url.pathname === "/manifest.webmanifest")) {
        const c = await caches.open(SHELL); c.put(req, r.clone());
      }
      return r;
    } catch (_) {
      const c = await caches.open(SHELL);
      return (await c.match(req)) || (await c.match("/")) || Response.error();
    }
  })());
});
