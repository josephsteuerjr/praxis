import { t as uiText } from "../../lang/lang";

export interface ReleaseSummary {
  state: 'checking' | 'preparing' | 'ready' | 'current' | 'disabled' | 'error' | 'used';
  latest?: string;
  sha256?: string;
}

export function releaseNotice(host: HTMLElement, check: () => Promise<ReleaseSummary>,
  restart: (sha256: string) => Promise<void>, settings: () => void, failure: (error: unknown) => void) {
  let alive = true, pending = false, applying = false, nextCheck = 0, digest = '';
  const action = document.createElement('button');
  action.type = 'button'; action.className = 'release-notice-open';
  const preserve = document.createElement('button');
  preserve.type = 'button'; preserve.className = 'release-notice-preserve';
  preserve.textContent = uiText('update.ready.preserve');
  preserve.addEventListener('click', settings);
  action.addEventListener('click', async () => {
    if (!alive || applying || !digest) return;
    applying = true; action.disabled = preserve.disabled = true;
    try { await restart(digest); host.hidden = true; }
    catch (error) { applying = false; action.disabled = preserve.disabled = false; failure(error); void refresh(); }
  });
  host.className = 'release-notice release-ready'; host.hidden = true;
  host.setAttribute('role', 'status'); host.append(action, preserve);
  const refresh = async () => {
    if (!alive || pending || applying || document.hidden) return;
    pending = true;
    try {
      const result = await check();
      if (!alive || applying) return;
      digest = result.state === 'ready' ? result.sha256 || '' : '';
      host.hidden = !digest;
      action.textContent = digest ? uiText('update.ready.restart', {version: result.latest || ''}) : '';
      const delay = result.state === 'preparing' || result.state === 'checking' ? 3000 :
        result.state === 'error' ? 60000 : 15 * 60000;
      nextCheck = Date.now() + delay;
    } catch { digest = ''; host.hidden = true; nextCheck = Date.now() + 60000; }
    finally { pending = false; }
  };
  const tick = () => { if (Date.now() >= nextCheck) void refresh(); };
  const changed = () => { nextCheck = 0; void refresh(); };
  document.addEventListener('visibilitychange', tick);
  window.addEventListener('helene:update-preference', changed);
  const timer = window.setInterval(tick, 3000);
  void refresh();
  return {refresh, dispose: () => { alive = false; window.clearInterval(timer);
    document.removeEventListener('visibilitychange', tick); window.removeEventListener('helene:update-preference', changed); host.remove(); }};
}
