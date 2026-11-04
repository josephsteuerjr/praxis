import './paper-select.css';

let nextId=0;
type View={select:HTMLSelectElement;wrap:HTMLElement;button:HTMLButtonElement;label:HTMLElement;tabIndex:number;change:()=>void};
/** Keep the real select and its config handlers; replace only its OS picker. */
export function mountPaperSelects(root:HTMLElement) {
  const views=new Map<HTMLSelectElement,View>();
  let active:View|undefined,menu:HTMLDivElement|undefined,choices:HTMLElement[]=[],cursor=-1;
  let disposed=false,pending=false,typeahead='',typedAt=0;
  function close(focus=false) {
    const prior=active;active=undefined;
    if(menu){try{if(typeof menu.hidePopover==='function')menu.hidePopover()}catch{/* already closed */}menu.remove();menu=undefined}
    choices=[];cursor=-1;prior?.button.setAttribute('aria-expanded','false');
    if(focus&&prior?.button.isConnected)prior.button.focus({preventScroll:true});
  }
  function sync(view:View) {
    const {select,button,label}=view;
    const value=select.selectedOptions[0]?.label||select.selectedOptions[0]?.textContent||'';
    if(label.textContent!==value)label.textContent=value;
    if(button.disabled!==select.disabled)button.disabled=select.disabled;
    if(button.hidden!==select.hidden)button.hidden=select.hidden;
    const caption=select.getAttribute('aria-label')||select.labels?.[0]?.querySelector('.field-label,.look-label')?.textContent||select.title||value;
    if(button.getAttribute('aria-label')!==caption)button.setAttribute('aria-label',caption);
    if(button.title!==select.title)button.title=select.title;
  }
  function focusChoice(index:number) {
    if(!choices.length)return;
    cursor=(index+choices.length)%choices.length;
    choices[cursor].focus({preventScroll:true});choices[cursor].scrollIntoView({block:'nearest'});
  }
  function move(delta:number) {for(let n=0;n<choices.length;n++){const i=(cursor+delta*(n+1)+choices.length*2)%choices.length;if(choices[i].getAttribute('aria-disabled')!=='true'){focusChoice(i);return}}}
  function choose(index:number) {
    if(!active||choices[index]?.getAttribute('aria-disabled')==='true')return;
    const view=active,optionIndex=Number(choices[index]?.dataset.index),option=view.select.options[optionIndex];
    if(!option||option.disabled||view.select.disabled)return;
    const changed=view.select.selectedIndex!==optionIndex;view.select.selectedIndex=optionIndex;sync(view);close(true);
    if(changed){view.select.dispatchEvent(new Event('input',{bubbles:true}));view.select.dispatchEvent(new Event('change',{bubbles:true}))}
  }
  function open(view:View) {
    if(view.button.disabled)return;
    if(active===view){close(true);return}close();active=view;sync(view);
    menu=document.createElement('div');menu.id='paper-select-'+(++nextId);menu.className='paper-select-menu';
    menu.setAttribute('role','listbox');menu.setAttribute('aria-label',view.button.getAttribute('aria-label')||'Варианты');
    view.button.setAttribute('aria-controls',menu.id);view.button.setAttribute('aria-expanded','true');
    let group='';
    typeahead='';typedAt=0;
    for(const [optionIndex,option] of [...view.select.options].entries()) {
      const parent=option.parentElement,heading=parent instanceof HTMLOptGroupElement?parent.label:'';
      if(heading&&heading!==group){const title=document.createElement('div');title.className='paper-select-group';title.textContent=heading;menu.append(title)}group=heading;
      if(option.hidden)continue;
      const row=document.createElement('div');row.className='paper-select-option';row.tabIndex=-1;
      row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.selected));
      row.setAttribute('aria-disabled',String(option.disabled||(parent instanceof HTMLOptGroupElement&&parent.disabled)));
      row.textContent=option.label;row.dataset.index=String(optionIndex);
      const index=choices.length;row.addEventListener('click',()=>choose(index));choices.push(row);menu.append(row);
    }
    // The top layer escapes the settings scroll clip, while the menu stays
    // in its modal DOM subtree for focus trapping and screen readers.
    const topLayer=typeof menu.showPopover==='function';
    if(topLayer)menu.setAttribute('popover','manual');
    (view.button.closest('[role="dialog"]')||root).append(menu);
    if(topLayer)menu.showPopover();
    const box=view.button.getBoundingClientRect(),height=Math.min(menu.scrollHeight,320,innerHeight*.65);
    const width=Math.min(Math.max(box.width,200),innerWidth-20);
    menu.style.width=width+'px';menu.style.left=Math.max(10,Math.min(box.left,innerWidth-width-10))+'px';
    menu.style.top=(box.bottom+height+8<innerHeight?box.bottom+5:Math.max(8,box.top-height-5))+'px';
    focusChoice(Math.max(0,choices.findIndex(row=>row.getAttribute('aria-selected')==='true')));
  }
  function add(select:HTMLSelectElement) {
    if(select.multiple||select.size>1||views.has(select))return;
    const wrap=document.createElement('span');wrap.className='paper-select';
    select.before(wrap);wrap.append(select);select.classList.add('paper-select-native');
    const button=document.createElement('button');button.type='button';button.className='paper-select-trigger';
    button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');
    const label=document.createElement('span');label.className='paper-select-label';const arrow=document.createElement('span');arrow.className='paper-select-arrow';arrow.setAttribute('aria-hidden','true');button.append(label,arrow);wrap.append(button);
    const view={select,wrap,button,label,tabIndex:select.tabIndex,change:()=>sync(view)};select.tabIndex=-1;select.setAttribute('aria-hidden','true');views.set(select,view);
    select.addEventListener('change',view.change);select.addEventListener('input',view.change);
    button.addEventListener('click',()=>open(view));
    button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();open(view);if(event.key==='End')focusChoice(choices.length-1);if(event.key==='Home')focusChoice(0)}});
    sync(view);
  }
  function restore(view:View) {
    view.select.removeEventListener('change',view.change);view.select.removeEventListener('input',view.change);
    view.select.classList.remove('paper-select-native');view.select.removeAttribute('aria-hidden');view.select.tabIndex=view.tabIndex;
    // Cached settings move out of the application while closed. Unwrap before
    // forgetting the view, so reopening cannot add a second trigger around it.
    if(view.wrap.contains(view.select))view.wrap.replaceWith(view.select);
  }
  function scan() {
    pending=false;if(disposed)return;
    for(const [select,view] of views)if(!root.contains(select)){if(active===view)close();restore(view);views.delete(select)}
    for(const select of root.querySelectorAll<HTMLSelectElement>('select'))add(select);
    for(const view of views.values())sync(view);
  }
  const observer=new MutationObserver(()=>{if(!pending){pending=true;queueMicrotask(scan)}});
  observer.observe(root,{subtree:true,childList:true,attributes:true,attributeFilter:['disabled','hidden','selected','label','value','aria-label']});scan();
  function outside(event:Event){if(active&&!menu?.contains(event.target as Node)&&!active.wrap.contains(event.target as Node))close()}
  function keys(event:KeyboardEvent) {
    if(!active)return;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true)}
    else if(event.key==='Tab'){close(true)}
    else if(['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(event.key)){
      event.preventDefault();event.stopPropagation();
      if(event.key==='ArrowDown')move(1);else if(event.key==='ArrowUp')move(-1);
      else if(event.key==='Home')focusChoice(0);else if(event.key==='End')focusChoice(choices.length-1);else choose(cursor);
    }else if(event.key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){
      const now=Date.now();typeahead=(now-typedAt>800?'':typeahead)+event.key.toLocaleLowerCase();typedAt=now;
      const index=choices.findIndex(row=>row.getAttribute('aria-disabled')!=='true'&&row.textContent?.toLocaleLowerCase().startsWith(typeahead));if(index>=0)focusChoice(index);
    }
  }
  document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',keys,true);
  const resize=()=>close();window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);
  return ()=>{disposed=true;observer.disconnect();close();document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',keys,true);window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',resize);
    for(const view of views.values())restore(view);views.clear();};
}
