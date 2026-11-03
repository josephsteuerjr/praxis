import { el, readTheme, saveTheme, type Theme } from '../dom';
import { DEFAULT_LOOK, PAPER_DAY, PAPER_NIGHT, ACCENTS, apply, save, load, type Look } from './look';
import { resetZoom } from './scroll';
import './styles/look-customization.css';

const PRESETS = [
  {id:'paper', name:'Бумага', note:'Тёплая, с характером'},
  {id:'clean', name:'Чистый лист', note:'Ничего лишнего'},
  {id:'compact', name:'Рабочий стол', note:'Больше места для дела'},
  {id:'studio', name:'Студия', note:'Сетка и чёткие линии'},
  {id:'glass', name:'Стекло', note:'Мягкий свет и воздух'},
  {id:'terminal', name:'Терминал', note:'Строго и компактно'},
] as const;

/** All controls use the existing, shared preference owner. */
export function customizationCard(look: Look, rebuild: () => void, options:{phone?:boolean}={}): HTMLElement {
  const box=el('div','look-customization');
  const set=<K extends keyof Look>(key:K,value:Look[K])=>{Object.assign(look,load());look[key]=value;save(look);apply(look);};
  const section=(title:string,note?:string)=>{
    const root=el('section','look-section');root.append(el('h4','look-section-title',title));
    if(note)root.append(el('p','look-section-note',note));return root;
  };
  const select=(title:string,choices:Array<[string,string]>,value:string,change:(v:string)=>void)=>{
    const row=el('label','look-field');row.append(el('span','look-label',title));
    const input=document.createElement('select');input.setAttribute('aria-label',title);
    for(const [id,label] of choices){const item=document.createElement('option');item.value=id;item.textContent=label;input.append(item);}
    input.value=value;input.addEventListener('change',()=>change(input.value));row.append(input);return row;
  };
  const radioKeys=(group:HTMLElement)=>group.addEventListener('keydown',event=>{
    if(!['ArrowRight','ArrowDown','ArrowLeft','ArrowUp','Home','End'].includes(event.key))return;
    const buttons=[...group.querySelectorAll<HTMLButtonElement>('[role=radio]')];
    const current=buttons.indexOf(document.activeElement as HTMLButtonElement);if(current<0)return;
    event.preventDefault();
    const index=event.key==='Home'?0:event.key==='End'?buttons.length-1:
      (current+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+buttons.length)%buttons.length;
    buttons[index].focus();buttons[index].click();
  });
  const mark=(group:HTMLElement,value:string)=>{
    for(const button of group.querySelectorAll<HTMLButtonElement>('[role=radio]')){
      const chosen=button.dataset.value===value;button.setAttribute('aria-checked',String(chosen));button.tabIndex=chosen?0:-1;
    }
  };
  const presets=section('Начни с готового вида','Выбери основу. Цвета, размер текста и детали можно настроить ниже.');
  const grid=el('div','look-presets');grid.setAttribute('role','radiogroup');grid.setAttribute('aria-label','Готовый вид');
  for(const preset of PRESETS){
    const button=el('button','look-preset') as HTMLButtonElement;button.type='button';
    button.dataset.value=preset.id;button.setAttribute('role','radio');button.setAttribute('aria-label',preset.name);
    const preview=el('span','look-preset-preview');preview.dataset.preset=preset.id;preview.setAttribute('aria-hidden','true');
    preview.innerHTML='<span class="look-mini-rail"><i></i><i></i><i></i></span><span class="look-mini-page"><b>Привет.</b><i></i><i></i><span></span></span><span class="look-mini-panel"><i></i><i></i></span>';
    const label=el('span','look-preset-label');label.append(el('strong','',preset.name),el('span','',preset.note));button.append(preview,label);
    button.addEventListener('click',()=>{
      const v=preset.id;
      Object.assign(look,load(),{style:v==='compact'?'paper':v,density:v==='compact'||v==='terminal'?'compact':'comfortable',
        grain:v==='paper'?'soft':'none',font:v==='terminal'?'mono':v==='paper'?'hand':'sans',
        shape:v==='terminal'?'square':v==='glass'?'round':'default',backdrop:v==='glass'?'glow':v==='studio'?'grid':'plain'});
      save(look);apply(look);rebuild();
      queueMicrotask(()=>document.querySelector<HTMLButtonElement>('.look-preset[data-value="'+v+'"]')?.focus({preventScroll:true}));
    });grid.append(button);
  }
  mark(grid,look.density==='compact'&&look.style==='paper'?'compact':look.style);radioKeys(grid);presets.append(grid);box.append(presets);
  const essentials=section('Свет, текст и пространство');
  const theme=el('div','look-theme');theme.setAttribute('role','radiogroup');theme.setAttribute('aria-label','Тема');
  for(const [id,name,symbol] of [['system','Как в системе','◐'],['light','Светлая','☀'],['dark','Тёмная','☾']] as const){
    const button=el('button','look-theme-option') as HTMLButtonElement;button.type='button';button.dataset.value=id;button.setAttribute('role','radio');
    const icon=el('span','look-theme-icon',symbol);icon.setAttribute('aria-hidden','true');button.append(icon,el('span','',name));
    button.onclick=()=>{saveTheme(id as Theme);Object.assign(look,load());apply(look);mark(theme,id);};theme.append(button);
  }
  mark(theme,readTheme());radioKeys(theme);essentials.append(theme);
  const fields=el('div','look-fields');const size=el('label','look-field look-scale');
  const caption=el('span','look-scale-caption');caption.append(el('span','look-label','Масштаб всего окна'));
  const slider=document.createElement('input');slider.type='range';slider.min='70';slider.max='150';slider.step='5';
  slider.value=String(Math.round(look.uiScale*100));slider.setAttribute('aria-label','Масштаб всего окна');
  const percent=el('output','look-scale-value',slider.value+'%');caption.append(percent);
  slider.addEventListener('input',()=>{set('uiScale',Number(slider.value)/100);percent.textContent=slider.value+'%';window.dispatchEvent(new Event('resize'));});
  size.append(caption,slider);fields.append(size);
  fields.append(select('Шрифт',[['hand','Почерк Hélène'],['sans','Современный'],['serif','Книжный'],['mono','Моноширинный']],look.font,v=>set('font',v as Look['font'])));
  fields.append(select('Плотность',[['comfortable','Свободно'],['compact','Компактно']],look.density,v=>{
    set('density',v as Look['density']);mark(grid,look.density==='compact'&&look.style==='paper'?'compact':look.style);
  }));
  essentials.append(fields);box.append(essentials);
  const details=document.createElement('details');details.className='look-details';details.append(el('summary','','Детали оформления'));
  const fine=el('div','look-fields');
  fine.append(select('Форма',[['default','Стандартная'],['round','Мягкая'],['square','Прямые углы']],look.shape,v=>set('shape',v as Look['shape'])));
  fine.append(select('Фон',[['plain','Ровный'],['grid','Сетка'],['glow','Световые пятна']],look.backdrop,v=>set('backdrop',v as Look['backdrop'])));
  for(const side of options.phone?[]:['rail','panel'] as const){
    fine.append(select(side==='rail'?'Список чатов':'История ходов',[['0','Показывать'],['1','Свернуть']],localStorage.getItem('frame.'+side)||'0',v=>{
      localStorage.setItem('frame.'+side,v);window.dispatchEvent(new CustomEvent('helene:appearance'));
    }));
  }
  details.append(fine);const colors=el('div','look-custom-colors');
  for(const [key,title,palette,fallback] of [
    ['dayColor','Свой цвет дня',PAPER_DAY,look.paperDay],['nightColor','Свой цвет ночи',PAPER_NIGHT,look.paperNight],
    ['accentColor','Свой акцент',ACCENTS,look.accent],
  ] as const){
    const current=()=>{const selected=load();const id=key==='dayColor'?selected.paperDay:key==='nightColor'?selected.paperNight:selected.accent;
      const choice=palette.find(x=>x.id===id)||palette.find(x=>x.id===fallback)||palette[0];return 'color' in choice?choice.color:choice.fill;};
    const row=el('label','look-field');row.append(el('span','look-label',title));
    const input=document.createElement('input');input.type='color';input.dataset.lookColor=key;input.value=look[key]||current();input.setAttribute('aria-label',title);input.addEventListener('input',()=>set(key,input.value));
    const clear=el('button','look-reset','Из палитры') as HTMLButtonElement;clear.type='button';clear.onclick=()=>{set(key,'');input.value=current();};
    const controls=el('span','look-color-controls');controls.append(input,clear);row.append(controls);colors.append(row);
  }
  details.append(colors);box.append(details);
  const reset=el('button','look-reset look-reset-all','Вернуть стандартный вид') as HTMLButtonElement;reset.type='button';
  reset.onclick=()=>{
    save({...DEFAULT_LOOK});saveTheme('system');
    for(const key of ['frame.rail','frame.panel','frame.rail.width','frame.panel.width','helene.zoom'])localStorage.removeItem(key);
    resetZoom();apply({...DEFAULT_LOOK});window.dispatchEvent(new CustomEvent('helene:appearance'));rebuild();
  };
  const status=el('p','receipt');status.setAttribute('role','status');const onError=(event:Event)=>{status.textContent='Не удалось сохранить: '+(event as CustomEvent).detail;};
  window.addEventListener('helene:preference-error',onError);
  const observer=new MutationObserver(()=>{if(!box.isConnected){window.removeEventListener('helene:preference-error',onError);observer.disconnect();}});
  observer.observe(document.body,{childList:true,subtree:true});box.append(reset,status);return box;
}
