import { t as uiText, locale } from "../../lang/lang";
// Passive receipts only. Keep details nodes across polls and conversation changes.
export interface TestActivity {
  state?: string; live?: boolean; completed?: number; total?: number; slots?: number;
  tests_completed?: number; tests_failed?: number; copied_bytes?: number;
  active?: Array<{slot?: number; phase?: string; file?: string; current_test?: string; started_at?: number}>;
}
export interface UpdateActivity {
  id?: string; state?: string; version?: string; live?: boolean; role?: string; review_id?: string;
  next?: string; error?: string; tests?: TestActivity;
  editing?: {completed?: number; total?: number; pending?: string[]};
}
export interface Reviewer {
  id: string; name?: string; execution?: string; live?: boolean; review_id?: string;
  task_id?: string; unit_id?: string; model?: string; cancel_requested?: boolean;
  started_at?: number; finished_at?: number; observed_at?: number;
  verdict?: string; summary?: string; findings?: Array<{path?: string; scenario?: string; detail?: string}>;
}
export interface WorkActivity { running?: number; queued?: number; update?: UpdateActivity | null; reviewers?: Reviewer[]; recent_reviewers?: Reviewer[]; recent_reviewers_cursor?: string | null }
const count = (n: unknown) => typeof n === 'number' && Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
const states: Record<string,string> = {get queued() { return uiText("ui.ui.kit.window.work.activity.f8ba02ed06"); },get starting() { return uiText("ui.ui.kit.window.work.activity.4c9118412a"); },get running() { return uiText("ui.ui.kit.window.work.activity.c01295237e"); },get finishing() { return uiText("ui.ui.kit.window.work.activity.e9a207e8f0"); },get done() { return uiText("ui.ui.kit.window.work.activity.57778825e1"); },
  get error() { return uiText("ui.ui.kit.window.work.activity.44d4090ae9"); },get failed() { return uiText("ui.ui.kit.window.work.activity.b15413deaf"); },get unknown() { return uiText("ui.ui.kit.window.work.activity.7752a89d84"); },get cancelled() { return uiText("ui.ui.kit.window.work.activity.8a09723017"); },get unavailable() { return uiText("ui.ui.kit.window.work.activity.18a921b91e"); },get stopped() { return uiText("ui.ui.kit.window.work.activity.df14a196ee"); }};
