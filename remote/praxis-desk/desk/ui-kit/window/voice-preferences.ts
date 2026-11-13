import { electron, shell } from './api';
import { paperButton, paperDialog } from '../paper-dialog';
import { esc } from './lib';

export interface VoicePreferences { hotkey: string; global: boolean; autoplay: boolean }
const KEY = 'helene.voice-ui';
export interface VoiceNativeState { active:boolean; error?:string; bindings?:Record<string,string> }
let nativeState: VoiceNativeState={active:false};
export function setVoiceNativeState(state:VoiceNativeState){nativeState=state;}
function nativeHint(){const b=nativeState.bindings;return b?.record ? `Клавиши рабочего стола: запись — ${b.record}; пауза — ${b.pause||'не назначена'}; отмена — ${b.cancel||'не назначена'}.` : '';}
export function voicePreferences(): VoicePreferences {
  let saved: Partial<VoicePreferences> = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch {}
  return {hotkey: typeof saved.hotkey === 'string' ? saved.hotkey : 'Ctrl+Alt+Space', global: saved.global !== false, autoplay: saved.autoplay === true};
}
export function writeVoicePreferences(patch: Partial<VoicePreferences>) {
  const next = {...voicePreferences(), ...patch};
  localStorage.setItem(KEY, JSON.stringify(next));
  dispatchEvent(new CustomEvent('helene:voice-preferences', {detail: next}));
  return next;
}
export function shortcutFromEvent(e: KeyboardEvent): string | null {
  if (['Control','Alt','Shift','Meta'].includes(e.key)) return null;
  const key = e.code === 'Space' ? 'Space' : e.code.startsWith('Key') ? e.code.slice(3) : e.code.startsWith('Digit') ? e.code.slice(5) : e.key;
  if (!/^(Space|Enter|F(?:[1-9]|1[0-9]|2[0-4])|[A-Z0-9])$/.test(key)) return null;
  const parts = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Meta', key].filter(Boolean);
  if (parts.length < 2) return null;
  return parts.join('+');
}
export function voiceHelp() {
  const ui = paperDialog('Говори, когда удобно'); ui.dialog.classList.add('voice-help');
  const prefs = voicePreferences();
  ui.body.innerHTML = `<p class="lrn-kicker">Голосовые сообщения</p><p class="lrn-lead">Удерживай <kbd>${esc(nativeState.bindings?.record||prefs.hotkey)}</kbd>, пока говоришь.</p><div class="voice-help-grid"><div><b>В окне Hélène</b><p>Отпусти — запись останется на паузе. Можно послушать, продолжить или отправить.</p></div><div><b>В другом приложении</b><p>Отпусти — голосовое сразу отправится в выбранный чат.</p></div></div><p>${nativeHint() ? esc(nativeHint())+' В окне также работают Shift и Esc.' : '<kbd>Shift</kbd> во время записи — оставить на паузе, без отправки. <kbd>Esc</kbd> — удалить запись.'}</p><p>Кнопка микрофона тоже включает запись и ставит её на паузу. Сочетание и автоозвучка меняются в настройках.</p>`;
  ui.footer.append(paperButton('Настройки голоса', () => { ui.dialog.close(); localStorage.setItem('settings.group','brain'); dispatchEvent(new CustomEvent('frame-go', {detail: 'settings'})); let attempts=0; const timer=setInterval(()=>{const target=document.querySelector('[data-voice-preferences]'); if(target){document.querySelector<HTMLButtonElement>('.settings-tab[data-group=brain]')?.click();target.scrollIntoView({block:'center',behavior:'smooth'});clearInterval(timer);}else if(++attempts>=30)clearInterval(timer);},100); }, true));
}
export function voicePreferencesFields(): HTMLElement {
  const box = document.createElement('section'); box.dataset.voicePreferences = ''; box.className = 'voice-preferences';
  const prefs = voicePreferences();
  box.innerHTML = `<h4>Запись и воспроизведение</h4><label class="field-label">Горячие клавиши <input class="field-input" data-hotkey readonly aria-label="Сочетание для записи" value="${esc(prefs.hotkey)}"></label><p class="field-hint">Нажми в поле и задай сочетание. В окне отпускание ставит запись на паузу; вне окна — отправляет.</p><label class="voice-option"><input type="checkbox" data-global ${prefs.global ? 'checked' : ''}> Записывать из других приложений</label><label class="voice-option"><input type="checkbox" data-autoplay ${prefs.autoplay ? 'checked' : ''}> Автоматически воспроизводить новые голосовые ответы</label><p class="field-hint" data-voice-native-status></p>`;
  const status = box.querySelector<HTMLElement>('[data-voice-native-status]')!;
  const describe = async () => {
    if (electron?.shell !== 'python') { status.textContent = 'В этом окне сочетание работает, пока окно активно.'; return; }
    try { const result = await shell<VoiceNativeState>('voice_shortcuts', {...voicePreferences()}); setVoiceNativeState(result); status.textContent = nativeHint() || result.error || (result.active ? 'Сочетание работает и в других приложениях. Изменения сохранены.' : 'Глобальное сочетание выключено.'); }
    catch (e) { status.textContent = String(e); }
  };
  const input = box.querySelector<HTMLInputElement>('[data-hotkey]')!;
  input.addEventListener('focus',()=>{if(electron?.shell==='python')void shell('voice_shortcuts',{...voicePreferences(),capture:true}).catch(()=>{});});
  input.addEventListener('blur',()=>void describe());
  input.addEventListener('keydown', e => { e.preventDefault(); e.stopPropagation(); const key = shortcutFromEvent(e); if (!key) return; input.value = key; writeVoicePreferences({hotkey:key}); input.blur(); });
  for (const [selector, name] of [['[data-global]','global'],['[data-autoplay]','autoplay']] as const) {
    box.querySelector<HTMLInputElement>(selector)!.addEventListener('change', e => { writeVoicePreferences({[name]:(e.target as HTMLInputElement).checked}); void describe(); });
  }
  void describe(); return box;
}
