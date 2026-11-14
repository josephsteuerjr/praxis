import { t as uiText } from "../../lang/lang";
import { publishActivity, type WorkActivity } from './work-activity';
type Activity = WorkActivity;

export function reviewActivity(
  badge: HTMLElement, read: () => Promise<Activity>, identity: () => string,
) {
  let owner = identity(), reading = false, attempted = 0, received = 0;
  let last: Activity = {}, stale = false;
  const count = (n: unknown) => typeof n === 'number' && Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
  const terminal = (value: Activity) => ['completed','cancelled','superseded'].includes(value.update?.state || '');
  const visible = (value: Activity) => count(value.running) + count(value.queued) > 0
    || !!value.update && !terminal(value);
  const freeze=() => {
    if (stale) return;stale=true;
    if(visible(last)){
      badge.hidden=false;badge.textContent=uiText("ui.ui.kit.window.review.activity.c7c2f80c90");
      badge.title=uiText("ui.ui.kit.window.review.activity.e1df0472f7");
      badge.setAttribute('aria-label',badge.title);
    }
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
    if (now - received > 15000) {badge.hidden = !visible(last);freeze();}
    if (reading || now - attempted < (received && !visible(last) && !stale ? 10000 : 3000)) return;
    reading = true;
    attempted = now;
    try {
      const value = await read();
      if (current !== identity()) return;
      last=value;stale=false;
      publishActivity(current,value);
      const running = count(value.running), queued = count(value.queued);
      received = Date.now();
      const update=value.update, tests=update?.tests, editing=update?.editing;
      const ended=terminal(value);
      badge.textContent = running ? `${uiText('ui.work.review')} · ${running}` : queued ? uiText("ui.ui.kit.window.review.activity.cbd70c23c4", {p0: String(queued)})
        : tests && update?.state==='verification_requested' ? `${uiText('ui.work.tests')} · ${count(tests.completed)}/${count(tests.total)}`
        : update && !ended ? update.error ? uiText("ui.ui.kit.window.review.activity.a942bd4dd9")
          : ['waiting_agent','needs_attention'].includes(update.state || '')
            ? uiText("ui.ui.kit.window.review.activity.14294f74e9", {p0: String(editing?.total ? ` · ${count(editing.completed)}/${count(editing.total)}` : '')})
            : uiText("ui.ui.kit.window.review.activity.8fd70cc93f")+(update.state==='ready_to_apply' ? uiText("ui.ui.kit.window.review.activity.af0162434f") : update.state==='review_requested' ? uiText("ui.ui.kit.window.review.activity.ea1ed92608") : uiText("ui.ui.kit.window.review.activity.d31403f3cc"))
        : update?.state==='cancelled' ? uiText("ui.ui.kit.window.review.activity.a863d0ec86") : update?.state==='completed' ? uiText("ui.ui.kit.window.review.activity.76c3b3d627") : uiText('ui.work.review');
      badge.title = [update?.version ? uiText("ui.ui.kit.window.review.activity.9ab2cecf32", {p0: String(update.version)}) : '',
        running ? uiText("ui.ui.kit.window.review.activity.0d807a080f", {p0: String(running), p1: String(queued)}) : update?.next || uiText("ui.ui.kit.window.review.activity.6f1da2c2be", {p0: String(queued)}),
        uiText("ui.ui.kit.window.review.activity.536a2db9ce")].filter(Boolean).join(' ');
      badge.setAttribute("aria-label", badge.title);
      badge.hidden = !visible(value);
    } catch {
      if (current === identity()) {badge.hidden = !visible(last);freeze();}
    } finally {
      reading = false;
    }
  };
}