function node<K extends keyof HTMLElementTagNameMap>(tag: K, cls='', text='') {
  const value=document.createElement(tag);value.className=cls;value.textContent=text;return value;
}
function duration(row: Reviewer) {
  if (!row.started_at) return '';
  // Only an observed live receipt permits a ticking clock. Stale records freeze.
  const end=row.finished_at || (row.live ? Date.now()/1000 : row.observed_at || row.started_at);
  const seconds=Math.max(0,Math.floor(end-row.started_at));
  return seconds<60 ? uiText("ui.ui.kit.window.work.activity.baf1ec7874", {p0: String(seconds)}) : uiText("ui.ui.kit.window.work.activity.aef6c8dfde", {p0: String(Math.floor(seconds/60)), p1: String(seconds%60)});
}
export function workActivity(root: HTMLElement,options: {reviewers?: boolean; heading?: boolean} = {}) {
  root.classList.add('work-activity');root.hidden=true;
  const heading=node('div','work-heading'),tests=node('details','work-tests'),summary=node('summary');
  const testBadge=node('span','work-badge',uiText('ui.work.tests')),testTitle=node('span','work-current'),testCount=node('span','work-count');
  summary.append(testBadge,testTitle,testCount);tests.append(summary);
  const testBody=node('div','work-detail'),progress=node('progress'),numbers=node('p','work-numbers'),files=node('div','work-files');
  progress.setAttribute('aria-label',uiText("ui.ui.kit.window.work.activity.4a9ea273d0"));testBody.append(progress,numbers,files);tests.append(testBody);
  const reviewBadge=node('span','work-badge',uiText('ui.work.review')),role=node('span','work-role');heading.append(reviewBadge,role);
  const roster=node('div','work-roster'),error=node('p','work-error');error.setAttribute('role','status');
  const editing=node('details','work-edits'),editSummary=node('summary');
  const editBadge=node('span','work-badge',uiText("ui.ui.kit.window.work.activity.2babadd89c")),editTitle=node('span','work-edit-title'),editCount=node('span','work-count');
  const editBody=node('div','work-detail');editSummary.append(editBadge,editTitle,editCount);editing.append(editSummary,editBody);
  root.append(editing,tests,heading,roster,error);
  const rows=new Map<string,{row:HTMLDetailsElement;name:HTMLElement;state:HTMLElement;body:HTMLElement}>();
  let operation='';
  return (value: WorkActivity) => {
    const update=value.update, t=update?.tests;
    if (operation!==(update?.id || '')) {operation=update?.id || '';tests.open=false;editing.open=false;}
    const reviewers=value.reviewers || [];
    const displayed=options.reviewers===false ? [] : reviewers;
    root.hidden=!update && reviewers.length===0;
    editing.hidden=!update || !['waiting_agent','needs_attention'].includes(update.state || '');
    editTitle.textContent=update?.error ? uiText("ui.ui.kit.window.work.activity.3efeb89bce") : uiText("ui.ui.kit.window.work.activity.9da978f132");
    editCount.textContent=update?.editing?.total ? `${count(update.editing.completed)}/${count(update.editing.total)}` : '';
    editBody.replaceChildren(node('p','',update?.next || uiText("ui.ui.kit.window.work.activity.efd53944b9")),
      ...(update?.editing?.pending || []).map(name=>node('div','work-file',name)));
    tests.hidden=!t;
    if (t) {
      const completed=count(t.completed),total=count(t.total),active=t.active || [];
      testCount.textContent=total ? `${completed}/${total}` : '';
      testTitle.textContent=!t.live && t.state!=='completed' ? uiText("ui.ui.kit.window.work.activity.84dcfcf410")
        : t.state==='completed' ? uiText("ui.ui.kit.window.work.activity.0e1d9c3916") : active.find(row=>row.current_test)?.current_test || active.find(row=>row.file)?.file || uiText("ui.ui.kit.window.work.activity.fd2271b638");
      testTitle.title=testTitle.textContent || '';
      progress.max=total || 1;progress.value=completed;progress.hidden=!total;
      numbers.textContent=uiText("ui.ui.kit.window.work.activity.682165bed5", {p0: String(completed), p1: String(total), p2: String(count(t.tests_completed)), p3: String(count(t.tests_failed)), p4: String(count(t.slots))});
      files.replaceChildren(...active.map(row=>node('div','work-file',`${count(row.slot)+1} · ${row.current_test || row.file || uiText("ui.ui.kit.window.work.activity.d31403f3cc")}${row.phase==='preparing' && t.copied_bytes ? uiText("ui.ui.kit.window.work.activity.b4674840ae", {p0: (t.copied_bytes/1048576).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1})}) : ''}`)));
    }
    heading.hidden=options.heading===false || (!update && reviewers.length===0);
    reviewBadge.hidden=reviewers.length===0 && !['reviewing','review_requested'].includes(update?.state || '');
    reviewBadge.textContent=uiText('ui.work.review')+`${reviewers.length ? ` · ${reviewers.filter(row=>row.live).length}/${reviewers.length}` : ''}`;
    if(reviewers.length && reviewers.every(row=>row.execution==='done'))reviewBadge.textContent=uiText("ui.ui.kit.window.work.activity.03f22c2de7");
    role.textContent=update?.error ? uiText("ui.ui.kit.window.work.activity.f98181e30a") : update?.role==='agent' ? uiText("ui.ui.kit.window.work.activity.8b4121d0ea")
      : update?.state==='completed' ? uiText("ui.ui.kit.window.work.activity.cbe9ee26dc") : update?.state==='paused' ? uiText("ui.ui.kit.window.work.activity.0bb2232e2e")
      : ['cancelled','superseded'].includes(update?.state || '') ? uiText("ui.ui.kit.window.work.activity.91e9b8f6dd")
      : update?.state==='ready_to_apply' ? uiText("ui.ui.kit.window.work.activity.cd907d47e4") : update ? uiText("ui.ui.kit.window.work.activity.c32d46b798") : '';
    role.title=update?.next || '';
    const keep=new Set(displayed.map(row=>row.id));
    for (const [id,view] of rows) if (!keep.has(id)) {view.row.remove();rows.delete(id);}
    for (const item of displayed) {
      let view=rows.get(item.id);
      if (!view) {
        const row=node('details','work-reviewer'),head=node('summary'),name=node('span','work-name'),state=node('span','work-state'),body=node('div','work-detail');
        head.append(name,state);row.append(head,body);roster.append(row);view={row,name,state,body};rows.set(item.id,view);
      }
      view.name.textContent=item.name || uiText("ui.ui.kit.window.work.activity.14964b4f9f");view.name.title=item.id;
      view.state.textContent=[item.live ? states[item.execution || ''] || uiText("ui.ui.kit.window.work.activity.c01295237e") : states[item.execution || ''] || uiText("ui.ui.kit.window.work.activity.7752a89d84"),duration(item)].filter(Boolean).join(' · ');
      if (!item.live && ['running','starting','finishing'].includes(item.execution || '')) view.state.textContent=uiText("ui.ui.kit.window.work.activity.84dcfcf410");
      if(item.cancel_requested&&!item.live&&['running','starting','finishing'].includes(item.execution||''))view.state.textContent=uiText("ui.ui.kit.window.work.activity.6f44a05cb3");
      const verdict=({clear:uiText("ui.ui.kit.window.work.activity.e39639b118"),findings:uiText("ui.ui.kit.window.work.activity.826964e603"),insufficient:uiText("ui.ui.kit.window.work.activity.31426c875a"),blocked:uiText("ui.ui.kit.window.work.activity.b31bb1052b")} as Record<string,string>)[item.verdict||''] || item.verdict || '';
      view.body.replaceChildren(...(item.model?[node('p','muted',item.model)]:[]),...(verdict?[node('p','',verdict)]:[]),node('p','',item.summary || uiText("ui.ui.kit.window.work.activity.e138677d27")),
        ...((item.findings || []).map(f=>node('p','work-finding',[f.path,f.scenario,f.detail].filter(Boolean).join(' · ')))));
    }
    error.textContent=update?.error || '';error.hidden=!error.textContent;
  };
}

let latest: {scope:string;value:WorkActivity} | undefined;
const listeners=new Set<(scope:string,value:WorkActivity)=>void>();
export function publishActivity(scope:string,value:WorkActivity) { latest={scope,value};for(const listener of listeners) listener(scope,value); }
export function observeActivity(identity:()=>string,render:(value:WorkActivity)=>void) {
  const listener=(scope:string,value:WorkActivity)=>render(scope===identity() ? value : {});
  listeners.add(listener);if(latest)listener(latest.scope,latest.value);
  return ()=>listeners.delete(listener);
}
