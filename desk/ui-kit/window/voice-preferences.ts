import { t as uiText } from "../../lang/lang";
import { electron, shell } from './api';
import { paperButton, paperDialog } from '../paper-dialog';
import { esc } from './lib';

export interface VoicePreferences { hotkey: string; global: boolean; autoplay: boolean }
const KEY = 'helene.voice-ui';
export interface VoiceNativeState { active:boolean; error?:string; bindings?:Record<string,string> }
let nativeState: VoiceNativeState={active:false};
export function setVoiceNativeState(state:VoiceNativeState){nativeState=state;}
function nativeHint(){const b=nativeState.bindings;return b?.record ? uiText("ui.ui.kit.window.voice.preferences.2dde0576c1", {p0: String(b.record), p1: String(b.pause||uiText("ui.ui.kit.window.voice.preferences.8aa82fd2af")), p2: String(b.cancel||uiText("ui.ui.kit.window.voice.preferences.8aa82fd2af"))}) : '';}
export function voicePreferences(): VoicePreferences {
  let saved: Partial<VoicePreferences> = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch {}
  return {hotkey: typeof saved.hotkey === 'string' ? saved.hotkey : 'Ctrl+Alt+Space', global: saved.global !== false, autoplay: saved.autoplay !== false};
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
  const ui = paperDialog(uiText("ui.ui.kit.window.voice.preferences.bc8e451cb1")); ui.dialog.classList.add('voice-help');
  const prefs = voicePreferences();
  ui.body.innerHTML = uiText("ui.ui.kit.window.voice.preferences.597e5e1221", {p0: String(esc(nativeState.bindings?.record||prefs.hotkey)), p1: String(nativeHint() ? esc(nativeHint()) : uiText("ui.ui.kit.window.voice.preferences.490f1e858c"))});
  ui.footer.append(paperButton(uiText("ui.ui.kit.window.voice.preferences.856c9542f0"), () => { ui.dialog.close(); localStorage.setItem('settings.group','brain'); dispatchEvent(new CustomEvent('frame-go', {detail: 'settings'})); let attempts=0; const timer=setInterval(()=>{const target=document.querySelector('[data-voice-preferences]'); if(target){document.querySelector<HTMLButtonElement>('.settings-tab[data-group=brain]')?.click();target.scrollIntoView({block:'center',behavior:'smooth'});clearInterval(timer);}else if(++attempts>=30)clearInterval(timer);},100); }, true));
}
export function voicePreferencesFields(): HTMLElement {
  const box = document.createElement('section'); box.dataset.voicePreferences = ''; box.className = 'voice-preferences';
  const prefs = voicePreferences();
  box.innerHTML = uiText("ui.ui.kit.window.voice.preferences.8ef9b14661", {p0: String(esc(prefs.hotkey)), p1: String(prefs.global ? 'checked' : ''), p2: String(prefs.autoplay ? 'checked' : '')});
  const status = box.querySelector<HTMLElement>('[data-voice-native-status]')!;
  const describe = async () => {
    if (electron?.shell !== 'python') { status.textContent = uiText("ui.ui.kit.window.voice.preferences.1890d2e15b"); return; }
    try { const result = await shell<VoiceNativeState>('voice_shortcuts', {...voicePreferences()}); setVoiceNativeState(result); status.textContent = nativeHint() || result.error || (result.active ? uiText("ui.ui.kit.window.voice.preferences.faa7fb3308") : uiText("ui.ui.kit.window.voice.preferences.6f27dac8ee")); }
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
