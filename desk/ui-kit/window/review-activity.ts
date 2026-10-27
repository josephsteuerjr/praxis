type Activity = { running?: number; queued?: number };

export function reviewActivity(
  badge: HTMLElement, read: () => Promise<Activity>, identity: () => string,
) {
  let owner = identity(), reading = false, attempted = 0, received = 0;
  return async () => {
    const now = Date.now(), current = identity();
    if (owner !== current) {
      owner = current;
      attempted = received = 0;
      badge.hidden = true;
    }
    if (now - received > 15000) badge.hidden = true;
    if (reading || now - attempted < 3000) return;
    reading = true;
    attempted = now;
    try {
      const value = await read();
      if (current !== identity()) return;
      const count = (n: unknown) => typeof n === "number" && Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
      const running = count(value.running), queued = count(value.queued);
      received = Date.now();
      badge.textContent = running ? `review · ${running}` : "review";
      badge.title = running ? `Ревьюеры работают: ${running}. В очереди: ${queued}.` : `Ревью ожидает запуска: ${queued}.`;
      badge.setAttribute("aria-label", badge.title);
      badge.hidden = running + queued === 0;
    } catch {
      if (current === identity()) badge.hidden = true;
    } finally {
      reading = false;
    }
  };
}
