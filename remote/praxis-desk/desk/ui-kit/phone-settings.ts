import { el } from './dom';
import { lookCard } from './window/look';
export type SettingsRequest=<T>(path:string,body?:unknown)=>Promise<T>;

/** Praxis keeps model roles and the live XTTS catalogue in its server System view. */
export function phoneSettings(root:HTMLElement,_request:SettingsRequest,openSystem:()=>void) {
  root.className='phone-settings';root.dataset.dirty='false';
  const nav=el('nav','phone-settings-tabs'),body=el('div','phone-settings-body');
  nav.setAttribute('role','tablist');nav.setAttribute('aria-label','Разделы настроек');
  const pages=new Map<string,HTMLElement>();
  function pick(id:string){for(const [key,page] of pages)page.hidden=key!==id;for(const tab of nav.querySelectorAll('[role=tab]'))tab.setAttribute('aria-selected',String((tab as HTMLElement).dataset.group===id));body.scrollTop=0;}
  for(const [id,title] of [['look','Вид'],['agent','Праксис'],['voice','Голос']]){
    const tab=el('button','',title) as HTMLButtonElement;tab.type='button';tab.dataset.group=id;tab.setAttribute('role','tab');tab.addEventListener('click',()=>pick(id));nav.append(tab);
    const page=el('section','phone-settings-group');page.dataset.settingsGroup=id;pages.set(id,page);body.append(page);
    if(id==='look')page.append(lookCard({phone:true}));
    else {
      page.append(el('h2','',id==='voice'?'Голос Праксис':'Праксис на сервере'));
      page.append(el('p','',id==='voice'?'Послушать и выбрать голос, добавить собственный образец или приостановить озвучку можно в разделе «Система».':'Переписка, память и субагенты работают на сервере. В разделе «Система» доступны модели, состояние служб и журналы.'));
      const button=el('button','btn',id==='voice'?'Открыть голос':'Открыть систему') as HTMLButtonElement;button.type='button';button.addEventListener('click',()=>{openSystem();if(id==='voice')setTimeout(()=>document.querySelector('#praxis-voice-group')?.scrollIntoView({block:'start',behavior:'smooth'}),500)});page.append(button);
    }
  }
  root.replaceChildren(nav,body);pick('look');
  return {dirty:()=>false,reload:async()=>undefined};
}
