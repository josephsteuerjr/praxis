import { publishActivity, type WorkActivity } from './work-activity';
type Activity = WorkActivity;

export function reviewActivity(
  badge: HTMLElement, read: () => Promise<Activity>, identity: () => string,
) {
  let owner = identity(), reading = false, attempted = 0, received = 0;
  let last: Activity = {}, stale = false;
  const freeze=() => {
    if (stale) return;stale=true;
    publishActivity(owner,{...last,running:0,queued:0,
      update:last.update ? {...last.update,live:false,tests:last.update.tests ? {...last.update.tests,live:false} : undefined} : undefined,
      reviewers:last.reviewers?.map(row=>({...row,live:false}))});
  };
  return async () => {
    const now = Date.now(), current = identity();
    if (owner !== current) {
      owner = current;
      attempted = received = 0;
      badge.hidden = true;
      last={};stale=false;
      publishActivity(current,{});
    }
    if (now - received > 15000) {badge.hidden = true;freeze();}
    if (reading || now - attempted < 3000) return;
    reading = true;
    attempted = now;
    try {
      const value = await read();
      if (current !== identity()) return;
      last=value;stale=false;
      publishActivity(current,value);
      const count = (n: unknown) => typeof n === "number" && Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
      const running = count(value.running), queued = count(value.queued);
      received = Date.now();
      badge.textContent = running ? `review · ${running}` : "review";
      badge.title = running ? `Ревьюеры работают: ${running}. В очереди: ${queued}.` : `Ревью ожидает запуска: ${queued}.`;
      badge.setAttribute("aria-label", badge.title);
      badge.hidden = running + queued === 0;
    } catch {
      if (current === identity()) {badge.hidden = true;freeze();}
    } finally {
      reading = false;
    }
  };
}
