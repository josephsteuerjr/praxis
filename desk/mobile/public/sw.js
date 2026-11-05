// Retirement worker for the former standalone Hélène phone PWA.
// Never cache or intercept fetch. Remove only our own legacy caches.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const key of await caches.keys()) if (key.startsWith('helene-m-')) await caches.delete(key);
  await self.registration.unregister();
})()));
