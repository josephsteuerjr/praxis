import { el, readTheme, saveTheme, type Theme } from '../dom';
import { DEFAULT_LOOK, PAPER_DAY, PAPER_NIGHT, ACCENTS, apply, save, load, type Look } from './look';
import { resetZoom } from './scroll';

/** Controls edit the same keys the window/agent preference owner stores. */
export function customizationCard(look: Look, rebuild: () => void): HTMLElement {
  const box=el('div','look-customization');
  const set=<K extends keyof Look>(key:K,value:Look[K])=>{Object.assign(look,load());look[key]=value;save(look);apply(look);};
  const select=(title:string,options:Array<[string,string]>,value:string,change:(v:string)=>void)=>{
    const row=el('label','look-row');row.append(el('span','look-label',title));
    const input=document.createElement('select');input.setAttribute('aria-label',title);
    for(const [id,label] of options){const o=document.createElement('option');o.value=id;o.textContent=label;input.append(o);}
    input.value=value;input.addEventListener('change',()=>change(input.value));row.append(input);return row;
  };
  box.append(select('Готовый вид',[['paper','Бумага'],['clean','Чистый лист'],['compact','Рабочий стол'],['studio','Студия'],['glass','Стекло'],['terminal','Терминал']],look.density==='compact'&&look.style==='paper'?'compact':look.style,v=>{
    Object.assign(look,load(),{style:v==='compact'?'paper':v,density:v==='compact'||v==='terminal'?'compact':'comfortable',grain:v==='paper'?'soft':'none',font:v==='terminal'?'mono':v==='paper'?'hand':'sans',shape:v==='terminal'?'square':v==='glass'?'round':'default',backdrop:v==='glass'?'glow':v==='studio'?'grid':'plain'});save(look);apply(look);rebuild();
  }));
  box.append(select('Тема',[['system','Системная'],['light','Светлая'],['dark','Тёмная']],readTheme(),v=>{saveTheme(v as Theme);Object.assign(look,load());apply(look);}));
  const size=el('label','look-row');size.append(el('span','look-label','Масштаб всего окна'));
  const slider=document.createElement('input');slider.type='range';slider.min='70';slider.max='150';slider.step='5';slider.value=String(Math.round(look.uiScale*100));slider.setAttribute('aria-label','Масштаб всего окна');
  const percent=el('output','look-name',slider.value+'%');slider.addEventListener('input',()=>{set('uiScale',Number(slider.value)/100);percent.textContent=slider.value+'%';window.dispatchEvent(new Event('resize'));});size.append(slider,percent);box.append(size);
  box.append(select('Шрифт',[['hand','Почерк Hélène'],['sans','Современный'],['serif','Книжный'],['mono','Моноширинный']],look.font,v=>set('font',v as Look['font'])));
  box.append(select('Форма',[['default','Стандартная'],['round','Мягкая'],['square','Прямые углы']],look.shape,v=>set('shape',v as Look['shape'])));
  box.append(select('Фон',[['plain','Ровный'],['grid','Сетка'],['glow','Световые пятна']],look.backdrop,v=>set('backdrop',v as Look['backdrop'])));
  box.append(select('Плотность',[['comfortable','Свободно'],['compact','Компактно']],look.density,v=>set('density',v as Look['density'])));
  for(const side of ['rail','panel'] as const) {
    box.append(select(side==='rail'?'Список чатов':'История ходов',[['0','Показывать'],['1','Свернуть']],localStorage.getItem('frame.'+side)||'0',v=>{localStorage.setItem('frame.'+side,v);window.dispatchEvent(new CustomEvent('helene:appearance'));}));
  }
  for(const [key,title,fallback] of [
    ['dayColor','Свой цвет дня',PAPER_DAY.find(x=>x.id===look.paperDay)!.color],
    ['nightColor','Свой цвет ночи',PAPER_NIGHT.find(x=>x.id===look.paperNight)!.color],
    ['accentColor','Свой акцент',ACCENTS.find(x=>x.id===look.accent)!.fill],
  ] as const) {
    const row=el('label','look-row');row.append(el('span','look-label',title));const input=document.createElement('input');input.type='color';input.value=look[key]||fallback;input.setAttribute('aria-label',title);input.addEventListener('input',()=>set(key,input.value));
    const clear=el('button','look-reset','Из палитры') as HTMLButtonElement;clear.type='button';clear.onclick=()=>{set(key,'');input.value=fallback;};const controls=el('span','look-color-controls');controls.append(input,clear);row.append(controls);box.append(row);
  }
  const reset=el('button','look-reset','Системная тема · стандартный вид') as HTMLButtonElement;reset.type='button';
  reset.onclick=()=>{
    save({...DEFAULT_LOOK});saveTheme('system');
    for(const k of ['frame.rail','frame.panel','frame.rail.width','frame.panel.width','helene.zoom'])localStorage.removeItem(k);
    resetZoom();apply({...DEFAULT_LOOK});window.dispatchEvent(new CustomEvent('helene:appearance'));rebuild();
  };
  const status=el('p','receipt');const onError=(event:Event)=>{status.textContent='Не удалось сохранить: '+(event as CustomEvent).detail;};
  window.addEventListener('helene:preference-error',onError);
  const observer=new MutationObserver(()=>{if(!box.isConnected){window.removeEventListener('helene:preference-error',onError);observer.disconnect();}});
  observer.observe(document.body,{childList:true,subtree:true});box.append(reset,status);return box;
}
