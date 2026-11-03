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
      badge.hidden=false;badge.textContent='Действия · нет связи';
      badge.title='Нет свежего состояния. Открыть последние подтверждённые действия';
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
    if (reading || now - attempted < 3000) return;
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
      badge.textContent = running ? `review · ${running}` : queued ? `review · очередь ${queued}`
        : tests && update?.state==='verification_requested' ? `tests · ${count(tests.completed)}/${count(tests.total)}`
        : update && !ended ? update.error ? 'Обновление · нужна помощь'
          : ['waiting_agent','needs_attention'].includes(update.state || '')
            ? `Исправления${editing?.total ? ` · ${count(editing.completed)}/${count(editing.total)}` : ''}`
            : 'Обновление · '+(update.state==='ready_to_apply' ? 'переключение' : update.state==='review_requested' ? 'подготовка review' : 'подготовка')
        : update?.state==='cancelled' ? 'Обновление отменено' : update?.state==='completed' ? 'Обновление завершено' : 'review';
      badge.title = [update?.version ? `Обновление ${update.version}.` : '',
        running ? `Ревьюеры работают: ${running}. В очереди: ${queued}.` : update?.next || `В очереди: ${queued}.`,
        'Открыть действия'].filter(Boolean).join(' ');
      badge.setAttribute("aria-label", badge.title);
      badge.hidden = !visible(value);
    } catch {
      if (current === identity()) {badge.hidden = !visible(last);freeze();}
    } finally {
      reading = false;
    }
  };
}
