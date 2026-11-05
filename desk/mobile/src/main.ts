// Legacy /m/ bookmarks use the same Telegram renderer. No PWA install flow.
import '../../miniapp/src/main';

// Retire only our old /m/ worker, preserving other applications and stores.
if ('serviceWorker' in navigator) {
  void navigator.serviceWorker.getRegistrations().then(registrations => Promise.all(
    registrations.filter(row => new URL(row.scope).origin === location.origin && new URL(row.scope).pathname === '/m/')
      .map(row => row.unregister())
  )).catch(() => {});
}
