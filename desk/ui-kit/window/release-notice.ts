import { t as uiText } from "../../lang/lang";
/** Passive metadata checks. Installing an update always remains a user action. */
export interface ReleaseSummary { current: string; latest: string; newer: boolean }
export function releaseNotice(host: HTMLElement, check: () => Promise<ReleaseSummary>, open: () => void,
  options: {intervalMs?: number; storage?: Pick<Storage,'getItem'|'setItem'>} = {}) {
  const interval=options.intervalMs ?? 15*60*1000;
  let alive=true, pending=false, latest='', checkedAt=0;
  const storage=options.storage ?? (()=>{try{return localStorage;}catch{return undefined;}})();
  const hiddenVersion=()=>{try{return storage?.getItem('helene.release-notice.dismissed') || '';}catch{return '';}};
  const label=document.createElement('span');
  const action=document.createElement('button');action.type='button';action.className='release-notice-open';
  action.textContent=uiText("ui.ui.kit.window.release.notice.d4998bc841");action.addEventListener('click',()=>{if(alive&&latest)open();});
  const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='release-notice-close';
  dismiss.textContent='×';dismiss.setAttribute('aria-label',uiText("ui.ui.kit.window.release.notice.9c9ba0a56a"));
  dismiss.addEventListener('click',()=>{try{storage?.setItem('helene.release-notice.dismissed',latest);}catch{}host.hidden=true;});
  host.className='release-notice';host.hidden=true;host.setAttribute('role','status');host.append(label,action,dismiss);
  const refresh=async()=>{
    if(!alive||pending||document.hidden)return;
    pending=true;
    try {
      const value=await check();
      if(!alive)return;
      checkedAt=Date.now();
      latest=typeof value.latest==='string' ? value.latest.trim().slice(0,80) : '';
      host.hidden=!(value.newer===true&&latest&&latest!==hiddenVersion());
      label.textContent=host.hidden ? '' : uiText("ui.ui.kit.window.release.notice.5dc8127aa8", {p0: String(latest)});
    } catch { /* A background metadata outage never blocks the working agent. */ }
    finally {pending=false;}
  };
  const visible=()=>{if(!document.hidden&&Date.now()-checkedAt>=interval)void refresh();};
  document.addEventListener('visibilitychange',visible);
  const timer=window.setInterval(visible,interval);
  void refresh();
  return {refresh,dispose:()=>{alive=false;window.clearInterval(timer);document.removeEventListener('visibilitychange',visible);host.remove();}};
}
