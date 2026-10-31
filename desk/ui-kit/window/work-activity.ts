// Passive receipts only. Keep details nodes across polls and conversation changes.
export interface TestActivity {
  state?: string; live?: boolean; completed?: number; total?: number; slots?: number;
  tests_completed?: number; tests_failed?: number; copied_bytes?: number;
  active?: Array<{slot?: number; phase?: string; file?: string; current_test?: string; started_at?: number}>;
}
export interface UpdateActivity {
  id?: string; state?: string; version?: string; live?: boolean; role?: string; review_id?: string;
  next?: string; error?: string; tests?: TestActivity;
}
interface Reviewer {
  id: string; name?: string; execution?: string; live?: boolean; review_id?: string;
  started_at?: number; finished_at?: number; observed_at?: number;
  verdict?: string; summary?: string; findings?: Array<{path?: string; scenario?: string; detail?: string}>;
}
export interface WorkActivity { running?: number; queued?: number; update?: UpdateActivity | null; reviewers?: Reviewer[] }
const count = (n: unknown) => typeof n === 'number' && Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
const states: Record<string,string> = {queued:'в очереди',starting:'запускается',running:'работает',finishing:'завершается',done:'готово',
  error:'ошибка',failed:'сбой',unknown:'нет подтверждения',cancelled:'отменено',unavailable:'недоступен',stopped:'остановлен'};
function node<K extends keyof HTMLElementTagNameMap>(tag: K, cls='', text='') {
  const value=document.createElement(tag);value.className=cls;value.textContent=text;return value;
}
function duration(row: Reviewer) {
  if (!row.started_at) return '';
  // Only an observed live receipt permits a ticking clock. Stale records freeze.
  const end=row.finished_at || (row.live ? Date.now()/1000 : row.observed_at || row.started_at);
  const seconds=Math.max(0,Math.floor(end-row.started_at));
  return seconds<60 ? `${seconds} с` : `${Math.floor(seconds/60)} мин ${seconds%60} с`;
}
export function workActivity(root: HTMLElement,options: {reviewers?: boolean} = {}) {
  root.classList.add('work-activity');root.hidden=true;
  const heading=node('div','work-heading'),tests=node('details','work-tests'),summary=node('summary');
  const testBadge=node('span','work-badge','tests'),testTitle=node('span','work-current'),testCount=node('span','work-count');
  summary.append(testBadge,testTitle,testCount);tests.append(summary);
  const testBody=node('div','work-detail'),progress=node('progress'),numbers=node('p','work-numbers'),files=node('div','work-files');
  progress.setAttribute('aria-label','Завершённые этапы проверки');testBody.append(progress,numbers,files);tests.append(testBody);
  const reviewBadge=node('span','work-badge','review'),role=node('span','work-role');heading.append(reviewBadge,role);
  const roster=node('div','work-roster'),error=node('p','work-error');error.setAttribute('role','status');
  root.append(tests,heading,roster,error);
  const rows=new Map<string,{row:HTMLDetailsElement;name:HTMLElement;state:HTMLElement;body:HTMLElement}>();
  let operation='';
  return (value: WorkActivity) => {
    const update=value.update, t=update?.tests;
    if (operation!==(update?.id || '')) {operation=update?.id || '';tests.open=false;}
    const reviewers=value.reviewers || [];
    const displayed=options.reviewers===false ? [] : reviewers;
    root.hidden=!update && reviewers.length===0;
    tests.hidden=!t;
    if (t) {
      const completed=count(t.completed),total=count(t.total),active=t.active || [];
      testCount.textContent=total ? `${completed}/${total}` : '';
      testTitle.textContent=!t.live && t.state!=='completed' ? 'нет свежего подтверждения'
        : t.state==='completed' ? 'проверки завершены' : active.find(row=>row.current_test)?.current_test || active.find(row=>row.file)?.file || 'подготавливаю проверку';
      testTitle.title=testTitle.textContent || '';
      progress.max=total || 1;progress.value=completed;progress.hidden=!total;
      numbers.textContent=`${completed} из ${total} этапов · выполнено тестов: ${count(t.tests_completed)} · ошибок: ${count(t.tests_failed)} · потоков: ${count(t.slots)}`;
      files.replaceChildren(...active.map(row=>node('div','work-file',`${count(row.slot)+1} · ${row.current_test || row.file || 'подготовка'}${row.phase==='preparing' && t.copied_bytes ? ` · ${(t.copied_bytes/1048576).toFixed(1)} МиБ` : ''}`)));
    }
    heading.hidden=!update && reviewers.length===0;
    reviewBadge.hidden=reviewers.length===0 && !['reviewing','review_requested'].includes(update?.state || '');
    reviewBadge.textContent=`review${reviewers.length ? ` · ${reviewers.filter(row=>row.live).length}/${reviewers.length}` : ''}`;
    if(reviewers.length && reviewers.every(row=>row.execution==='done'))reviewBadge.textContent='review · завершено';
    role.textContent=update?.error ? 'нужно исправление' : update?.role==='agent' ? 'агент готовит исправления'
      : update?.state==='completed' ? 'обновление завершено' : update?.state==='paused' ? 'на паузе'
      : ['cancelled','superseded'].includes(update?.state || '') ? 'продолжение отменено'
      : update?.state==='ready_to_apply' ? 'готово к переключению' : update ? 'обновлятор продолжает' : '';
    role.title=update?.next || '';
    const keep=new Set(displayed.map(row=>row.id));
    for (const [id,view] of rows) if (!keep.has(id)) {view.row.remove();rows.delete(id);}
    for (const item of displayed) {
      let view=rows.get(item.id);
      if (!view) {
        const row=node('details','work-reviewer'),head=node('summary'),name=node('span','work-name'),state=node('span','work-state'),body=node('div','work-detail');
        head.append(name,state);row.append(head,body);roster.append(row);view={row,name,state,body};rows.set(item.id,view);
      }
      view.name.textContent=item.name || 'Ревьюер';view.name.title=item.id;
      view.state.textContent=[item.live ? states[item.execution || ''] || 'работает' : states[item.execution || ''] || 'нет подтверждения',duration(item)].filter(Boolean).join(' · ');
      if (!item.live && ['running','starting','finishing'].includes(item.execution || '')) view.state.textContent='нет свежего подтверждения';
      view.body.replaceChildren(node('p','',item.summary || 'Вывод ещё не получен.'),
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
