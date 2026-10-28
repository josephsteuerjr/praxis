// Synthetic data, real production right-column mount/viewer/styles. No live API.
import '../../../ui-kit/window/styles/app.css';
import '../../../ui-kit/window/styles/paper.css';
import '../../../ui-kit/window/styles/turns.css';
import * as panel from '../../../ui-kit/window/panel';
import {forgePanel} from '../../../ui-kit/window/views/forge-panel';
import {S} from '../../../ui-kit/window/state';

S.view = 'talk'; S.room = 'window'; S.roomName = 'Джарвис';
const calls: string[] = [];
const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const path = typeof input === 'string' ? input : input.toString(); calls.push(path);
  if (path.startsWith('/api/chat-turns/')) return new Response(JSON.stringify([
    {run_id:'fixture-turn',ts:1793217600,in:'Покажи работающих детей над ходами',out:'Работа родителя закончилась; дочерний исполнитель всё ещё виден.',delivery:'sent'},
  ]), {headers:{'Content-Type':'application/json'}});
  if (path.startsWith('/api/run/')) return new Response(JSON.stringify({id:'fixture-turn',manifest:{status:'done'},iterations:[]}), {headers:{'Content-Type':'application/json'}});
  return originalFetch(input, init);
};
const errors: string[] = [];
window.addEventListener('error',event=>errors.push(event.message));
window.addEventListener('unhandledrejection',event=>errors.push(String(event.reason)));
const full = document.querySelector<HTMLElement>('#full')!;
let fullView: ReturnType<typeof forgePanel>|undefined;
addEventListener('frame-go', ()=> {
  full.hidden = false;
  fullView ||= forgePanel(full); fullView.start();
});
document.querySelector('#theme')!.addEventListener('click',()=> {
  document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
});
document.querySelector('#narrow')!.addEventListener('click',()=>document.documentElement.style.setProperty('--panel-w','270px'));
document.querySelector('#room')!.addEventListener('click',async()=> {S.room='window-other'; S.roomName='Другая комната'; await panel.render();});
document.querySelector('#disconnect')!.addEventListener('click',()=> {
  window.fetch = async ()=> { throw Error('Synthetic connection interrupted'); };
});
document.querySelector('#dispose')!.addEventListener('click',()=> {panel.dispose();fullView?.stop();});
Object.assign(window, {fixture:{calls,errors,panel,full,identity:'synthetic-only'}});
await panel.render();
